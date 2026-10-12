"""Power control drivers.

``GpioRelay`` drives a real relay on a Raspberry Pi GPIO pin; ``SimRelay``
starts and kills the simulated target process instead. Both expose the same
``off() / on() / cycle()`` interface.
"""

from __future__ import annotations

import os
import signal
import subprocess
import sys
import time
from abc import ABC, abstractmethod
from pathlib import Path

from ..config import PowerConfig


class PowerRelay(ABC):
    """Abstract power relay for the DUT."""

    def __init__(self, off_ms: int = 2000) -> None:
        self.off_ms = off_ms

    @abstractmethod
    def off(self) -> None:
        """Remove power from the DUT."""

    @abstractmethod
    def on(self) -> None:
        """Apply power to the DUT."""

    def cycle(self) -> None:
        """Power off, wait ``off_ms``, power back on."""
        self.off()
        time.sleep(self.off_ms / 1000.0)
        self.on()


class SimRelay(PowerRelay):
    """Kills/starts the :mod:`simtarget.target` process.

    The PTY slave fd allocated by the harness is handed to the child as its
    stdin/stdout/stderr so its output is a genuine pty transcript.
    """

    def __init__(
        self,
        off_ms: int = 2000,
        slave_fd: int | None = None,
        sandbox: Path | None = None,
        faults: str = "",
        seed: int = -1,
        jitter: float = 4.0,
        net_port: int = 0,
        net_host: str = "127.0.0.1",
        line_delay_ms: float = 6.0,
        repo_root: Path | None = None,
    ) -> None:
        super().__init__(off_ms)
        self.slave_fd = slave_fd
        self.sandbox = sandbox
        self.faults = faults
        self.seed = seed
        self.jitter = jitter
        self.net_port = net_port
        self.net_host = net_host
        self.line_delay_ms = line_delay_ms
        self.repo_root = repo_root or Path.cwd()
        self.proc: subprocess.Popen | None = None

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.poll() is None

    def on(self) -> None:
        if self.running:
            return
        cmd = [
            sys.executable,
            "-m",
            "simtarget.target",
            "--faults",
            self.faults,
            "--seed",
            str(self.seed),
            "--jitter",
            str(self.jitter),
            "--line-delay-ms",
            str(self.line_delay_ms),
            "--net-host",
            self.net_host,
        ]
        if self.net_port:
            cmd += ["--net-port", str(self.net_port)]
        env = dict(os.environ)
        env["PYTHONPATH"] = str(self.repo_root) + os.pathsep + env.get("PYTHONPATH", "")
        # stdin/stdout/stderr all point at the pty slave.
        std = self.slave_fd if self.slave_fd is not None else subprocess.DEVNULL
        self.proc = subprocess.Popen(
            cmd,
            stdin=std,
            stdout=std,
            stderr=std,
            cwd=str(self.repo_root),
            env=env,
            start_new_session=True,
            close_fds=True,
        )

    def off(self) -> None:
        if self.proc is None:
            return
        if self.proc.poll() is None:
            try:
                self.proc.terminate()
                self.proc.wait(timeout=2.0)
            except subprocess.TimeoutExpired:
                self.proc.kill()
                self.proc.wait(timeout=2.0)
        self.proc = None

    def kill_hard(self) -> None:
        """Best-effort SIGKILL for teardown."""
        if self.proc and self.proc.poll() is None:
            try:
                os.killpg(os.getpgid(self.proc.pid), signal.SIGKILL)
            except Exception:
                self.proc.kill()
        self.proc = None


class GpioRelay(PowerRelay):
    """Real relay wired to a Raspberry Pi GPIO pin (via gpiozero)."""

    def __init__(self, cfg: PowerConfig) -> None:
        super().__init__(cfg.off_ms)
        self.pin = cfg.gpio_pin
        self.active_high = cfg.active_high
        self._relay = None

    def _ensure(self):
        if self._relay is None:
            from gpiozero import OutputDevice  # lazy: keeps non-Pi hosts importable

            self._relay = OutputDevice(self.pin, active_high=self.active_high, initial_value=False)
        return self._relay

    def off(self) -> None:
        self._ensure().off()

    def on(self) -> None:
        self._ensure().on()


def build_power(cfg: PowerConfig, **kwargs) -> PowerRelay:
    """Factory: pick the relay implementation from config."""
    if cfg.driver == "gpio":
        return GpioRelay(cfg)
    return SimRelay(off_ms=cfg.off_ms, **kwargs)
