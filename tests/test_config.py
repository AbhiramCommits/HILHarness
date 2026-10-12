"""Unit tests for rig configuration validation."""

from __future__ import annotations

import pytest
from pydantic import ValidationError

from hilharness.config import CanConfig, Gates, RigConfig


@pytest.mark.hil
@pytest.mark.sim
def test_defaults():
    cfg = RigConfig()
    assert cfg.target == "sim"
    assert cfg.power.driver == "sim"
    assert cfg.console.driver == "pty"
    assert cfg.can.interface == "vcan0"
    assert cfg.gates.max_boot_ms == 15000


@pytest.mark.hil
@pytest.mark.sim
def test_load_sim_config():
    cfg = RigConfig.from_yaml("configs/rig_sim.yaml")
    assert cfg.target == "sim"
    assert "display" in cfg.expected_drivers
    assert cfg.can.backend == "auto"
    assert cfg.is_sim is True


@pytest.mark.hil
@pytest.mark.sim
def test_load_hw_config():
    cfg = RigConfig.from_yaml("configs/rig_hw_raspi.yaml")
    assert cfg.target == "hw"
    assert cfg.power.driver == "gpio"
    assert cfg.power.gpio_pin == 17
    assert cfg.power.active_high is False
    assert cfg.console.driver == "serial"
    assert cfg.console.device == "/dev/ttyUSB0"
    assert cfg.can.interface == "can0"
    assert cfg.is_sim is False


@pytest.mark.hil
@pytest.mark.sim
def test_gate_percentage_validation():
    with pytest.raises(ValidationError):
        Gates(max_frame_loss_pct=150)
    with pytest.raises(ValidationError):
        Gates(min_fault_detection_pct=-1)


@pytest.mark.hil
@pytest.mark.sim
def test_empty_can_interface_rejected():
    with pytest.raises(ValidationError):
        CanConfig(interface="")


@pytest.mark.hil
@pytest.mark.sim
def test_derived_paths():
    cfg = RigConfig(results_dir="results")
    assert cfg.results_path.name == "results"
    assert cfg.sandbox.name == "dut_root"
    assert cfg.transcripts_dir.name == "transcripts"
