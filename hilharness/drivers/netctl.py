"""TCP/IP DUT control driver.

A small line protocol over TCP with explicit timeouts, retry-with-backoff on
connect, and per-request latency measurement:

    PING / SYSINFO / SET key=value / GET key / STRESS cpu|mem N / REBOOT
"""

from __future__ import annotations

import json
import socket
import time
from dataclasses import dataclass

from ..config import NetConfig


class NetCtlError(RuntimeError):
    pass


@dataclass
class NetResponse:
    command: str
    response: str
    latency_ms: float
    attempts: int = 1

    def to_dict(self) -> dict:
        return {
            "command": self.command,
            "response": self.response,
            "latency_ms": round(self.latency_ms, 3),
            "attempts": self.attempts,
        }


class NetCtl:
    def __init__(self, cfg: NetConfig) -> None:
        self.cfg = cfg
        self.sock: socket.socket | None = None
        self.latencies: list[float] = []
        self._buf = b""

    # -- connection ------------------------------------------------------
    def connect(self) -> None:
        if self.sock is not None:
            return
        last: OSError | None = None
        for attempt in range(1, self.cfg.retries + 1):
            try:
                self.sock = socket.create_connection(
                    (self.cfg.host, self.cfg.port), timeout=self.cfg.timeout_s
                )
                self.sock.settimeout(self.cfg.timeout_s)
                return
            except OSError as exc:
                last = exc
                time.sleep(self.cfg.backoff_s * attempt)
        raise NetCtlError(f"could not connect to {self.cfg.host}:{self.cfg.port}: {last}")

    def close(self) -> None:
        if self.sock is not None:
            try:
                self.sock.close()
            except OSError:
                pass
            self.sock = None

    def __enter__(self) -> NetCtl:
        self.connect()
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    @property
    def connected(self) -> bool:
        return self.sock is not None

    # -- protocol --------------------------------------------------------
    def _recv_line(self) -> str:
        assert self.sock is not None
        deadline = time.monotonic() + self.cfg.timeout_s
        while b"\n" not in self._buf:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise NetCtlError("timed out waiting for response")
            self.sock.settimeout(remaining)
            try:
                chunk = self.sock.recv(4096)
            except TimeoutError as exc:
                raise NetCtlError("timed out waiting for response") from exc
            if not chunk:
                raise NetCtlError("connection closed by DUT")
            self._buf += chunk
        line, _, rest = self._buf.partition(b"\n")
        self._buf = rest
        return line.decode("utf-8", "replace")

    def request(self, command: str, timeout: float | None = None) -> NetResponse:
        if self.sock is None:
            self.connect()
        assert self.sock is not None
        if timeout is not None:
            self.sock.settimeout(timeout)
        start = time.perf_counter()
        try:
            self.sock.sendall((command + "\n").encode("utf-8"))
            line = self._recv_line()
        finally:
            self.sock.settimeout(self.cfg.timeout_s)
        latency = (time.perf_counter() - start) * 1000.0
        self.latencies.append(latency)
        return NetResponse(command=command, response=line, latency_ms=latency)

    # -- convenience -----------------------------------------------------
    def ping(self) -> bool:
        return self.request("PING").response.strip() == "PONG"

    def sysinfo(self) -> dict:
        return json.loads(self.request("SYSINFO").response)

    def set(self, key: str, value) -> str:
        return self.request(f"SET {key}={value}").response

    def get(self, key: str) -> str:
        resp = self.request(f"GET {key}").response
        if resp.startswith("ERR"):
            raise NetCtlError(resp)
        return resp.split("=", 1)[1] if "=" in resp else resp

    def stress(self, kind: str, amount: int) -> str:
        return self.request(f"STRESS {kind} {amount}").response

    def reboot(self) -> str:
        return self.request("REBOOT").response

    @property
    def mean_latency_ms(self) -> float:
        return sum(self.latencies) / len(self.latencies) if self.latencies else 0.0
