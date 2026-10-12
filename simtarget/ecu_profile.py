"""Boot profile for the simulated infotainment-style target.

Timings here are the *simulated* hardware profile. They are deliberately
plausible for an embedded Linux infotainment head unit and are perturbed with
configurable jitter so regression detection has something real to measure.
"""

from __future__ import annotations

import random
from dataclasses import dataclass, field


@dataclass
class Stage:
    name: str
    duration_ms: float


@dataclass
class DriverProbe:
    name: str
    probe_ms: float
    subsystem: str = "platform"


# Ordered boot stages (durations are approximate; actual log timestamps are
# derived by accumulating them).
STAGES: list[Stage] = [
    Stage("bootloader", 850.0),
    Stage("kernel", 2250.0),
]

# Expected driver bring-up probes, in probe order.
DRIVERS: list[DriverProbe] = [
    DriverProbe("display", 140.0, "drm"),
    DriverProbe("audio", 210.0, "alsa"),
    DriverProbe("bt", 330.0, "bluetooth"),
    DriverProbe("wifi", 540.0, "cfg80211"),
    DriverProbe("usb", 95.0, "xhci"),
    DriverProbe("camera", 410.0, "v4l2"),
]

# Userspace hand-off overhead before the first driver probe.
USERSPACE_OVERHEAD_MS = 260.0
# Overhead after the last driver probe before "reached target".
POST_PROBE_MS = 180.0


@dataclass
class Faults:
    """Injected faults, parsed from env vars / CLI flags."""

    failing_drivers: set[str] = field(default_factory=set)
    hanging_drivers: set[str] = field(default_factory=set)
    missing_drivers: set[str] = field(default_factory=set)
    slow_boot: bool = False
    log_corrupt: bool = False
    extra_slow_drivers: set[str] = field(default_factory=set)

    def describe(self) -> str:
        parts = []
        if self.failing_drivers:
            parts.append(f"fail={','.join(sorted(self.failing_drivers))}")
        if self.hanging_drivers:
            parts.append(f"hang={','.join(sorted(self.hanging_drivers))}")
        if self.missing_drivers:
            parts.append(f"missing={','.join(sorted(self.missing_drivers))}")
        if self.slow_boot:
            parts.append("slow_boot")
        if self.log_corrupt:
            parts.append("log_corrupt")
        return " ".join(parts) or "none"

    @classmethod
    def from_spec(cls, spec: str) -> Faults:
        """Parse a spec like ``fail:audio hang:usb missing:wifi slow_boot``."""
        f = cls()
        for token in (spec or "").replace(",", " ").split():
            if token == "slow_boot":
                f.slow_boot = True
            elif token == "log_corrupt":
                f.log_corrupt = True
            elif ":" in token:
                kind, name = token.split(":", 1)
                if kind == "fail":
                    f.failing_drivers.add(name)
                elif kind == "hang":
                    f.hanging_drivers.add(name)
                    f.failing_drivers.add(name)
                elif kind == "missing":
                    f.missing_drivers.add(name)
                elif kind == "slow":
                    f.extra_slow_drivers.add(name)
        return f


def jitter(value: float, pct: float, rng: random.Random) -> float:
    """Apply symmetric jitter of ``pct`` percent to ``value``."""
    delta = value * (pct / 100.0)
    return max(1.0, value + rng.uniform(-delta, delta))
