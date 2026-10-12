"""psutil-based process sampling of the DUT target.

Used during boot and under ``STRESS`` to measure real CPU% and RSS of the target
process (the simulated target allocates memory and burns CPU in-process, so the
numbers are genuine).
"""

from __future__ import annotations

import threading
import time

import psutil


class ProcessSampler:
    def __init__(self, pid: int, interval: float = 0.05) -> None:
        self.pid = pid
        self.interval = interval
        self.cpu: list[float] = []
        self.rss_mb: list[float] = []
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def _proc(self) -> psutil.Process | None:
        try:
            return psutil.Process(self.pid)
        except psutil.Error:
            return None

    def _run(self) -> None:
        proc = self._proc()
        if proc is None:
            return
        proc.cpu_percent(interval=None)  # prime
        while not self._stop.is_set():
            try:
                self.cpu.append(proc.cpu_percent(interval=None))
                self.rss_mb.append(proc.memory_info().rss / (1024 * 1024))
            except psutil.Error:
                return
            time.sleep(self.interval)

    def start(self) -> ProcessSampler:
        self._thread = threading.Thread(target=self._run, daemon=True)
        self._thread.start()
        return self

    def stop(self) -> dict:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
        return self.summary()

    def summary(self) -> dict:
        cpu = self.cpu or [0.0]
        rss = self.rss_mb or [0.0]
        return {
            "cpu_pct_max": max(cpu),
            "cpu_pct_mean": sum(cpu) / len(cpu),
            "rss_mb_max": max(rss),
            "rss_mb_mean": sum(rss) / len(rss),
            "samples": len(self.cpu),
        }


def sample_process(pid: int, duration: float, interval: float = 0.05) -> dict:
    sampler = ProcessSampler(pid, interval=interval).start()
    time.sleep(duration)
    return sampler.stop()
