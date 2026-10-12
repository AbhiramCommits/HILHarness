"""Unit tests for power driver selection and the simulated relay."""

from __future__ import annotations

import time

import pytest

from hilharness.config import PowerConfig
from hilharness.drivers.power import GpioRelay, SimRelay, build_power


@pytest.mark.hil
@pytest.mark.sim
def test_build_power_selects_sim():
    relay = build_power(PowerConfig(driver="sim"))
    assert isinstance(relay, SimRelay)


@pytest.mark.hil
@pytest.mark.sim
def test_build_power_selects_gpio_lazily():
    relay = build_power(PowerConfig(driver="gpio", gpio_pin=22))
    assert isinstance(relay, GpioRelay)
    assert relay.pin == 22
    # gpiozero must not be imported / touched until first use.
    assert relay._relay is None


@pytest.mark.hil
@pytest.mark.sim
def test_sim_relay_starts_and_stops_target():
    relay = SimRelay(off_ms=100)
    try:
        relay.on()
        assert relay.running is True
        time.sleep(0.3)
        assert relay.running is True
        relay.off()
        assert relay.running is False
    finally:
        relay.kill_hard()


@pytest.mark.hil
@pytest.mark.sim
def test_sim_relay_cycle_finishes():
    relay = SimRelay(off_ms=100)
    try:
        relay.cycle()
        assert relay.running is True
    finally:
        relay.kill_hard()
