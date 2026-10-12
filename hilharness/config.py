"""Typed rig configuration loaded from YAML.

A single :class:`RigConfig` describes the entire rig. Switching from the
simulated target (``target: sim``) to real hardware (``target: hw``) is a
config swap: the driver names change (``sim`` -> ``gpio``, ``pty`` -> ``serial``)
but the harness code path is identical.
"""

from __future__ import annotations

from pathlib import Path
from typing import Literal

import yaml
from pydantic import BaseModel, Field, field_validator


class PowerConfig(BaseModel):
    driver: Literal["sim", "gpio"] = "sim"
    gpio_pin: int = 17
    off_ms: int = 2000
    active_high: bool = True


class ConsoleConfig(BaseModel):
    driver: Literal["pty", "serial"] = "pty"
    device: str = "/dev/ttyUSB0"
    baud: int = 115200
    read_timeout_s: float = 5.0


class CanConfig(BaseModel):
    interface: str = "vcan0"
    bitrate: int = 500000
    dbc_path: str = "configs/infotainment.dbc"
    backend: Literal["auto", "socketcan", "virtual"] = "auto"

    @field_validator("interface")
    @classmethod
    def _non_empty(cls, v: str) -> str:
        if not v:
            raise ValueError("can.interface must not be empty")
        return v


class NetConfig(BaseModel):
    host: str = "127.0.0.1"
    port: int = 9000
    timeout_s: float = 5.0
    retries: int = 3
    backoff_s: float = 0.2


class Gates(BaseModel):
    max_boot_ms: float = 15000.0
    max_driver_init_ms: float = 1500.0
    max_frame_loss_pct: float = 1.0
    min_fault_detection_pct: float = 80.0

    @field_validator("max_frame_loss_pct", "min_fault_detection_pct")
    @classmethod
    def _pct_range(cls, v: float) -> float:
        if not 0.0 <= v <= 100.0:
            raise ValueError("percentage gates must be within 0..100")
        return v


class RigConfig(BaseModel):
    """Top-level rig configuration."""

    name: str = "unconfigured"
    target: Literal["sim", "hw"] = "sim"
    power: PowerConfig = Field(default_factory=PowerConfig)
    console: ConsoleConfig = Field(default_factory=ConsoleConfig)
    can: CanConfig = Field(default_factory=CanConfig)
    net: NetConfig = Field(default_factory=NetConfig)
    image_path: str = "configs/rootfs.img"
    image_sha256: str | None = None
    expected_drivers: list[str] = Field(
        default_factory=lambda: ["display", "audio", "bt", "wifi", "usb", "camera"]
    )
    gates: Gates = Field(default_factory=Gates)
    results_dir: str = "results"

    @classmethod
    def from_yaml(cls, path: str | Path) -> RigConfig:
        p = Path(path)
        with p.open("r", encoding="utf-8") as fh:
            data = yaml.safe_load(fh) or {}
        cfg = cls.model_validate(data)
        # Paths in the config are relative to the repository root (cwd), but if a
        # config is provided from elsewhere we still resolve relative to cwd which
        # is how the CLI is invoked.
        return cfg

    @property
    def results_path(self) -> Path:
        return Path(self.results_dir)

    @property
    def sandbox(self) -> Path:
        return self.results_path / "dut_root"

    @property
    def transcripts_dir(self) -> Path:
        return self.results_path / "transcripts"

    @property
    def can_logs_dir(self) -> Path:
        return self.results_path / "can_logs"

    @property
    def is_sim(self) -> bool:
        return self.target == "sim"

    def ensure_dirs(self) -> None:
        for d in (
            self.results_path,
            self.sandbox,
            self.transcripts_dir,
            self.can_logs_dir,
        ):
            d.mkdir(parents=True, exist_ok=True)
