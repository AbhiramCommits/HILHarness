"""The rig: wires config to concrete drivers and owns the DUT lifecycle.

One :class:`Rig` instance per session. It allocates the PTY pair for the
simulated target (or nothing for hardware), builds the power relay, and lazily
hands out console/CAN/net drivers.
"""

from __future__ import annotations

import os
import pty
import time
import tty

from .config import RigConfig
from .drivers.canbus import CanBus
from .drivers.console import ConsoleReader, ConsoleShell
from .drivers.flasher import Flasher, HwFlasher
from .drivers.netctl import NetCtl
from .drivers.power import PowerRelay, build_power


class Rig:
    def __init__(self, cfg: RigConfig, faults: str | None = None, use_net: bool = True) -> None:
        self.cfg = cfg
        cfg.ensure_dirs()
        self.faults = faults if faults is not None else os.environ.get("HIL_FAULT", "")
        self.use_net = use_net
        self.master_fd: int | None = None
        self.slave_fd: int | None = None

        if cfg.is_sim:
            self.master_fd, self.slave_fd = pty.openpty()
            tty.setraw(self.slave_fd)
            tty.setraw(self.master_fd)

        self.relay: PowerRelay = build_power(
            cfg.power,
            slave_fd=self.slave_fd,
            sandbox=cfg.sandbox,
            faults=self.faults,
            net_port=cfg.net.port if (cfg.is_sim and use_net) else 0,
            net_host=cfg.net.host,
        )
        self._console: ConsoleReader | None = None
        self._can: CanBus | None = None
        self._net: NetCtl | None = None

    # -- flasher ---------------------------------------------------------
    def flasher(self, hw_command: str | None = None) -> Flasher:
        if self.cfg.target == "hw" and hw_command:
            return HwFlasher(
                self.cfg.sandbox, command=hw_command, expected_sha256=self.cfg.image_sha256
            )
        return Flasher(self.cfg.sandbox, expected_sha256=self.cfg.image_sha256)

    # -- console ---------------------------------------------------------
    def console(self, cycle: int | None = None) -> ConsoleReader:
        path = None
        if cycle is not None:
            path = self.cfg.transcripts_dir / f"cycle_{cycle:04d}.log"
        if self._console is None:
            self._console = ConsoleReader(
                self.cfg.console, master_fd=self.master_fd, transcript_path=path
            )
        elif path is not None:
            self._console.transcript_path = path
        return self._console

    def shell(self, cycle: int | None = None) -> ConsoleShell:
        return ConsoleShell(self.console(cycle=cycle))

    # -- CAN / net -------------------------------------------------------
    def can(self) -> CanBus:
        if self._can is None:
            self._can = CanBus(self.cfg.can)
        return self._can

    def net(self) -> NetCtl:
        if self._net is None:
            self._net = NetCtl(self.cfg.net)
        return self._net

    # -- lifecycle -------------------------------------------------------
    def power_cycle(self) -> None:
        self.relay.cycle()

    def power_on(self) -> None:
        self.relay.on()

    def power_off(self) -> None:
        self.relay.off()

    def target_pid(self) -> int | None:
        proc = getattr(self.relay, "proc", None)
        return proc.pid if proc is not None and proc.poll() is None else None

    def wait_alive(self, timeout: float = 5.0) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if getattr(self.relay, "running", True):
                return True
            time.sleep(0.05)
        return getattr(self.relay, "running", True)

    def close(self) -> None:
        if self._can is not None:
            try:
                self._can.close()
            except Exception:
                pass
        if self._net is not None:
            try:
                self._net.close()
            except Exception:
                pass
        # Kill the simulated target, then close the pty.
        if hasattr(self.relay, "kill_hard"):
            try:
                self.relay.kill_hard()  # type: ignore[attr-defined]
            except Exception:
                pass
        else:
            try:
                self.relay.off()
            except Exception:
                pass
        if self._console is not None:
            self._console.close()
        for fd in (self.slave_fd, self.master_fd):
            if fd is not None:
                try:
                    os.close(fd)
                except OSError:
                    pass
        self.slave_fd = None
        self.master_fd = None
