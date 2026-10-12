"""Release-readiness report: gates, regressions and a GO / NO-GO verdict."""

from __future__ import annotations

import json
from datetime import UTC, datetime
from pathlib import Path

from ..config import RigConfig
from ..metrics.store import MetricsStore
from .regress import check_regressions

# key, human label, unit, higher_is_better, gate attribute on RigConfig.gates, op
METRIC_SPECS = [
    ("boot_ms_p95", "Boot time p95", "ms", False, "max_boot_ms", "<="),
    ("driver_init_ms_p95", "Driver init p95", "ms", False, "max_driver_init_ms", "<="),
    ("can_frame_loss_pct", "CAN frame loss (max)", "%", False, "max_frame_loss_pct", "<="),
    (
        "fault_detection_pct",
        "Fault detection rate",
        "%",
        True,
        "min_fault_detection_pct",
        ">=",
    ),
    ("cpu_pct_max", "Target CPU peak", "%", False, None, "<="),
    ("rss_mb_max", "Target RSS peak", "MiB", False, None, "<="),
    ("suite_pass_rate", "Suite pass rate", "%", True, None, ">="),
    ("clean_boot_rate", "Clean boot rate", "%", True, None, ">="),
]

# Fixed thresholds where there is no config gate.
FIXED_GATES = {
    "suite_pass_rate": 95.0,
    "clean_boot_rate": 100.0,
}

DIRECTIONS = {spec[0]: spec[3] for spec in METRIC_SPECS}


def _gate_for(key: str, cfg: RigConfig) -> tuple[float, str] | None:
    if key in FIXED_GATES:
        spec = next(s for s in METRIC_SPECS if s[0] == key)
        return FIXED_GATES[key], spec[5]
    attr = next(s[4] for s in METRIC_SPECS if s[0] == key)
    if attr is None:
        return None
    spec = next(s for s in METRIC_SPECS if s[0] == key)
    return float(getattr(cfg.gates, attr)), spec[5]


def collect_current(
    store: MetricsStore, run_id: int | None, results_json: Path | None = None
) -> tuple[dict[str, float], dict]:
    run_id = run_id if run_id is not None else store.last_run_id()

    def last(metric: str, agg: str = "max") -> float:
        st = store.stats(metric, run_id)
        return float(st.get(agg, 0.0))

    summary = store.cycle_summary(run_id) if run_id is not None else {}
    non_fault = [c for c in summary.get("items", []) if not c.get("fault_spec")]
    clean_rate = (
        100.0 * sum(1 for c in non_fault if c["clean_boot"]) / len(non_fault) if non_fault else 0.0
    )

    suite_pass_rate = 0.0
    suite_info = {}
    if results_json and Path(results_json).exists():
        suite_info = json.loads(Path(results_json).read_text())
        suite_pass_rate = float(suite_info.get("pass_rate", 0.0))
    else:
        suite_vals = store.values("suite_pass_rate", run_id)
        if suite_vals:
            suite_pass_rate = suite_vals[-1]

    values = {
        "boot_ms_p95": float(summary.get("boot_p95", 0.0)),
        "driver_init_ms_p95": last("driver_init_ms", "p95"),
        "can_frame_loss_pct": last("can_frame_loss_pct", "max"),
        "fault_detection_pct": last("fault_detection_pct", "max"),
        "cpu_pct_max": last("cpu_pct_max", "max"),
        "rss_mb_max": last("rss_mb_max", "max"),
        "suite_pass_rate": suite_pass_rate,
        "clean_boot_rate": clean_rate,
    }
    details = {
        "run_id": run_id,
        "cycle_summary": summary,
        "suite": suite_info,
        "driver_init_by_driver": store.tag_values("driver_init_ms", "driver", run_id),
    }
    return values, details


def build_report(
    cfg: RigConfig,
    store_path: str = "results/hil.db",
    baseline_path: str = "reports/baseline.json",
    results_json: str = "results/hil_results.json",
    report_md: str = "reports/release_readiness.md",
    report_json: str = "reports/release_readiness.json",
    regression_pct: float = 10.0,
    run_id: int | None = None,
) -> tuple[dict, int]:
    store = MetricsStore(store_path)
    try:
        values, details = collect_current(store, run_id, Path(results_json))
    finally:
        store.close()

    baseline = {}
    if Path(baseline_path).exists():
        baseline = json.loads(Path(baseline_path).read_text()).get("metrics", {})

    rows = []
    gates_ok = True
    for key, label, unit, higher_better, _attr, _op in METRIC_SPECS:
        measured = values.get(key, 0.0)
        gate = _gate_for(key, cfg)
        if gate is None:
            gate_thr, op, passed = None, "", True
        else:
            gate_thr, op = gate
            passed = measured <= gate_thr if op == "<=" else measured >= gate_thr
            gates_ok = gates_ok and passed
        base = baseline.get(key, {})
        base_val = base.get("value") if isinstance(base, dict) else base
        delta_pct = (
            (measured - base_val) / abs(base_val) * 100.0 if base_val not in (None, 0) else None
        )
        rows.append(
            {
                "key": key,
                "label": label,
                "unit": unit,
                "measured": round(measured, 4),
                "higher_is_better": higher_better,
                "gate_threshold": gate_thr,
                "gate_op": op,
                "gate_pass": passed,
                "baseline": base_val,
                "delta_pct": round(delta_pct, 2) if delta_pct is not None else None,
            }
        )

    offenders = check_regressions(
        {k: v for k, v in values.items()},
        baseline,
        directions=DIRECTIONS,
        threshold_pct=regression_pct,
    )
    overall_go = gates_ok and not offenders
    verdict = "GO" if overall_go else "NO-GO"

    report = {
        "generated_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "rig": cfg.name,
        "target": cfg.target,
        "run_id": details.get("run_id"),
        "verdict": verdict,
        "gates_ok": gates_ok,
        "regression_pct": regression_pct,
        "regressions": offenders,
        "metrics": rows,
        "details": details,
    }

    _write_markdown(report, report_md)
    Path(report_json).parent.mkdir(parents=True, exist_ok=True)
    Path(report_json).write_text(json.dumps(report, indent=2), encoding="utf-8")
    return report, (0 if overall_go else 1)


def _write_markdown(report: dict, path: str) -> None:
    lines: list[str] = []
    lines.append("# HIL Release-Readiness Report")
    lines.append("")
    lines.append(f"- Generated: {report['generated_at']}")
    lines.append(f"- Rig: `{report['rig']}` (target `{report['target']}`)")
    lines.append(f"- Run ID: {report['run_id']}")
    lines.append(f"- Verdict: **{report['verdict']}**")
    lines.append("")
    lines.append("## Gates")
    lines.append("")
    lines.append("| Metric | Measured | Gate | Result | Baseline | Delta |")
    lines.append("|---|---:|---:|:--:|---:|---:|")
    for r in report["metrics"]:
        if r["gate_threshold"] is None:
            gate = "informational"
            result = "-"
        else:
            gate = f"{r['gate_op']} {r['gate_threshold']:g} {r['unit']}"
            result = "PASS" if r["gate_pass"] else "FAIL"
        base = "-" if r["baseline"] is None else f"{r['baseline']:.2f}"
        delta = "-" if r["delta_pct"] is None else f"{r['delta_pct']:+.1f}%"
        lines.append(
            f"| {r['label']} | {r['measured']:.2f} {r['unit']} | {gate} | {result} | "
            f"{base} | {delta} |"
        )
    lines.append("")
    lines.append(f"### Regression check (threshold {report['regression_pct']:g}%)")
    lines.append("")
    if report["regressions"]:
        lines.append("| Metric | Baseline | Current | Delta |")
        lines.append("|---|---:|---:|---:|")
        for o in report["regressions"]:
            lines.append(
                f"| {o['metric']} | {o['baseline']:.3f} | {o['current']:.3f} | "
                f"{o['delta_pct']:+.2f}% |"
            )
    else:
        lines.append("No regressions detected against the baseline.")
    lines.append("")

    cs = report["details"].get("cycle_summary", {})
    if cs:
        lines.append("## Soak summary")
        lines.append("")
        lines.append(f"- Cycles: {cs.get('cycles', 0)}")
        lines.append(f"- Clean boots: {cs.get('clean', 0)} ({cs.get('clean_rate', 0)}%)")
        lines.append(
            f"- Boot p50/p95/max: {cs.get('boot_p50', 0):.1f} / "
            f"{cs.get('boot_p95', 0):.1f} / {cs.get('boot_max', 0):.1f} ms"
        )
        lines.append("")

    did = report["details"].get("driver_init_by_driver", {})
    if did:
        lines.append("## Driver init latency (p95)")
        lines.append("")
        lines.append("| Driver | p50 ms | p95 ms | max ms | n |")
        lines.append("|---|---:|---:|---:|---:|")
        for name, st in sorted(did.items()):
            lines.append(
                f"| {name} | {st.get('p50', 0):.1f} | {st.get('p95', 0):.1f} | "
                f"{st.get('max', 0):.1f} | {st.get('count', 0)} |"
            )
        lines.append("")

    lines.append("## Verdict")
    lines.append("")
    lines.append(f"**{report['verdict']}**")
    lines.append("")
    Path(path).parent.mkdir(parents=True, exist_ok=True)
    Path(path).write_text("\n".join(lines), encoding="utf-8")


def build_baseline(
    cfg: RigConfig,
    store_path: str = "results/hil.db",
    results_json: str = "results/hil_results.json",
    baseline_path: str = "reports/baseline.json",
    run_id: int | None = None,
) -> dict:
    store = MetricsStore(store_path)
    try:
        values, details = collect_current(store, run_id, Path(results_json))
    finally:
        store.close()
    baseline = {
        "created_at": datetime.now(UTC).isoformat(timespec="seconds"),
        "run_id": details.get("run_id"),
        "rig": cfg.name,
        "metrics": {
            k: {"value": v, "higher_is_better": DIRECTIONS.get(k, False)} for k, v in values.items()
        },
    }
    Path(baseline_path).parent.mkdir(parents=True, exist_ok=True)
    Path(baseline_path).write_text(json.dumps(baseline, indent=2), encoding="utf-8")
    return baseline
