"""CAN traffic generation, DBC decoding and fault-injection detection tests."""

from __future__ import annotations

import itertools
import statistics
import time

import pytest

from hilharness.config import CanConfig
from hilharness.drivers.canbus import CanBus
from hilharness.validators.can import detect_faults, fault_kinds
from simtarget.can_node import CanFaults, PyCanNode

DBC_PATH = "configs/infotainment.dbc"
UNIQ = itertools.count(1)


def make_bus() -> CanBus:
    cfg = CanConfig(interface=f"test{next(UNIQ)}", dbc_path=DBC_PATH, backend="virtual")
    return CanBus(cfg)


def run_capture(faults: CanFaults | None, duration: float, settle: float = 0.2):
    bus = make_bus()
    node = PyCanNode(bus.channel, DBC_PATH, faults=faults)
    node.start()
    try:
        time.sleep(settle)
        frames = bus.stream(duration)
    finally:
        node.stop()
        bus.close()
    return bus, node, frames


def sample_value(sig) -> float:
    lo, hi = sig.minimum, sig.maximum
    phys = (lo + (hi - lo) * 0.4) if (lo is not None and hi is not None) else 1.0
    raw = round((phys - sig.offset) / sig.scale)
    return raw * sig.scale + sig.offset


def _signal_sets(msg):
    """Yield dicts of physical values covering all signals/mux branches."""
    if msg.is_multiplexed():
        mux_sig = next(s for s in msg.signals if s.is_multiplexer)
        mux_values = sorted(
            {mid for s in msg.signals if s.multiplexer_ids for mid in s.multiplexer_ids}
        )
        for mv in mux_values:
            sigs = {mux_sig.name: mv}
            for s in msg.signals:
                if s.is_multiplexer:
                    continue
                if s.multiplexer_ids and mv in s.multiplexer_ids:
                    sigs[s.name] = sample_value(s)
                elif not s.multiplexer_ids:
                    sigs[s.name] = sample_value(s)
            yield sigs
    else:
        yield {s.name: sample_value(s) for s in msg.signals}


@pytest.mark.hil
@pytest.mark.sim
def test_dbc_decode_roundtrip_every_signal():
    db = CanBus(CanConfig(interface="rt", dbc_path=DBC_PATH, backend="virtual")).dbc
    n_signals = 0
    for msg in db.messages:
        for sigs in _signal_sets(msg):
            encoded = msg.encode(sigs)
            decoded = msg.decode(encoded, decode_choices=False)
            for name, value in sigs.items():
                n_signals += 1
                assert decoded[name] == pytest.approx(
                    value, abs=msg.get_signal_by_name(name).scale * 1e-6 + 1e-9
                ), f"{msg.name}.{name}: {decoded[name]} != {value}"
    assert n_signals >= 15


@pytest.mark.hil
@pytest.mark.sim
def test_periodicity_under_load():
    _bus, _node, frames = run_capture(None, duration=2.0)
    veh = sorted(f.timestamp for f in frames if f.arbitration_id == 0x100)
    assert len(veh) >= 30
    periods = [(b - a) * 1000.0 for a, b in zip(veh, veh[1:], strict=False)]
    mean = statistics.fmean(periods)
    assert abs(mean - 20.0) <= 10.0, f"mean period {mean:.2f} ms"
    assert max(periods) < 60.0


@pytest.mark.hil
@pytest.mark.sim
def test_diagnostic_request_response():
    bus = make_bus()
    node = PyCanNode(bus.channel, DBC_PATH)
    node.start()
    try:
        time.sleep(0.2)
        bus.send(
            0x7DF,
            signals={"ServiceId": 0x22, "DidHigh": 0xF1, "DidLow": 0x90, "RequestSeq": 7},
            name="DIAG_REQUEST",
        )
        found = None
        deadline = time.monotonic() + 2.0
        while time.monotonic() < deadline:
            cf = bus.recv(timeout=0.3)
            if cf is not None and cf.arbitration_id == 0x7E8:
                found = cf
                break
        assert found is not None, "no diagnostic response"
        assert found.decoded["ResponseService"] == 0x62
        assert found.decoded["DidHigh"] == 0xF1
        assert found.decoded["ResponseSeq"] == 7
    finally:
        node.stop()
        bus.close()


FAULT_CASES = [
    ("drop_rate", CanFaults(drop_rate=0.5), "FrameLoss"),
    ("stale_signal", CanFaults(stale_signal="VehicleSpeed"), "StaleSignal"),
    ("bad_dlc", CanFaults(bad_dlc=4), "BadDLC"),
    ("out_of_range", CanFaults(out_of_range="VehicleSpeed"), "SignalOutOfRange"),
    ("silence_ms", CanFaults(silence_ms=800), "BusSilence"),
]


@pytest.mark.hil
@pytest.mark.sim
@pytest.mark.parametrize("name,faults,expected_kind", FAULT_CASES, ids=[c[0] for c in FAULT_CASES])
def test_each_fault_type_is_detected(name, faults, expected_kind):
    _bus, _node, frames = run_capture(faults, duration=2.0, settle=0.3)
    kinds = fault_kinds(detect_faults(frames, db=_bus.dbc))
    assert expected_kind in kinds, f"{name}: expected {expected_kind}, got {kinds}"


@pytest.mark.hil
@pytest.mark.sim
def test_fault_detection_rate_is_complete():
    rate = 0
    for _name, faults, expected_kind in FAULT_CASES:
        bus, _node, frames = run_capture(faults, duration=1.5, settle=0.3)
        kinds = fault_kinds(detect_faults(frames, db=bus.dbc))
        if expected_kind in kinds:
            rate += 1
    pct = 100.0 * rate / len(FAULT_CASES)
    print(f"\nfault detection rate: {rate}/{len(FAULT_CASES)} = {pct:.0f}%")
    assert pct >= 80.0


@pytest.mark.hil
@pytest.mark.sim
def test_clean_stream_has_no_faults():
    bus, _node, frames = run_capture(None, duration=2.0)
    findings = detect_faults(frames, db=bus.dbc)
    assert findings == [], f"unexpected findings: {[f.kind for f in findings]}"
