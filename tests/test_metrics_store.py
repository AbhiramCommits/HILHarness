"""Unit tests for the SQLite metrics store."""

from __future__ import annotations

import pytest

from hilharness.metrics.store import MetricsStore


@pytest.fixture
def store(tmp_path):
    s = MetricsStore(tmp_path / "hil.db")
    yield s
    s.close()


@pytest.mark.hil
@pytest.mark.sim
def test_runs_and_measurements(store):
    run = store.new_run("soak", notes="unit")
    for v in range(1, 11):
        store.record_measurement(run, "boot_ms", float(v), "ms")
    store.finish_run(run)
    assert store.last_run_id("soak") == run
    assert store.last_run_id() == run
    stats = store.stats("boot_ms", run)
    assert stats["count"] == 10
    assert stats["min"] == 1.0
    assert stats["max"] == 10.0
    assert stats["mean"] == pytest.approx(5.5)
    assert stats["p50"] == pytest.approx(5.5, abs=0.6)
    assert stats["p95"] == pytest.approx(9.55, abs=0.5)


@pytest.mark.hil
@pytest.mark.sim
def test_percentiles_known_values(store):
    run = store.new_run("soak")
    for v in (10, 20, 30, 40, 50):
        store.record_measurement(run, "x", float(v))
    stats = store.stats("x", run)
    assert stats["p50"] == pytest.approx(30.0)
    assert stats["p99"] == pytest.approx(49.6, abs=0.5)


@pytest.mark.hil
@pytest.mark.sim
def test_cycle_summary_and_tags(store):
    run = store.new_run("soak")
    store.record_cycle(run, 1, True, 1000.0, 5.0, "")
    store.record_cycle(run, 2, True, 2000.0, 6.0, "")
    store.record_cycle(run, 3, False, 3000.0, 7.0, "missing:wifi")
    store.record_measurement(run, "driver_init_ms", 100.0, "ms", tags={"driver": "display"})
    store.record_measurement(run, "driver_init_ms", 200.0, "ms", tags={"driver": "audio"})
    summary = store.cycle_summary(run)
    assert summary["cycles"] == 3
    assert summary["clean"] == 2
    grouped = store.tag_values("driver_init_ms", "driver", run)
    assert set(grouped) == {"display", "audio"}


@pytest.mark.hil
@pytest.mark.sim
def test_empty_metric_stats(store):
    stats = store.stats("nope")
    assert stats["count"] == 0
    assert stats["p95"] == 0.0
