"""Bootchain + driver bring-up validation tests (golden logs)."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from hilharness.validators.boot import (
    find_bootprobe,
    parse_with_python,
    validate_boot,
)

DATA = Path(__file__).parent / "data"
EXPECTED = ["display", "audio", "bt", "wifi", "usb", "camera"]


def _load(name: str) -> str:
    return (DATA / name).read_text(encoding="utf-8")


@pytest.mark.hil
@pytest.mark.sim
@pytest.mark.parametrize(
    "name,clean,missing,failed,timedout",
    [
        ("boot_good.log", True, [], [], []),
        ("boot_missing.log", False, ["wifi"], [], []),
        ("boot_fail.log", False, [], ["audio"], []),
        ("boot_hang.log", False, [], [], ["usb"]),
        ("boot_corrupt.log", False, ["bt", "wifi", "usb", "camera"], [], []),
    ],
)
def test_validate_boot_classification(name, clean, missing, failed, timedout):
    result = validate_boot(_load(name), EXPECTED)
    assert result.clean_bootchain is clean
    assert result.missing_drivers == missing
    assert result.failed_drivers == failed
    assert result.timeout_drivers == timedout
    assert result.malformed is False


@pytest.mark.hil
@pytest.mark.sim
def test_slow_boot_over_gate():
    result = validate_boot(_load("boot_slow.log"), EXPECTED)
    # Slow-but-healthy boot: drivers all come up but the gate is blown.
    assert result.clean_bootchain is True
    assert result.total_boot_ms > 6000.0


@pytest.mark.hil
@pytest.mark.sim
def test_good_boot_reports_stage_and_driver_timings():
    result = validate_boot(_load("boot_good.log"), EXPECTED)
    assert result.total_boot_ms > 0
    assert set(result.stage_ms) == {"bootloader", "kernel", "userspace"}
    assert all(v > 0 for v in result.stage_ms.values())
    assert set(result.measured_driver_ms) == set(EXPECTED)
    assert all(v > 0 for v in result.measured_driver_ms.values())


@pytest.mark.hil
@pytest.mark.sim
def test_c_and_python_parsers_agree():
    for name in ("boot_good.log", "boot_fail.log", "boot_hang.log"):
        txt = _load(name)
        c = validate_boot(txt, EXPECTED)
        py = validate_boot(txt, EXPECTED, force_python=True)
        assert c.total_boot_ms == pytest.approx(py.total_boot_ms, abs=0.01)
        assert c.missing_drivers == py.missing_drivers
        assert c.failed_drivers == py.failed_drivers
        assert c.timeout_drivers == py.timeout_drivers


@pytest.mark.hil
@pytest.mark.sim
def test_python_parser_tolerates_garbage():
    data = parse_with_python("\x00\x01 not a boot log at all \x7f\x7f")
    assert data["malformed"] is True
    assert data["total_boot_ms"] == 0.0


@pytest.mark.hil
@pytest.mark.sim
@pytest.mark.skipif(find_bootprobe() is None, reason="bootprobe binary not built")
def test_c_bootprobe_binary_emits_json():
    import subprocess

    probe = find_bootprobe()
    proc = subprocess.run(
        [str(probe)], input=_load("boot_good.log"), capture_output=True, text=True
    )
    assert proc.returncode == 0
    payload = json.loads(proc.stdout)
    assert payload["malformed"] is False
    assert set(payload["drivers_ms"]) == set(EXPECTED)


@pytest.mark.hil
@pytest.mark.sim
@pytest.mark.skipif(find_bootprobe() is None, reason="bootprobe binary not built")
def test_c_bootprobe_nonzero_exit_on_malformed():
    import subprocess

    probe = find_bootprobe()
    proc = subprocess.run(
        [str(probe)], input="garbage\nmore garbage\n", capture_output=True, text=True
    )
    assert proc.returncode != 0
    assert json.loads(proc.stdout)["malformed"] is True
