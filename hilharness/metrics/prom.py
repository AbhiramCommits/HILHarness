"""Prometheus textfile exporter.

Writes ``results/hil_metrics.prom`` in the Prometheus text exposition format
(gauges), with an optional push to a Pushgateway.
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

HELP = {
    "hil_boot_ms_p50": "Boot time 50th percentile (ms) across the latest soak run.",
    "hil_boot_ms_p95": "Boot time 95th percentile (ms) across the latest soak run.",
    "hil_driver_init_ms_p95": "Driver init latency 95th percentile (ms).",
    "hil_can_frame_loss_pct": "Measured CAN frame loss (%).",
    "hil_cpu_pct_max": "Peak target CPU utilisation (%) under load.",
    "hil_rss_mb_max": "Peak target RSS (MiB) under load.",
    "hil_suite_pass_rate": "HIL suite pass rate (%).",
    "hil_clean_boot_rate": "Clean-boot rate (%) across soak cycles.",
    "hil_fault_detection_pct": "Injected CAN fault detection rate (%).",
}
TYPE = "gauge"


def gauge_lines(gauges: dict[str, float]) -> str:
    """Render gauges. Keys may carry labels, e.g. ``metric{driver="audio"}``.

    HELP/TYPE are emitted once per base metric name.
    """
    lines: list[str] = []
    seen: set[str] = set()
    for key, value in gauges.items():
        base = key.split("{", 1)[0]
        if base not in seen:
            if base in HELP:
                lines.append(f"# HELP {base} {HELP[base]}")
            lines.append(f"# TYPE {base} {TYPE}")
            seen.add(base)
        lines.append(f"{key} {float(value):.6g}")
    return "\n".join(lines) + "\n"


def write_textfile(gauges: dict[str, float], path: str | Path = "results/hil_metrics.prom") -> str:
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    text = gauge_lines(gauges)
    Path(path).write_text(text, encoding="utf-8")
    return text


def push_to_gateway(
    gauges: dict[str, float], url: str, job: str = "hilharness", instance: str = "ci"
) -> None:
    """POST gauges to a Prometheus Pushgateway (best effort)."""
    endpoint = url.rstrip("/") + f"/metrics/job/{job}/instance/{instance}"
    data = gauge_lines(gauges).encode("utf-8")
    req = urllib.request.Request(
        endpoint, data=data, method="POST", headers={"Content-Type": "text/plain"}
    )
    with urllib.request.urlopen(req, timeout=10) as resp:  # noqa: S310
        resp.read()
