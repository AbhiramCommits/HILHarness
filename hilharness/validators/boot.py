"""Bootchain and driver bring-up validation.

``validate_boot`` runs the compiled C ``bootprobe`` on a console transcript and
falls back to an equivalent pure-Python parser when the binary is unavailable.
"""

from __future__ import annotations

import json
import os
import re
import subprocess
from dataclasses import dataclass, field
from pathlib import Path

_TS_RE = re.compile(r"^\[\s*([0-9]+(?:\.[0-9]+)?)\]")
_STAGE_RE = re.compile(r"^hil-stage\s+(\S+)\s+(begin|ready)")
_DRIVER_RE = re.compile(r"^hil-driver\s+(\S+)\s+probe\s+(begin|FAIL|timeout|[0-9]+(?:\.[0-9]+)?)")
_FAULT_WORDS = ("fail", "timeout", "oops", "panic")


@dataclass
class BootResult:
    clean_bootchain: bool
    total_boot_ms: float
    stage_ms: dict[str, float]
    driver_init_ms: dict[str, float | None]
    missing_drivers: list[str] = field(default_factory=list)
    failed_drivers: list[str] = field(default_factory=list)
    timeout_drivers: list[str] = field(default_factory=list)
    faults: list[str] = field(default_factory=list)
    malformed: bool = False
    source: str = "c"
    line_count: int = 0

    @property
    def measured_driver_ms(self) -> dict[str, float]:
        return {k: v for k, v in self.driver_init_ms.items() if v is not None}

    def to_dict(self) -> dict:
        return {
            "clean_bootchain": self.clean_bootchain,
            "total_boot_ms": round(self.total_boot_ms, 3),
            "stage_ms": {k: round(v, 3) for k, v in self.stage_ms.items()},
            "driver_init_ms": self.driver_init_ms,
            "missing_drivers": self.missing_drivers,
            "failed_drivers": self.failed_drivers,
            "timeout_drivers": self.timeout_drivers,
            "faults": self.faults,
            "malformed": self.malformed,
            "source": self.source,
            "line_count": self.line_count,
        }


def find_bootprobe() -> Path | None:
    env = os.environ.get("HIL_BOOTPROBE")
    if env and Path(env).exists():
        return Path(env)
    root = Path(__file__).resolve().parents[2]
    candidate = root / "native" / "bootprobe"
    return candidate if candidate.exists() else None


def parse_with_python(transcript: str) -> dict:
    """Mirror of bootprobe.c, used when the C binary is absent."""
    drivers: dict[str, float] = {}
    failed: list[str] = []
    timedout: list[str] = []
    faults: list[str] = []
    t: dict[str, float | None] = {"bootloader": None, "kernel": None, "userspace": None}
    has_ready = False
    t_ready = 0.0
    first_ts = None
    last_ts = 0.0
    n_ts = 0
    line_count = 0

    for raw in transcript.splitlines():
        line = raw.rstrip("\n")
        line_count += 1
        low = line.lower()
        if any(w in low for w in _FAULT_WORDS):
            faults.append(line)
        m = _TS_RE.match(line)
        if not m:
            continue
        ts = float(m.group(1))
        n_ts += 1
        if first_ts is None:
            first_ts = ts
        last_ts = ts
        msg = line[m.end() :].lstrip(" \t")
        sm = _STAGE_RE.match(msg)
        if sm:
            stage, verb = sm.group(1), sm.group(2)
            if verb == "begin" and stage in t and t[stage] is None:
                t[stage] = ts
            elif verb == "ready" and stage == "userspace":
                has_ready = True
                t_ready = ts
            continue
        dm = _DRIVER_RE.match(msg)
        if dm:
            name, verb = dm.group(1), dm.group(2)
            if verb == "FAIL":
                failed.append(name)
            elif verb == "timeout":
                timedout.append(name)
            elif verb != "begin":
                drivers[name] = float(verb)

    end = t_ready if has_ready else last_ts
    start = t["bootloader"] if t["bootloader"] is not None else (first_ts or 0.0)
    total = max(0.0, (end - start) * 1000.0)

    def stage_ms(a: float | None, b: float | None) -> float:
        if a is None or b is None:
            return 0.0
        return max(0.0, (b - a) * 1000.0)

    stages = {
        "bootloader": stage_ms(t["bootloader"], t["kernel"]),
        "kernel": stage_ms(t["kernel"], t["userspace"]),
        "userspace": stage_ms(t["userspace"], end) if t["userspace"] is not None else 0.0,
    }
    malformed = (n_ts == 0) or all(v is None for v in t.values())
    return {
        "total_boot_ms": total,
        "stages_ms": stages,
        "drivers_ms": drivers,
        "failed_drivers": failed,
        "timeout_drivers": timedout,
        "fault_lines": faults,
        "has_bootloader": t["bootloader"] is not None,
        "has_kernel": t["kernel"] is not None,
        "has_userspace": t["userspace"] is not None,
        "has_ready": has_ready,
        "line_count": line_count,
        "timestamp_count": n_ts,
        "malformed": malformed,
    }


def _run_bootprobe(transcript: str, probe: Path) -> dict:
    proc = subprocess.run(
        [str(probe)],
        input=transcript,
        capture_output=True,
        text=True,
        timeout=30,
        check=False,
    )
    try:
        return json.loads(proc.stdout)
    except json.JSONDecodeError as exc:  # pragma: no cover - probe always emits JSON
        raise RuntimeError(f"bootprobe produced invalid JSON: {exc}\n{proc.stdout}") from exc


def _to_result(data: dict, expected_drivers: list[str], source: str) -> BootResult:
    seen = set(data.get("drivers_ms", {}))
    seen |= set(data.get("failed_drivers", []))
    seen |= set(data.get("timeout_drivers", []))
    missing = [d for d in expected_drivers if d not in seen]

    driver_init: dict[str, float | None] = {}
    for d in expected_drivers:
        driver_init[d] = data.get("drivers_ms", {}).get(d)

    failed = list(data.get("failed_drivers", []))
    timedout = list(data.get("timeout_drivers", []))
    malformed = bool(data.get("malformed", False))
    faults = list(data.get("fault_lines", []))
    clean = not malformed and not missing and not failed and not timedout
    # Any oops/panic is fatal even if drivers look fine.
    if any(("oops" in f.lower() or "panic" in f.lower()) for f in faults):
        clean = False

    return BootResult(
        clean_bootchain=clean,
        total_boot_ms=float(data.get("total_boot_ms", 0.0)),
        stage_ms={k: float(v) for k, v in data.get("stages_ms", {}).items()},
        driver_init_ms=driver_init,
        missing_drivers=missing,
        failed_drivers=failed,
        timeout_drivers=timedout,
        faults=faults,
        malformed=malformed,
        source=source,
        line_count=int(data.get("line_count", 0)),
    )


def validate_boot(
    transcript: str,
    expected_drivers: list[str],
    probe: Path | None = None,
    force_python: bool = False,
) -> BootResult:
    """Validate a boot transcript; uses C bootprobe when available."""
    probe = probe if probe is not None else find_bootprobe()
    if probe is not None and not force_python:
        data = _run_bootprobe(transcript, probe)
        return _to_result(data, expected_drivers, source="c")
    data = parse_with_python(transcript)
    return _to_result(data, expected_drivers, source="python")
