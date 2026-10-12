"""Soak runner: N full cycles of flash -> power-cycle -> boot -> CAN -> IO.

Every cycle persists real measurements into the SQLite store. A subset of
cycles injects boot and CAN faults to measure detection.

Used by ``hil soak`` and ``scripts/soak.py``.
"""

from __future__ import annotations

import json
import statistics
import subprocess
import time
from pathlib import Path

from simtarget.can_node import CanFaults, PyCanNode

from .config import RigConfig
from .drivers.canbus import CanBus
from .metrics.collect import ProcessSampler
from .metrics.prom import write_textfile
from .metrics.store import MetricsStore
from .rig import Rig
from .validators.boot import validate_boot
from .validators.can import detect_faults, fault_kinds

BOOT_FAULT_POOL = [
    ("missing:wifi", "missing"),
    ("fail:audio", "failed"),
    ("hang:usb", "timeout"),
    ("slow_boot", "slow"),
    ("missing:camera", "missing"),
]

CAN_FAULT_POOL = [
    (CanFaults(drop_rate=0.5), "FrameLoss"),
    (CanFaults(stale_signal="VehicleSpeed"), "StaleSignal"),
    (CanFaults(bad_dlc=4), "BadDLC"),
    (CanFaults(out_of_range="VehicleSpeed"), "SignalOutOfRange"),
    (CanFaults(silence_ms=800), "BusSilence"),
]


def _git_sha() -> str:
    try:
        return subprocess.run(
            ["git", "rev-parse", "--short", "HEAD"],
            capture_output=True,
            text=True,
            check=False,
        ).stdout.strip()
    except Exception:  # pragma: no cover
        return ""


def _jitter_ms(frames) -> float:
    ts = sorted(f.timestamp for f in frames if f.arbitration_id == 0x100)
    if len(ts) < 2:
        return 0.0
    periods = [(b - a) * 1000.0 for a, b in zip(ts, ts[1:], strict=False)]
    mean = statistics.fmean(periods)
    return max(abs(p - mean) for p in periods)


def _io_checks(rig: Rig, cfg: RigConfig) -> bool:
    from .drivers.console import ConsoleShell
    from .drivers.netctl import NetCtl

    ok = True
    client = NetCtl(cfg.net)
    try:
        client.connect()
        ok = ok and client.ping()
        ok = ok and "uptime_s" in client.sysinfo()
        client.set("display.brightness", 90)
        ok = ok and client.get("display.brightness") == "90"
    except Exception:
        ok = False
    finally:
        client.close()

    try:
        shell = ConsoleShell(rig.console())
        out = shell.run("echo soak-io", timeout=5)
        ok = ok and "soak-io" in out
    except Exception:
        ok = False
    return ok


def _clean_cycle(
    base: Rig,
    cfg: RigConfig,
    store: MetricsStore,
    run_id: int,
    i: int,
    can_load_s: float,
    stress_s: float,
) -> None:
    flash = base.flasher().flash(cfg.image_path)
    base.power_off()
    time.sleep(cfg.power.off_ms / 1000.0)
    base.power_on()
    pid = base.target_pid()
    sampler = ProcessSampler(pid).start() if pid else None

    reader = base.console(cycle=i)
    reader.capture = ""
    try:
        reader.read_until(r"hil-stage userspace ready", timeout=12.0)
        reader.read_until(r"hil\$", timeout=3.0)
    except TimeoutError:
        reader.drain(quiet_for=0.5, max_total=2.0)
    boot = validate_boot(reader.capture, cfg.expected_drivers)

    # CAN load test
    can = CanBus(cfg.can)
    node = PyCanNode(can.channel, cfg.can.dbc_path)
    node.start()
    try:
        time.sleep(0.2)
        frames = can.stream(can_load_s)
    finally:
        node.stop()
    loss = can.sequence_loss_stats(frames)
    jitter = _jitter_ms(frames)
    can_findings = detect_faults(frames, db=can.dbc)
    can.close()

    io_ok = _io_checks(base, cfg)

    # STRESS the target and let the sampler capture CPU/RSS peaks.
    from .drivers.netctl import NetCtl

    client = NetCtl(cfg.net)
    try:
        client.connect()
        client.stress("cpu", 2)
        client.stress("mem", 30)
    except Exception:
        io_ok = False
    finally:
        client.close()
    time.sleep(stress_s)
    sample = sampler.stop() if sampler else {"cpu_pct_max": 0.0, "rss_mb_max": 0.0}

    if boot.clean_bootchain:
        store.record_measurement(run_id, "boot_ms", boot.total_boot_ms, "ms", cycle_index=i)
        for name, ms in boot.measured_driver_ms.items():
            store.record_measurement(
                run_id, "driver_init_ms", ms, "ms", tags={"driver": name}, cycle_index=i
            )
    store.record_measurement(run_id, "flash_ms", flash.duration_s * 1000.0, "ms", cycle_index=i)
    store.record_measurement(run_id, "can_frame_loss_pct", loss["loss_pct"], "%", cycle_index=i)
    store.record_measurement(run_id, "can_period_jitter_ms", jitter, "ms", cycle_index=i)
    store.record_measurement(
        run_id, "can_unexpected_findings", float(len(can_findings)), "count", cycle_index=i
    )
    store.record_measurement(run_id, "cpu_pct_max", sample["cpu_pct_max"], "%", cycle_index=i)
    store.record_measurement(run_id, "rss_mb_max", sample["rss_mb_max"], "MiB", cycle_index=i)
    store.record_measurement(run_id, "io_pass", 1.0 if io_ok else 0.0, "bool", cycle_index=i)
    store.record_cycle(
        run_id, i, boot.clean_bootchain, boot.total_boot_ms, flash.duration_s * 1000.0, ""
    )


def _boot_fault_cycle(
    cfg: RigConfig, store: MetricsStore, run_id: int, i: int, spec: str, kind: str
) -> bool:
    rig = Rig(cfg, faults=spec, use_net=False)
    try:
        rig.power_on()
        reader = rig.console(cycle=i)
        reader.capture = ""
        try:
            reader.read_until(r"hil-stage userspace ready", timeout=14.0)
        except TimeoutError:
            reader.drain(quiet_for=0.5, max_total=2.0)
        boot = validate_boot(reader.capture, cfg.expected_drivers)
        if kind == "missing":
            detected = any(d in boot.missing_drivers for d in _spec_drivers(spec))
        elif kind == "failed":
            detected = bool(boot.failed_drivers)
        elif kind == "timeout":
            detected = bool(boot.timeout_drivers)
        elif kind == "slow":
            detected = boot.total_boot_ms > 6500.0
        else:
            detected = not boot.clean_bootchain
        store.record_measurement(
            run_id,
            "boot_fault_detected",
            1.0 if detected else 0.0,
            "bool",
            tags={"spec": spec, "kind": kind},
            cycle_index=i,
        )
        store.record_cycle(run_id, i, boot.clean_bootchain, boot.total_boot_ms, None, spec)
        return detected
    finally:
        rig.close()


def _spec_drivers(spec: str) -> list[str]:
    out = []
    for token in spec.replace(",", " ").split():
        if ":" in token:
            out.append(token.split(":", 1)[1])
    return out


def _can_fault_cycle(
    cfg: RigConfig, store: MetricsStore, run_id: int, i: int, faults: CanFaults, kind: str
) -> bool:
    can = CanBus(cfg.can)
    node = PyCanNode(can.channel, cfg.can.dbc_path, faults=faults)
    node.start()
    try:
        time.sleep(0.2)
        frames = can.stream(1.2)
    finally:
        node.stop()
    kinds = fault_kinds(detect_faults(frames, db=can.dbc))
    detected = kind in kinds
    can.close()
    store.record_measurement(
        run_id,
        "can_fault_detected",
        1.0 if detected else 0.0,
        "bool",
        tags={"kind": kind},
        cycle_index=i,
    )
    store.record_cycle(run_id, i, True, None, None, f"can:{kind}")
    return detected


def run_soak(
    cfg: RigConfig,
    cycles: int,
    store_path: str = "results/hil.db",
    out_json: str = "results/soak_summary.json",
    can_load_s: float = 0.8,
    stress_s: float = 0.7,
) -> dict:
    cfg.ensure_dirs()
    store = MetricsStore(store_path)
    run_id = store.new_run("soak", notes=f"cycles={cycles}", git_sha=_git_sha())
    base = Rig(cfg)
    faults_injected = 0
    faults_detected = 0
    try:
        for i in range(1, cycles + 1):
            if i % 10 == 0:
                spec, kind = BOOT_FAULT_POOL[((i // 10) - 1) % len(BOOT_FAULT_POOL)]
                faults_injected += 1
                faults_detected += int(_boot_fault_cycle(cfg, store, run_id, i, spec, kind))
            elif i % 10 == 5:
                faults, kind = CAN_FAULT_POOL[(i // 10) % len(CAN_FAULT_POOL)]
                faults_injected += 1
                faults_detected += int(_can_fault_cycle(cfg, store, run_id, i, faults, kind))
            else:
                _clean_cycle(base, cfg, store, run_id, i, can_load_s, stress_s)
    finally:
        base.close()

    detection_pct = 100.0 * faults_detected / faults_injected if faults_injected else 0.0
    store.record_measurement(run_id, "fault_detection_pct", detection_pct, "%")

    results_json = Path("results/hil_results.json")
    if results_json.exists():
        suite = json.loads(results_json.read_text())
        store.record_measurement(run_id, "suite_pass_rate", float(suite.get("pass_rate", 0.0)), "%")

    summary = store.cycle_summary(run_id)
    summary.update(
        {
            "run_id": run_id,
            "faults_injected": faults_injected,
            "faults_detected": faults_detected,
            "fault_detection_pct": detection_pct,
            "boot_ms_p95": store.stats("boot_ms", run_id)["p95"],
            "boot_ms_p50": store.stats("boot_ms", run_id)["p50"],
            "boot_ms_max": store.stats("boot_ms", run_id)["max"],
            "driver_init_ms_p95": store.stats("driver_init_ms", run_id)["p95"],
            "can_frame_loss_pct_max": store.stats("can_frame_loss_pct", run_id)["max"],
            "can_period_jitter_ms_max": store.stats("can_period_jitter_ms", run_id)["max"],
            "cpu_pct_max": store.stats("cpu_pct_max", run_id)["max"],
            "rss_mb_max": store.stats("rss_mb_max", run_id)["max"],
            "io_pass_rate": 100.0
            * sum(store.values("io_pass", run_id))
            / max(1, len(store.values("io_pass", run_id))),
            "driver_init_by_driver": store.tag_values("driver_init_ms", "driver", run_id),
        }
    )
    store.finish_run(run_id)
    store.close()

    Path(out_json).parent.mkdir(parents=True, exist_ok=True)
    Path(out_json).write_text(json.dumps(summary, indent=2), encoding="utf-8")

    gauges = {
        "hil_boot_ms_p50": summary["boot_ms_p50"],
        "hil_boot_ms_p95": summary["boot_ms_p95"],
        "hil_driver_init_ms_p95": summary["driver_init_ms_p95"],
        "hil_can_frame_loss_pct": summary["can_frame_loss_pct_max"],
        "hil_cpu_pct_max": summary["cpu_pct_max"],
        "hil_rss_mb_max": summary["rss_mb_max"],
        "hil_suite_pass_rate": store_suite_rate(summary),
        "hil_clean_boot_rate": summary["clean_rate"],
        "hil_fault_detection_pct": detection_pct,
    }
    for driver, st in summary.get("driver_init_by_driver", {}).items():
        gauges[f'hil_driver_init_ms_p95{{driver="{driver}"}}'] = st.get("p95", 0.0)
    write_textfile(gauges)
    return summary


def store_suite_rate(summary: dict) -> float:
    results_json = Path("results/hil_results.json")
    if results_json.exists():
        return float(json.loads(results_json.read_text()).get("pass_rate", 0.0))
    return 0.0
