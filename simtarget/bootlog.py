"""Generate a realistic kernel-style boot log for the simulated target.

The log uses tty-style millisecond timestamps (``[    1.234567]``) and carries
structured markers the C ``bootprobe`` and the Python fallback parser both
understand:

* ``hil-stage <name> begin``     - start of a boot stage
* ``hil-stage userspace ready``  - final marker; total boot time ends here
* ``hil-driver <name> probe <ms> ms``  - successful driver probe
* ``hil-driver <name> probe FAIL``     - failed driver probe
* ``hil-driver <name> probe timeout``  - hung driver probe
"""

from __future__ import annotations

import random
from dataclasses import dataclass

from . import ecu_profile
from .ecu_profile import Faults


@dataclass
class BootLog:
    lines: list[str]
    expected_drivers: list[str]
    stage_times_ms: dict[str, float]
    driver_times_ms: dict[str, float]
    total_boot_ms: float


def _ts(ms: float) -> str:
    return f"[{ms / 1000.0:12.6f}]"


def generate(
    faults: Faults | None = None,
    jitter_pct: float = 4.0,
    seed: int | None = None,
    all_drivers: list[str] | None = None,
) -> BootLog:
    faults = faults or Faults()
    rng = random.Random(seed)
    lines: list[str] = []

    # --- bootloader -----------------------------------------------------
    t = 0.0
    lines.append(f"{_ts(t)} hil-stage bootloader begin")
    lines.append(f"{_ts(t)} U-Boot 2023.07 (Jun 12 2023 - 09:41:02 +0000)")
    lines.append(f"{_ts(t)} SoC: Broadcom BCM2711, DRAM: 4 GiB")
    bl = ecu_profile.jitter(ecu_profile.STAGES[0].duration_ms, jitter_pct, rng)
    t += bl
    lines.append(f"{_ts(t)} hil-stage kernel begin")
    lines.append(f"{_ts(t)} Starting kernel ...")

    # --- kernel ---------------------------------------------------------
    kernel = ecu_profile.jitter(ecu_profile.STAGES[1].duration_ms, jitter_pct, rng)
    if faults.slow_boot:
        kernel *= 2.1
    # Sprinkle realistic dmesg lines across the kernel window.
    kernel_msgs = [
        "Linux version 5.15.0-rpi (domk@build) #1 SMP PREEMPT",
        "Memory: 3841024K/4050944K available",
        "rcu: Hierarchical RCU implementation.",
        "cpufreq: cpufreq-dt: registered",
        "mmc0: SDHCI controller on fe300000.mmcnr [fe300000.mmcnr] using DMA",
        "usbcore: registered new interface driver usbfs",
        "vchiq: vchiq_init_state: slot_zero = (ptrval)",
        "bcm2835-v4l2: V4L2 device registered as video0",
    ]
    for i, msg in enumerate(kernel_msgs):
        lines.append(f"{_ts(t + kernel * (i + 1) / (len(kernel_msgs) + 1))} {msg}")
    t += kernel
    lines.append(f"{_ts(t)} hil-stage userspace begin")
    lines.append(f"{_ts(t)} systemd[1]: Reached target Basic System.")

    # --- userspace: driver bring-up ------------------------------------
    t += ecu_profile.USERSPACE_OVERHEAD_MS
    drivers = all_drivers or [d.name for d in ecu_profile.DRIVERS]
    stage_times_snapshot: dict[str, float] = {}
    driver_times: dict[str, float] = {}

    for name in drivers:
        if name in faults.missing_drivers:
            lines.append(f"{_ts(t)} hil: {name}: no matching driver; skipping")
            continue
        base = next((d.probe_ms for d in ecu_profile.DRIVERS if d.name == name), 200.0)
        probe = ecu_profile.jitter(base, jitter_pct, rng)
        if name in faults.extra_slow_drivers:
            probe *= 4.0
        if name in faults.hanging_drivers:
            lines.append(f"{_ts(t)} hil-driver {name} probe begin")
            t += max(probe, 3000.0)
            lines.append(f"{_ts(t)} hil-driver {name} probe timeout")
            lines.append(f"{_ts(t)} {name}: probe timed out after 3000 ms")
            continue
        if name in faults.failing_drivers:
            lines.append(f"{_ts(t)} hil-driver {name} probe begin")
            t += probe
            lines.append(f"{_ts(t)} hil-driver {name} probe FAIL")
            lines.append(f"{_ts(t)} {name}: probe failed (-ENODEV)")
            lines.append(f"{_ts(t)} FAIL: {name} driver did not initialise")
            continue
        t += probe
        driver_times[name] = round(probe, 3)
        lines.append(f"{_ts(t)} hil-driver {name} probe {probe:.3f} ms")
        lines.append(f"{_ts(t)} {name}: subsystem ready")

    t += ecu_profile.POST_PROBE_MS
    lines.append(f"{_ts(t)} hil-stage userspace ready")
    lines.append(f"{_ts(t)} systemd[1]: Reached target Multi-User System.")
    lines.append(f"{_ts(t)} hil: boot complete in {t:.0f} ms")

    if faults.log_corrupt:
        # Truncate and garble the tail to simulate a dropped UART.
        cut = int(len(lines) * 0.6)
        lines = lines[:cut] + ["[   \x00\x01\x02garbled\x7f" + lines[-1][:8]]

    stage_times_snapshot = {
        "bootloader": round(bl, 3),
        "kernel": round(kernel, 3),
        "userspace": round(t - bl - kernel, 3),
    }
    return BootLog(
        lines=lines,
        expected_drivers=list(drivers),
        stage_times_ms=stage_times_snapshot,
        driver_times_ms=driver_times,
        total_boot_ms=round(t, 3),
    )
