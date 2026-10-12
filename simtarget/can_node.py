"""Python ECU CAN node (fallback when SocketCAN is unavailable).

Mirrors the behaviour of ``native/can_node.c``: transmits periodic infotainment
frames, answers diagnostic requests, and supports the same fault-injection
flags. Runs on python-can's in-process ``virtual`` bus, so it shares a channel
with :class:`~hilharness.drivers.canbus.CanBus` in the same process.
"""

from __future__ import annotations

import random
import threading
import time
from dataclasses import dataclass
from pathlib import Path

import can
import cantools

# (message name, period ms)
TX_SCHEDULE: list[tuple[str, float]] = [
    ("VEH_SPEED", 20.0),
    ("VEH_MUX", 50.0),
    ("HVAC_STATUS", 100.0),
    ("DISPLAY_STATUS", 100.0),
    ("AUDIO_STATUS", 100.0),
    ("BT_STATUS", 200.0),
]

DIAG_REQUEST_ID = 0x7DF
DIAG_RESPONSE_ID = 0x7E8


@dataclass
class CanFaults:
    drop_rate: float = 0.0
    stale_signal: str | None = None
    bad_dlc: int | None = None
    out_of_range: str | None = None
    silence_ms: int = 0

    def active(self) -> list[str]:
        out = []
        if self.drop_rate > 0:
            out.append(f"drop_rate={self.drop_rate}")
        if self.stale_signal:
            out.append(f"stale={self.stale_signal}")
        if self.bad_dlc is not None:
            out.append(f"bad_dlc={self.bad_dlc}")
        if self.out_of_range:
            out.append(f"out_of_range={self.out_of_range}")
        if self.silence_ms:
            out.append(f"silence_ms={self.silence_ms}")
        return out


def _initial_signals(name: str, db: cantools.database.Database, t: float) -> dict:
    """Plausible, slowly varying signals for a message."""
    import math

    if name == "VEH_SPEED":
        return {
            "VehicleSpeed": round(60 + 40 * math.sin(t / 4.0), 2),
            "GearPos": 3,
            "BrakeActive": 0,
            "ThrottlePos": round(20 + 15 * math.sin(t / 3.0), 1),
            "EngineRpm": round(1800 + 900 * math.sin(t / 3.0), 1),
        }
    if name == "HVAC_STATUS":
        return {
            "SetpointTemp": 22.0,
            "CabinTemp": round(21 + math.sin(t / 6.0), 1),
            "FanSpeed": 3,
            "ACOn": 1,
            "RecircOn": 0,
            "AirflowMode": 1,
        }
    if name == "DISPLAY_STATUS":
        return {"Brightness": 80, "Theme": 1, "DayNightMode": 0, "BrightnessManual": 0}
    if name == "AUDIO_STATUS":
        return {"Volume": 30, "AudioSource": 2, "Muted": 0, "Balance": 0, "Fader": 0}
    if name == "BT_STATUS":
        return {"BtState": 2, "PairedDevices": 3, "CallActive": 0, "CallDuration": 0}
    if name == "VEH_MUX":
        return {"Mux": 2, "AmbientLight": round(50 + 10 * math.sin(t), 1)}
    return {}


class PyCanNode:
    def __init__(
        self,
        channel: str,
        dbc_path: str | Path,
        faults: CanFaults | None = None,
        seed: int = 1234,
    ) -> None:
        self.channel = channel
        self.db = cantools.database.load_file(str(dbc_path))
        self.faults = faults or CanFaults()
        self.rng = random.Random(seed)
        self.bus: can.BusABC | None = None
        self._stop = threading.Event()
        self._threads: list[threading.Thread] = []
        self.tx_counts: dict[str, int] = {}
        self.counter = 0
        self._start = time.monotonic()
        self.diag_responses = 0

    # -- lifecycle -------------------------------------------------------
    def start(self) -> None:
        self.bus = can.Bus(interface="virtual", channel=self.channel)
        self._start = time.monotonic()
        self._threads.append(threading.Thread(target=self._tx_loop, daemon=True))
        self._threads.append(threading.Thread(target=self._rx_loop, daemon=True))
        for t in self._threads:
            t.start()

    def stop(self) -> None:
        self._stop.set()
        for t in self._threads:
            t.join(timeout=2.0)
        if self.bus is not None:
            try:
                self.bus.shutdown()
            except Exception:
                pass

    # -- transmit --------------------------------------------------------
    def _maybe_drop(self) -> bool:
        return self.faults.drop_rate > 0 and self.rng.random() < self.faults.drop_rate

    def _build(self, name: str, t: float) -> bytes:
        msg = self.db.get_message_by_name(name)
        sigs = _initial_signals(name, self.db, t)
        if name == "VEH_SPEED":
            self.counter = (self.counter + 1) % 16
            sigs["MsgCounter"] = self.counter
        if self.faults.stale_signal and self.faults.stale_signal in sigs:
            sigs[self.faults.stale_signal] = 42
        data = bytearray(msg.encode(sigs))
        if self.faults.out_of_range == "VehicleSpeed" and name == "VEH_SPEED":
            # Bypass cantools' range check: write a raw 30000 (=300.00 km/h,
            # above the DBC max of 250) straight into the little-endian signal.
            raw = 30000
            data[0] = raw & 0xFF
            data[1] = (raw >> 8) & 0xFF
        if self.faults.bad_dlc is not None:
            data = data[: self.faults.bad_dlc]
        return bytes(data)

    def _tx_loop(self) -> None:
        next_send = {name: self._start for name, _ in TX_SCHEDULE}
        silence_start = self._start + 0.4
        silence_end = silence_start + self.faults.silence_ms / 1000.0
        while not self._stop.is_set():
            now = time.monotonic()
            t = now - self._start
            for name, period_ms in TX_SCHEDULE:
                if now < next_send[name]:
                    continue
                next_send[name] = now + period_ms / 1000.0
                # Build first so the rolling counter advances even when the
                # frame is dropped -- otherwise a drop is invisible.
                try:
                    data = self._build(name, t)
                except Exception:
                    continue
                if silence_start <= now < silence_end:
                    continue
                if self._maybe_drop():
                    continue
                msg = self.db.get_message_by_name(name)
                frame = can.Message(arbitration_id=msg.frame_id, data=data, is_extended_id=False)
                try:
                    assert self.bus is not None
                    self.bus.send(frame)
                    self.tx_counts[name] = self.tx_counts.get(name, 0) + 1
                except can.CanError:
                    pass
            time.sleep(0.001)

    # -- diagnostics -----------------------------------------------------
    def _rx_loop(self) -> None:
        while not self._stop.is_set():
            try:
                assert self.bus is not None
                frame = self.bus.recv(timeout=0.2)
            except can.CanError:
                continue
            if frame is None or frame.arbitration_id != DIAG_REQUEST_ID:
                continue
            try:
                req = self.db.get_message_by_name("DIAG_REQUEST").decode(frame.data)
            except Exception:
                continue
            resp_msg = self.db.get_message_by_name("DIAG_RESPONSE")
            payload = {
                "ResponseService": (req.get("ServiceId", 0) + 0x40) & 0xFF,
                "DidHigh": req.get("DidHigh", 0),
                "DidLow": req.get("DidLow", 0),
                "DataValue": 0x1234,
                "ResponseSeq": req.get("RequestSeq", 0),
            }
            try:
                out = can.Message(
                    arbitration_id=DIAG_RESPONSE_ID,
                    data=resp_msg.encode(payload),
                    is_extended_id=False,
                )
                self.bus.send(out)
                self.diag_responses += 1
            except (can.CanError, Exception):
                pass


def build_faults_from_flags(
    drop_rate: float = 0.0,
    stale_signal: str | None = None,
    bad_dlc: int | None = None,
    out_of_range: str | None = None,
    silence_ms: int = 0,
) -> CanFaults:
    return CanFaults(drop_rate, stale_signal, bad_dlc, out_of_range, silence_ms)
