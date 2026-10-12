"""CAN signal assertions and typed fault detection.

All decoding is done through cantools; findings are typed so callers can count
detection rates per fault class.
"""

from __future__ import annotations

import statistics
from collections.abc import Iterable
from dataclasses import dataclass

from ..drivers.canbus import CanFrame

# Signals expected to change continuously; used for staleness detection.
DEFAULT_VARYING = {"VehicleSpeed", "EngineRpm", "CabinTemp", "AmbientLight"}


@dataclass
class Finding:
    kind: str  # SignalOutOfRange | StaleSignal | BadDLC | BusSilence | FrameLoss
    message: str
    detail: str
    frame_id: int | None = None
    signal: str | None = None
    value: float | None = None

    def to_dict(self) -> dict:
        return {
            "kind": self.kind,
            "message": self.message,
            "detail": self.detail,
            "frame_id": self.frame_id,
            "signal": self.signal,
            "value": self.value,
        }


# ---------------------------------------------------------------------------
# assertions
# ---------------------------------------------------------------------------
def assert_signal(msg: dict, signal: str, expected, tol: float = 0.0) -> None:
    """Assert a decoded signal equals ``expected`` within ``tol``."""
    assert signal in msg, f"signal {signal!r} not in {sorted(msg)}"
    got = msg[signal]
    if isinstance(expected, (int, float)) and not isinstance(expected, bool):
        assert abs(float(got) - float(expected)) <= tol, f"{signal}={got} != {expected} (tol {tol})"
    else:
        assert got == expected, f"{signal}={got!r} != {expected!r}"


def assert_period(
    frames: Iterable[CanFrame], msg_id: int, expected_ms: float, jitter_ms: float = 5.0
) -> dict:
    """Assert inter-frame period of ``msg_id`` is close to ``expected_ms``."""
    ts = sorted(f.timestamp for f in frames if f.arbitration_id == msg_id)
    assert len(ts) >= 2, f"need >=2 frames for 0x{msg_id:X}, got {len(ts)}"
    periods = [(b - a) * 1000.0 for a, b in zip(ts, ts[1:], strict=False)]
    mean = statistics.fmean(periods)
    assert (
        abs(mean - expected_ms) <= jitter_ms
    ), f"0x{msg_id:X} mean period {mean:.2f}ms not within {jitter_ms}ms of {expected_ms}ms"
    return {
        "msg_id": msg_id,
        "count": len(ts),
        "mean_ms": round(mean, 3),
        "p95_ms": round(_pct(periods, 0.95), 3),
        "max_ms": round(max(periods), 3),
        "min_ms": round(min(periods), 3),
    }


def _pct(values: list[float], q: float) -> float:
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = q * (len(ordered) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo)


# ---------------------------------------------------------------------------
# fault detection
# ---------------------------------------------------------------------------
def _seq_loss(counters: list[int], modulus: int = 16) -> tuple[int, int]:
    lost = 0
    for prev, cur in zip(counters, counters[1:], strict=False):
        gap = (cur - prev) % modulus
        lost += (modulus - 1) if gap == 0 else (gap - 1)
    return lost, len(counters)


def detect_faults(
    stream: list[CanFrame],
    db=None,
    varying_signals: set[str] | None = None,
    silence_threshold_ms: float = 150.0,
    stale_frames: int = 6,
) -> list[Finding]:
    """Return typed findings for a captured stream."""
    findings: list[Finding] = []
    varying = varying_signals if varying_signals is not None else DEFAULT_VARYING

    # BadDLC -- compare to the DBC length when available, else expect 8 bytes.
    seen_baddlc: set[int] = set()
    for f in stream:
        expected_len = None
        if db is not None:
            try:
                expected_len = db.get_message_by_frame_id(f.arbitration_id).length
            except KeyError:
                expected_len = None
        if expected_len is None:
            expected_len = 8
        if f.dlc != expected_len and f.arbitration_id not in seen_baddlc:
            seen_baddlc.add(f.arbitration_id)
            findings.append(
                Finding(
                    kind="BadDLC",
                    message=f.message_name or f"0x{f.arbitration_id:X}",
                    detail=f"dlc={f.dlc} expected {expected_len}",
                    frame_id=f.arbitration_id,
                )
            )

    # SignalOutOfRange -- requires the DBC for min/max.
    if db is not None:
        seen_oor: set[tuple[int, str]] = set()
        for f in stream:
            if not f.decoded:
                continue
            try:
                msg = db.get_message_by_frame_id(f.arbitration_id)
            except KeyError:
                continue
            for sig in msg.signals:
                if sig.name not in f.decoded:
                    continue
                val = f.decoded[sig.name]
                if not isinstance(val, (int, float)) or isinstance(val, bool):
                    continue
                lo, hi = sig.minimum, sig.maximum
                if sig.minimum is None or sig.maximum is None:
                    continue
                if val < lo or val > hi:
                    key = (f.arbitration_id, sig.name)
                    if key in seen_oor:
                        continue
                    seen_oor.add(key)
                    findings.append(
                        Finding(
                            kind="SignalOutOfRange",
                            message=msg.name,
                            detail=f"{sig.name}={val} outside [{lo},{hi}]",
                            frame_id=f.arbitration_id,
                            signal=sig.name,
                            value=float(val),
                        )
                    )

    # StaleSignal -- a "varying" signal that is frozen for the whole window.
    by_msg: dict[str, list[CanFrame]] = {}
    for f in stream:
        if f.message_name:
            by_msg.setdefault(f.message_name, []).append(f)
    for fname, frames in by_msg.items():
        for sig_name in varying:
            if not all(sig_name in fr.decoded for fr in frames) or len(frames) < stale_frames:
                continue
            vals = [fr.decoded[sig_name] for fr in frames]
            if max(vals) == min(vals):
                findings.append(
                    Finding(
                        kind="StaleSignal",
                        message=fname,
                        detail=f"{sig_name} frozen at {vals[0]} over {len(vals)} frames",
                        signal=sig_name,
                        value=float(vals[0]),
                    )
                )

    # BusSilence -- gaps between any consecutive frames.
    ordered = sorted(stream, key=lambda f: f.timestamp)
    for a, b in zip(ordered, ordered[1:], strict=False):
        gap_ms = (b.timestamp - a.timestamp) * 1000.0
        if gap_ms > silence_threshold_ms:
            findings.append(
                Finding(
                    kind="BusSilence",
                    message="bus",
                    detail=f"gap of {gap_ms:.1f} ms between frames",
                    value=round(gap_ms, 3),
                )
            )
            break  # one silence finding is enough

    # FrameLoss -- rolling counter (VEH_SPEED / MsgCounter).
    counters = [int(f.decoded["MsgCounter"]) for f in stream if "MsgCounter" in f.decoded]
    if len(counters) >= 2:
        lost, delivered = _seq_loss(counters)
        if lost > 0:
            findings.append(
                Finding(
                    kind="FrameLoss",
                    message="VEH_SPEED",
                    detail=f"{lost} lost of {lost + delivered} (counter gaps)",
                    value=round(100.0 * lost / (lost + delivered), 4),
                )
            )

    return findings


def fault_kinds(findings: list[Finding]) -> set[str]:
    return {f.kind for f in findings}
