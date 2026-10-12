"""End-to-end bootchain suite: flash -> power-cycle -> validate."""

from __future__ import annotations

import pytest

from tests.conftest import boot_once


@pytest.mark.hil
@pytest.mark.sim
def test_boot_chain_is_clean(booted):
    result, _reader = booted
    assert result.clean_bootchain
    assert result.missing_drivers == []
    assert result.failed_drivers == []
    assert result.timeout_drivers == []


@pytest.mark.hil
@pytest.mark.sim
def test_all_expected_drivers_probed(booted, cfg):
    result, _reader = booted
    assert set(result.driver_init_ms) == set(cfg.expected_drivers)
    assert set(result.measured_driver_ms) == set(cfg.expected_drivers)


@pytest.mark.hil
@pytest.mark.sim
def test_boot_time_within_gate(booted, cfg):
    result, _reader = booted
    assert 0 < result.total_boot_ms <= cfg.gates.max_boot_ms


@pytest.mark.hil
@pytest.mark.sim
def test_each_driver_init_within_gate(booted, cfg):
    result, _reader = booted
    for name, ms in result.measured_driver_ms.items():
        assert ms <= cfg.gates.max_driver_init_ms, f"{name} took {ms} ms"


@pytest.mark.hil
@pytest.mark.sim
def test_repeated_power_cycles_are_clean(rig, cfg):
    for _ in range(3):
        result, _reader = boot_once(rig, cfg)
        assert result.clean_bootchain


@pytest.mark.hil
@pytest.mark.sim
def test_injected_missing_driver_is_detected(rig_factory, cfg):
    r = rig_factory("missing:wifi")
    result, _reader = boot_once(r, cfg)
    assert "wifi" in result.missing_drivers
    assert result.clean_bootchain is False


@pytest.mark.hil
@pytest.mark.sim
def test_injected_failed_probe_is_detected(rig_factory, cfg):
    r = rig_factory("fail:audio")
    result, _reader = boot_once(r, cfg)
    assert "audio" in result.failed_drivers
    assert result.clean_bootchain is False


@pytest.mark.hil
@pytest.mark.sim
def test_injected_driver_hang_is_detected(rig_factory, cfg):
    r = rig_factory("hang:usb")
    result, _reader = boot_once(r, cfg)
    assert "usb" in result.timeout_drivers
    assert result.clean_bootchain is False


@pytest.mark.hil
@pytest.mark.sim
def test_injected_slow_boot_is_slower(rig_factory, cfg):
    r = rig_factory("slow_boot")
    result, _reader = boot_once(r, cfg)
    assert result.total_boot_ms > 6500.0
