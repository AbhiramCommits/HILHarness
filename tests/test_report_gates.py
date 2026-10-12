"""Unit tests for report gate logic and regression detection."""

from __future__ import annotations

import json

import pytest

from hilharness.config import RigConfig
from hilharness.metrics.store import MetricsStore
from hilharness.report.regress import check_regressions
from hilharness.report.release import build_baseline, build_report


def _make_store(path, boot_ms: float) -> None:
    store = MetricsStore(path)
    run = store.new_run("soak")
    for i in range(1, 21):
        store.record_cycle(run, i, True, boot_ms, 5.0, "")
    store.record_measurement(run, "driver_init_ms", 120.0, "ms", tags={"driver": "display"})
    store.record_measurement(run, "can_frame_loss_pct", 0.0, "%")
    store.record_measurement(run, "fault_detection_pct", 100.0, "%")
    store.record_measurement(run, "cpu_pct_max", 150.0, "%")
    store.record_measurement(run, "rss_mb_max", 45.0, "MiB")
    store.finish_run(run)
    store.close()


def _paths(tmp_path, boot_ms, results_pass=100.0):
    store = tmp_path / "hil.db"
    _make_store(store, boot_ms)
    results = tmp_path / "hil_results.json"
    results.write_text(json.dumps({"pass_rate": results_pass}))
    return {
        "store_path": str(store),
        "baseline_path": str(tmp_path / "baseline.json"),
        "results_json": str(results),
        "report_md": str(tmp_path / "r.md"),
        "report_json": str(tmp_path / "r.json"),
    }


@pytest.mark.hil
@pytest.mark.sim
def test_report_go_when_all_gates_pass(tmp_path):
    p = _paths(tmp_path, 5000.0)
    report, code = build_report(RigConfig(), **p)
    assert report["verdict"] == "GO"
    assert code == 0
    assert report["gates_ok"] is True


@pytest.mark.hil
@pytest.mark.sim
def test_report_nogo_when_gate_fails(tmp_path):
    p = _paths(tmp_path, 20000.0)  # above max_boot_ms
    report, code = build_report(RigConfig(), **p)
    assert report["verdict"] == "NO-GO"
    assert code == 1
    boot = next(m for m in report["metrics"] if m["key"] == "boot_ms_p95")
    assert boot["gate_pass"] is False


@pytest.mark.hil
@pytest.mark.sim
def test_report_regression_makes_nogo(tmp_path):
    p = _paths(tmp_path, 5000.0)
    # baseline says p95 was 1000 ms -> current 5000 is a regression
    baseline = {
        "metrics": {
            "boot_ms_p95": {"value": 1000.0, "higher_is_better": False},
            "can_frame_loss_pct": {"value": 0.0, "higher_is_better": False},
        }
    }
    with open(p["baseline_path"], "w") as fh:
        json.dump(baseline, fh)
    report, code = build_report(RigConfig(), regression_pct=10.0, **p)
    assert report["verdict"] == "NO-GO"
    assert any(o["metric"] == "boot_ms_p95" for o in report["regressions"])
    assert code == 1


@pytest.mark.hil
@pytest.mark.sim
def test_baseline_update_roundtrip(tmp_path):
    p = _paths(tmp_path, 4000.0)
    base = build_baseline(
        RigConfig(),
        store_path=p["store_path"],
        baseline_path=p["baseline_path"],
        results_json=p["results_json"],
    )
    assert base["metrics"]["boot_ms_p95"]["value"] == pytest.approx(4000.0)
    report, code = build_report(RigConfig(), **p)
    assert code == 0
    assert report["regressions"] == []


@pytest.mark.hil
@pytest.mark.sim
def test_regress_directions():
    current = {"boot_ms_p95": 120.0, "fault_detection_pct": 70.0}
    baseline = {
        "boot_ms_p95": {"value": 100.0},
        "fault_detection_pct": {"value": 100.0},
    }
    offenders = check_regressions(
        current, baseline, directions={"fault_detection_pct": True}, threshold_pct=10.0
    )
    kinds = {o["metric"] for o in offenders}
    assert kinds == {"boot_ms_p95", "fault_detection_pct"}
