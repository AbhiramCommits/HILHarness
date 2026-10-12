"""CAN bus driver: SocketCAN with automatic virtual-bus fallback.

``CanBus`` tries ``socketcan`` on the configured interface (Linux) and falls
back to python-can's in-process ``virtual`` bus, so the suite runs on any host.
Frames are decoded with the configured DBC.
"""

from __future__ import annotations

import sys
import time
from dataclasses import dataclass, field
from pathlib import Path

import can
import cantools

from ..config import CanConfig


def virtual_channel(cfg: CanConfig) -> str:
    """Deterministic virtual-bus channel name derived from the interface."""
    return f"hil-{cfg.interface or 'virtual'}"


@dataclass
class CanFrame:
    timestamp: float
    arbitration_id: int
    data: bytes
    dlc: int
    is_extended: bool
    decoded: dict = field(default_factory=dict)
    decode_error: str | None = None

    @property
    def message_name(self) -> str | None:
        return self.decoded.get("__name__")


class CanBus:
    def __init__(self, cfg: CanConfig) -> None:
        self.cfg = cfg
        self.dbc = cantools.database.load_file(str(cfg.dbc_path))
        self.backend, self.bus = self._open()

    @property
    def channel(self) -> str:
        if self.backend == "socketcan":
            return self.cfg.interface
        return virtual_channel(self.cfg)

    def _open(self) -> tuple[str, can.BusABC]:
        wants_socketcan = self.cfg.backend in ("auto", "socketcan")
        if wants_socketcan and sys.platform.startswith("linux") and self.cfg.interface:
            try:
                bus = can.Bus(
                    interface="socketcan", channel=self.cfg.interface, bitrate=self.cfg.bitrate
                )
                return "socketcan", bus
            except Exception:
                pass
        bus = can.Bus(interface="virtual", channel=virtual_channel(self.cfg))
        return "virtual", bus

    # -- message helpers -------------------------------------------------
    def message_by_id(self, frame_id: int):
        return self.dbc.get_message_by_frame_id(frame_id)

    def _decode(self, frame: can.Message) -> CanFrame:
        out = CanFrame(
            timestamp=frame.timestamp,
            arbitration_id=frame.arbitration_id,
            data=bytes(frame.data),
            dlc=frame.dlc,
            is_extended=frame.is_extended_id,
        )
        try:
            msg = self.message_by_id(frame.arbitration_id)
            out.decoded = msg.decode(bytes(frame.data), decode_choices=True)
            out.decoded["__name__"] = msg.name
        except KeyError:
            out.decode_error = "unknown_id"
        except Exception as exc:  # cantools DecodeError etc.
            out.decode_error = f"decode:{type(exc).__name__}"
        return out

    # -- IO --------------------------------------------------------------
    def send(
        self,
        frame_id: int,
        data: bytes | None = None,
        signals: dict | None = None,
        name: str | None = None,
    ) -> None:
        if signals is not None or name is not None:
            msg = self.dbc.get_message_by_name(name) if name else self.message_by_id(frame_id)
            frame_id = msg.frame_id
            data = msg.encode(signals or {})
        assert data is not None
        self.bus.send(
            can.Message(arbitration_id=frame_id, data=bytearray(data), is_extended_id=False)
        )

    def recv(self, timeout: float = 0.5) -> CanFrame | None:
        frame = self.bus.recv(timeout=timeout)
        return self._decode(frame) if frame is not None else None

    def stream(
        self, duration: float, message_name: str | None = None, max_frames: int = 2_000_000
    ) -> list[CanFrame]:
        """Collect frames for ``duration`` seconds (optionally one message)."""
        deadline = time.monotonic() + duration
        frames: list[CanFrame] = []
        while time.monotonic() < deadline and len(frames) < max_frames:
            remaining = deadline - time.monotonic()
            cf = self.recv(timeout=min(0.2, max(0.0, remaining)))
            if cf is None:
                continue
            if message_name is not None and cf.message_name != message_name:
                continue
            frames.append(cf)
        return frames

    # -- stats -----------------------------------------------------------
    def sequence_loss_stats(
        self,
        frames: list[CanFrame],
        message_name: str = "VEH_SPEED",
        counter_signal: str = "MsgCounter",
    ) -> dict:
        """Compute frame loss from a wrapping rolling-counter signal."""
        counters = [
            int(f.decoded[counter_signal])
            for f in frames
            if f.message_name == message_name and counter_signal in f.decoded
        ]
        if len(counters) < 2:
            return {
                "message": message_name,
                "delivered": len(counters),
                "lost": 0,
                "loss_pct": 0.0,
                "modulus": 16,
            }
        modulus = 16
        lost = 0
        for prev, cur in zip(counters, counters[1:], strict=False):
            gap = (cur - prev) % modulus
            if gap == 0:
                lost += modulus - 1  # duplicate / stalled counter
            else:
                lost += gap - 1
        delivered = len(counters)
        total = delivered + lost
        return {
            "message": message_name,
            "delivered": delivered,
            "lost": lost,
            "loss_pct": round(100.0 * lost / total, 4) if total else 0.0,
            "modulus": modulus,
        }

    def close(self) -> None:
        try:
            self.bus.shutdown()
        except Exception:
            pass


def load_dbc(path: str | Path):
    return cantools.database.load_file(str(path))
