"""The simulated DUT process.

Run as ``python -m simtarget.target``. Its stdin/stdout are wired to a PTY slave
allocated by the harness, so everything it prints becomes a real UART transcript
that the harness reads through a genuine pty device. After the boot log it opens
a TCP control port and serves the line protocol used by ``NetCtl``.

Faults are injected through ``--faults`` (or the ``HIL_FAULT`` env var) and a
seed makes runs reproducible.
"""

from __future__ import annotations

import argparse
import json
import os
import random
import signal
import socket
import socketserver
import sys
import threading
import time

from . import bootlog
from .ecu_profile import Faults


class TargetState:
    def __init__(self) -> None:
        self.start = time.monotonic()
        self.values = {
            "display.brightness": 50,
            "audio.volume": 30,
            "audio.source": "radio",
            "bt.state": "off",
            "wifi.state": "connected",
        }
        self.stress_threads: list[threading.Thread] = []
        self.hold: list[bytearray] = []
        self.lock = threading.Lock()

    def uptime(self) -> float:
        return time.monotonic() - self.start


STATE = TargetState()


def _stress_cpu(seconds: float) -> None:
    end = time.monotonic() + seconds
    x = 0
    while time.monotonic() < end:
        x = (x * 1103515245 + 12345) & 0x7FFFFFFF


def _stress_mem(mb: int) -> None:
    # Allocate in ~1 MiB chunks and touch every page so RSS actually grows.
    chunk = 1024 * 1024
    for _ in range(mb):
        block = bytearray(chunk)
        for i in range(0, chunk, 4096):
            block[i] = 1
        with STATE.lock:
            STATE.hold.append(block)


def start_stress(kind: str, amount: int) -> str:
    if kind == "cpu":
        for _ in range(max(1, os.cpu_count() or 1)):
            t = threading.Thread(target=_stress_cpu, args=(float(amount),), daemon=True)
            t.start()
            STATE.stress_threads.append(t)
        return f"OK stress cpu {amount}"
    if kind == "mem":
        t = threading.Thread(target=_stress_mem, args=(amount,), daemon=True)
        t.start()
        STATE.stress_threads.append(t)
        return f"OK stress mem {amount}"
    return "ERR unknown stress kind"


def handle_command(line: str) -> str:
    """Handle one line-protocol command. Returns a response string (no newline)."""
    line = line.strip()
    if not line:
        return ""
    cmd, _, rest = line.partition(" ")
    cmd = cmd.upper()
    if cmd == "PING":
        return "PONG"
    if cmd == "SYSINFO":
        info = {
            "uptime_s": round(STATE.uptime(), 3),
            "cpu_count": os.cpu_count(),
            "drivers": [d for d in STATE.values if False] or [],
            "version": "sim-hil-1.0",
        }
        try:
            import psutil

            info["cpu_pct"] = psutil.Process().cpu_percent(interval=0.0)
            info["rss_mb"] = round(psutil.Process().memory_info().rss / (1024 * 1024), 2)
        except Exception:  # pragma: no cover - psutil always present in practice
            pass
        return json.dumps(info)
    if cmd == "SET":
        if "=" not in rest:
            return "ERR usage: SET key=value"
        key, _, val = rest.partition("=")
        STATE.values[key.strip()] = val.strip()
        return f"OK {key.strip()}={val.strip()}"
    if cmd == "GET":
        key = rest.strip()
        if key not in STATE.values:
            return f"ERR unknown key {key}"
        return f"{key}={STATE.values[key]}"
    if cmd == "STRESS":
        parts = rest.split()
        if len(parts) != 2:
            return "ERR usage: STRESS <cpu|mem> <amount>"
        try:
            amount = int(parts[1])
        except ValueError:
            return "ERR amount must be an integer"
        return start_stress(parts[0].lower(), amount)
    if cmd == "REBOOT":
        threading.Thread(target=_delayed_exit, daemon=True).start()
        return "OK rebooting"
    return f"ERR unknown command {cmd}"


def _delayed_exit() -> None:
    time.sleep(0.2)
    os.kill(os.getpid(), signal.SIGTERM)


class ControlHandler(socketserver.StreamRequestHandler):
    def handle(self) -> None:
        for raw in self.rfile:
            try:
                line = raw.decode("utf-8", "replace")
            except Exception:
                continue
            resp = handle_command(line)
            if resp:
                self.wfile.write((resp + "\n").encode())
                self.wfile.flush()


class ControlServer(socketserver.ThreadingTCPServer):
    allow_reuse_address = True
    daemon_threads = True


def _console_loop() -> None:
    """Read UART lines from stdin and answer simple shell commands."""
    try:
        for raw in sys.stdin:
            cmd = raw.strip()
            if not cmd:
                continue
            low = cmd.lower()
            if low in ("sysinfo", "sysinfo\n"):
                out = handle_command("SYSINFO")
            elif low.startswith("echo "):
                out = cmd[5:]
            elif low.startswith("get "):
                out = handle_command("GET " + cmd[4:])
            elif low.startswith("set "):
                out = handle_command("SET " + cmd[4:])
            elif low.startswith("stress "):
                out = handle_command("STRESS " + cmd[7:])
            elif low == "ping":
                out = "PONG"
            elif low in ("reboot", "sleep"):
                out = "OK rebooting"
                sys.stdout.write(out + "\n")
                sys.stdout.flush()
                _delayed_exit()
                return
            else:
                out = f"sh: {cmd}: not found"
            sys.stdout.write(out + "\n" + "hil$ ")
            sys.stdout.flush()
    except (ValueError, OSError):
        return


def run(
    faults: Faults, seed: int, jitter_pct: float, line_delay_ms: float, net_port: int, net_host: str
) -> None:
    log = bootlog.generate(faults=faults, jitter_pct=jitter_pct, seed=seed)
    for line in log.lines:
        sys.stdout.write(line + "\n")
        sys.stdout.flush()
        if line_delay_ms > 0:
            time.sleep(line_delay_ms / 1000.0)
    sys.stdout.write("hil login: \n")
    sys.stdout.write("hil$ ")
    sys.stdout.flush()

    server = None
    try:
        server = ControlServer((net_host, net_port), ControlHandler)
        threading.Thread(target=server.serve_forever, daemon=True).start()
    except OSError as exc:
        sys.stderr.write(f"simtarget: control server unavailable on {net_host}:{net_port}: {exc}\n")
        sys.stderr.flush()

    threading.Thread(target=_console_loop, daemon=True).start()

    def _shutdown(signum, frame):  # noqa: ANN001
        try:
            if server is not None:
                server.shutdown()
        except Exception:
            pass
        sys.exit(0)

    signal.signal(signal.SIGTERM, _shutdown)
    signal.signal(signal.SIGINT, _shutdown)
    try:
        while True:
            time.sleep(1.0)
    except (KeyboardInterrupt, SystemExit):
        pass


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(prog="simtarget")
    ap.add_argument("--faults", default=os.environ.get("HIL_FAULT", ""))
    ap.add_argument("--seed", type=int, default=int(os.environ.get("HIL_SEED", "-1")))
    ap.add_argument("--jitter", type=float, default=float(os.environ.get("HIL_JITTER", "4.0")))
    ap.add_argument(
        "--line-delay-ms",
        type=float,
        default=float(os.environ.get("HIL_LINE_DELAY_MS", "6.0")),
    )
    ap.add_argument("--net-port", type=int, default=int(os.environ.get("HIL_NET_PORT", "0")))
    ap.add_argument("--net-host", default=os.environ.get("HIL_NET_HOST", "127.0.0.1"))
    args = ap.parse_args(argv)

    seed = args.seed if args.seed >= 0 else random.randrange(1, 2**31)

    port = args.net_port
    if port == 0:
        # Ask the OS for a free port and advertise it on stdout so the harness
        # can pick it up if it did not pin one.
        s = socket.socket()
        s.bind((args.net_host, 0))
        port = s.getsockname()[1]
        s.close()

    run(
        Faults.from_spec(args.faults),
        seed,
        args.jitter,
        args.line_delay_ms,
        port,
        args.net_host,
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
