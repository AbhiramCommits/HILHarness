"""Serial / PTY console driver.

``ConsoleReader`` reads the DUT's console output. In simulation it reads the
master side of a PTY pair (the target writes to the slave); on hardware it reads
a real ``/dev/tty*`` device through pyserial. ``ConsoleShell`` sends commands
and asserts on prompts/errors.
"""

from __future__ import annotations

import os
import re
import select
import time
from pathlib import Path
from re import Pattern

from ..config import ConsoleConfig


class ConsoleReader:
    def __init__(
        self,
        cfg: ConsoleConfig,
        master_fd: int | None = None,
        transcript_path: Path | None = None,
    ) -> None:
        self.cfg = cfg
        self.master_fd = master_fd
        self.serial = None
        self.capture = ""
        self.transcript_path = transcript_path
        self._raw = bytearray()

        if cfg.driver == "pty":
            if master_fd is None:
                raise ValueError("ptydriver requires a master_fd")
            os.set_blocking(master_fd, False)
        else:  # serial
            import serial  # lazy import

            self.serial = serial.Serial(cfg.device, cfg.baud, timeout=0)

        if self.transcript_path is not None:
            Path(self.transcript_path).parent.mkdir(parents=True, exist_ok=True)
            Path(self.transcript_path).write_text("", encoding="utf-8")

    # -- low level -------------------------------------------------------
    def _read_chunk(self, timeout: float) -> bytes:
        deadline = time.monotonic() + timeout
        while True:
            if self.master_fd is not None:
                r, _, _ = select.select([self.master_fd], [], [], max(0.0, timeout))
                if r:
                    try:
                        data = os.read(self.master_fd, 65536)
                    except (BlockingIOError, OSError):
                        data = b""
                    if data:
                        return data
                return b""
            # serial path
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                return b""
            if self.serial is not None and self.serial.in_waiting:
                return self.serial.read(self.serial.in_waiting)
            time.sleep(0.01)

    def _append(self, data: bytes) -> None:
        if not data:
            return
        self._raw.extend(data)
        text = data.decode("utf-8", "replace")
        self.capture += text
        if self.transcript_path is not None:
            with open(self.transcript_path, "a", encoding="utf-8") as fh:
                fh.write(text)

    def read_available(self, timeout: float = 0.2) -> str:
        """Read whatever is currently available; return the new text."""
        start_len = len(self.capture)
        data = self._read_chunk(timeout)
        self._append(data)
        return self.capture[start_len:]

    def read_until(
        self,
        pattern: str | Pattern[str],
        timeout: float = 5.0,
        reset: bool = False,
    ) -> str:
        """Read until ``pattern`` appears or timeout. Returns the matched text.

        Raises :class:`TimeoutError` if the pattern never shows up.
        """
        if reset:
            self.capture = ""
        rx = re.compile(pattern) if isinstance(pattern, str) else pattern
        deadline = time.monotonic() + timeout
        while True:
            m = rx.search(self.capture)
            if m:
                return self.capture
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise TimeoutError(
                    f"pattern {pattern!r} not seen within {timeout}s; got {len(self.capture)} bytes"
                )
            data = self._read_chunk(min(0.3, remaining))
            self._append(data)

    def drain(self, quiet_for: float = 0.4, max_total: float = 10.0) -> str:
        """Read until the line goes quiet for ``quiet_for`` seconds."""
        start_len = len(self.capture)
        last = time.monotonic()
        deadline = time.monotonic() + max_total
        while time.monotonic() < deadline:
            data = self._read_chunk(0.2)
            if data:
                self._append(data)
                last = time.monotonic()
            elif time.monotonic() - last >= quiet_for:
                break
        return self.capture[start_len:]

    def write(self, data: str) -> None:
        raw = data.encode("utf-8")
        if self.master_fd is not None:
            os.write(self.master_fd, raw)
        elif self.serial is not None:
            self.serial.write(raw)

    def close(self) -> None:
        if self.serial is not None:
            try:
                self.serial.close()
            except Exception:
                pass


class ConsoleShell:
    """Send commands over the UART console and assert on the response."""

    def __init__(self, reader: ConsoleReader, prompt: str = r"hil\$") -> None:
        self.reader = reader
        self.prompt = prompt

    def run(self, cmd: str, timeout: float = 5.0) -> str:
        self.reader.write(cmd + "\n")
        return self.reader.read_until(self.prompt, timeout=timeout, reset=True)

    def expect(self, cmd: str, needle: str, timeout: float = 5.0) -> str:
        out = self.run(cmd, timeout=timeout)
        if needle not in out:
            raise AssertionError(f"expected {needle!r} in output of {cmd!r}; got:\n{out}")
        return out

    def expect_error(self, cmd: str, timeout: float = 5.0) -> str:
        out = self.run(cmd, timeout=timeout)
        if "not found" not in out and "ERR" not in out and "error" not in out.lower():
            raise AssertionError(f"expected an error from {cmd!r}; got:\n{out}")
        return out
