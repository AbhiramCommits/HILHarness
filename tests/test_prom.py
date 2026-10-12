"""Unit tests for the Prometheus textfile exporter."""

from __future__ import annotations

import pytest

from hilharness.metrics.prom import gauge_lines, write_textfile


@pytest.mark.hil
@pytest.mark.sim
def test_gauge_lines_basic():
    text = gauge_lines({"hil_boot_ms_p95": 1234.5})
    assert "# TYPE hil_boot_ms_p95 gauge" in text
    assert "hil_boot_ms_p95 1234.5" in text
    assert text.endswith("\n")


@pytest.mark.hil
@pytest.mark.sim
def test_gauge_lines_with_labels_and_single_help():
    text = gauge_lines(
        {
            'hil_driver_init_ms_p95{driver="display"}': 140.0,
            'hil_driver_init_ms_p95{driver="audio"}': 210.0,
        }
    )
    # HELP/TYPE emitted exactly once for the base metric.
    assert text.count("# TYPE hil_driver_init_ms_p95 gauge") == 1
    assert 'hil_driver_init_ms_p95{driver="display"} 140' in text
    assert 'hil_driver_init_ms_p95{driver="audio"} 210' in text


@pytest.mark.hil
@pytest.mark.sim
def test_write_textfile(tmp_path):
    out = tmp_path / "m.prom"
    write_textfile({"hil_suite_pass_rate": 100.0}, out)
    assert out.exists()
    assert "hil_suite_pass_rate 100" in out.read_text()
