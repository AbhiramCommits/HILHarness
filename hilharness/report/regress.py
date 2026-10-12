"""Regression detection against a stored baseline."""

from __future__ import annotations


def check_regressions(
    current: dict[str, float],
    baseline: dict[str, dict],
    directions: dict[str, bool] | None = None,
    threshold_pct: float = 10.0,
) -> list[dict]:
    """Flag metrics that got worse than baseline by more than ``threshold_pct``.

    ``baseline`` maps metric -> {"value": float, ...}. ``directions`` maps metric
    -> higher_is_better (missing means lower is better).
    """
    directions = directions or {}
    offenders: list[dict] = []
    for key, cur in current.items():
        base = baseline.get(key)
        if base is None:
            continue
        base_val = float(base.get("value", base) if isinstance(base, dict) else base)
        if base_val == 0:
            continue
        higher_better = directions.get(key, False)
        delta_pct = (float(cur) - base_val) / abs(base_val) * 100.0
        worse = (-delta_pct > threshold_pct) if higher_better else (delta_pct > threshold_pct)
        if worse:
            offenders.append(
                {
                    "metric": key,
                    "baseline": base_val,
                    "current": float(cur),
                    "delta_pct": round(delta_pct, 2),
                    "higher_is_better": higher_better,
                }
            )
    return offenders
