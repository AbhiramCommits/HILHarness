"""CAN integration suite: traffic on the rig's bus, decoded via the DBC."""

from __future__ import annotations

import statistics

import pytest

from hilharness.validators.can import detect_faults

EXPECTED_MESSAGES = {
    "VEH_SPEED",
    "VEH_MUX",
    "HVAC_STATUS",
    "DISPLAY_STATUS",
    "AUDIO_STATUS",
    "BT_STATUS",
}


@pytest.mark.hil
@pytest.mark.sim
def test_receives_all_message_types(can_bus, can_node):
    frames = can_bus.stream(1.5)
    names = {f.message_name for f in frames if f.message_name}
    assert EXPECTED_MESSAGES <= names, f"missing {EXPECTED_MESSAGES - names}"


@pytest.mark.hil
@pytest.mark.sim
def test_decoded_signal_values_in_range(can_bus, can_node):
    frames = can_bus.stream(1.5)
    findings = detect_faults(frames, db=can_bus.dbc)
    assert findings == [], f"unexpected findings: {[f.kind for f in findings]}"


@pytest.mark.hil
@pytest.mark.sim
def test_vehicle_speed_periodicity(can_bus, can_node):
    frames = can_bus.stream(1.5)
    ts = sorted(f.timestamp for f in frames if f.arbitration_id == 0x100)
    assert len(ts) >= 20
    periods = [(b - a) * 1000.0 for a, b in zip(ts, ts[1:], strict=False)]
    assert abs(statistics.fmean(periods) - 20.0) <= 10.0


@pytest.mark.hil
@pytest.mark.sim
def test_round_trip_specific_signals(can_bus, can_node):
    frames = can_bus.stream(1.0)
    hvac = next(f for f in frames if f.message_name == "HVAC_STATUS")
    assert 16 <= hvac.decoded["SetpointTemp"] <= 30
    speed = next(f for f in frames if f.message_name == "VEH_SPEED")
    assert 0 <= speed.decoded["VehicleSpeed"] <= 250


@pytest.mark.hil
@pytest.mark.sim
def test_no_frame_loss_on_clean_bus(can_bus, can_node):
    frames = can_bus.stream(1.5)
    stats = can_bus.sequence_loss_stats(frames)
    assert stats["delivered"] > 20
    assert stats["loss_pct"] == 0.0


@pytest.mark.hil
@pytest.mark.sim
def test_diagnostic_response(can_bus, can_node):
    import time

    can_bus.send(
        0x7DF,
        signals={"ServiceId": 0x22, "DidHigh": 0xF1, "DidLow": 0x90, "RequestSeq": 1},
        name="DIAG_REQUEST",
    )
    deadline = time.monotonic() + 2.0
    resp = None
    while time.monotonic() < deadline:
        cf = can_bus.recv(timeout=0.3)
        if cf is not None and cf.arbitration_id == 0x7E8:
            resp = cf
            break
    assert resp is not None
    assert resp.decoded["ResponseService"] == 0x62
