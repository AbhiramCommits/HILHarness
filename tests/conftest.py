"""Shared pytest fixtures for the HIL suites.

The rig is brought up once per session (honoring ``--config``) and torn down at
the end. When a test fails, its console transcript is attached to the report so
a red build is debuggable.
"""

from __future__ import annotations

import itertools
import time
from pathlib import Path

import pytest

from hilharness.config import RigConfig
from hilharness.drivers.canbus import CanBus
from hilharness.drivers.console import ConsoleShell
from hilharness.drivers.netctl import NetCtl
from hilharness.rig import Rig
from hilharness.validators.boot import validate_boot
from simtarget.can_node import PyCanNode

TRANSCRIPTS: list[str] = []
FAULTS: str = ""


def pytest_addoption(parser):
    parser.addoption(
        "--config",
        action="store",
        default="configs/rig_sim.yaml",
        help="Rig YAML config used by the HIL suites",
    )
    parser.addoption(
        "--faults", action="store", default="", help="Fault spec for the simulated target"
    )


def pytest_configure(config):
    global FAULTS
    FAULTS = config.getoption("--faults") or ""


@pytest.fixture(scope="session")
def cfg(pytestconfig) -> RigConfig:
    return RigConfig.from_yaml(pytestconfig.getoption("--config"))


@pytest.fixture(scope="session")
def rig(cfg) -> Rig:
    r = Rig(cfg, faults=FAULTS)
    cfg.ensure_dirs()
    yield r
    r.close()


@pytest.fixture
def rig_factory(cfg):
    """Build throwaway rigs (e.g. with injected faults), closed at teardown."""
    made: list[Rig] = []

    def _make(faults: str = "", use_net: bool = False) -> Rig:
        r = Rig(cfg, faults=faults, use_net=use_net)
        made.append(r)
        return r

    yield _make
    for r in made:
        r.close()


_cycle = itertools.count(1)


def boot_once(rig: Rig, cfg: RigConfig) -> tuple:
    """Flash, power-cycle and read one boot; return (BootResult, reader)."""
    flash = rig.flasher().flash(cfg.image_path)
    assert flash.verified, "image sha256 mismatch"
    rig.power_cycle()
    n = next(_cycle)
    reader = rig.console(cycle=n)
    reader.capture = ""
    try:
        reader.read_until(r"hil-stage userspace ready", timeout=12.0)
        # Consume the trailing login/shell prompt so the transcript validates
        # cleanly and the shell starts from a known prompt.
        reader.read_until(r"hil\$", timeout=3.0)
    except TimeoutError:
        reader.drain(quiet_for=0.5, max_total=2.0)
    result = validate_boot(reader.capture, cfg.expected_drivers)
    if reader.transcript_path is not None:
        TRANSCRIPTS.append(str(reader.transcript_path))
    return result, reader


@pytest.fixture
def booted(rig, cfg):
    """A freshly booted, clean DUT (the test fails fast if the boot is dirty)."""
    result, reader = boot_once(rig, cfg)
    if not result.clean_bootchain:
        pytest.fail(
            f"boot not clean: missing={result.missing_drivers} "
            f"failed={result.failed_drivers} timeout={result.timeout_drivers}"
        )
    return result, reader


@pytest.fixture
def shell(booted) -> ConsoleShell:
    _result, reader = booted
    return ConsoleShell(reader)


@pytest.fixture
def net(rig, booted):
    """A connected NetCtl to the running DUT control daemon."""
    client = NetCtl(rig.cfg.net)
    client.connect()
    yield client
    client.close()


@pytest.fixture
def can_bus(cfg) -> CanBus:
    bus = CanBus(cfg.can)
    yield bus
    bus.close()


@pytest.fixture
def can_node(can_bus):
    node = PyCanNode(can_bus.channel, can_bus.cfg.dbc_path)
    node.start()
    time.sleep(0.2)
    yield node
    node.stop()


@pytest.hookimpl(hookwrapper=True)
def pytest_runtest_makereport(item, call):
    outcome = yield
    report = outcome.get_result()
    if report.when == "call" and report.failed and TRANSCRIPTS:
        latest = Path(TRANSCRIPTS[-1])
        if latest.exists():
            tail = latest.read_text(encoding="utf-8", errors="replace")[-2000:]
            report.sections.append((f"HIL transcript ({latest})", tail))
