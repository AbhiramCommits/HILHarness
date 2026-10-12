"""SQLite metrics store: runs, cycles and measurements.

ZERO external deps (stdlib sqlite3). Percentiles are computed in Python from
the stored values so they are exact for small soak samples.
"""

from __future__ import annotations

import json
import sqlite3
import threading
from collections.abc import Iterable
from datetime import UTC, datetime
from pathlib import Path

SCHEMA = """
CREATE TABLE IF NOT EXISTS runs (
    run_id      INTEGER PRIMARY KEY AUTOINCREMENT,
    kind        TEXT NOT NULL,
    started_at  TEXT NOT NULL,
    finished_at TEXT,
    git_sha     TEXT,
    notes       TEXT
);
CREATE TABLE IF NOT EXISTS cycles (
    cycle_id    INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id      INTEGER NOT NULL,
    cycle_index INTEGER NOT NULL,
    clean_boot  INTEGER,
    boot_ms     REAL,
    flash_ms    REAL,
    fault_spec  TEXT
);
CREATE TABLE IF NOT EXISTS measurements (
    measurement_id INTEGER PRIMARY KEY AUTOINCREMENT,
    run_id         INTEGER NOT NULL,
    cycle_index    INTEGER,
    metric         TEXT NOT NULL,
    value          REAL NOT NULL,
    unit           TEXT,
    tags           TEXT,
    ts             TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS idx_meas_metric ON measurements(metric);
CREATE INDEX IF NOT EXISTS idx_meas_run ON measurements(run_id);
CREATE INDEX IF NOT EXISTS idx_cycles_run ON cycles(run_id);
"""


def _utcnow() -> str:
    return datetime.now(UTC).isoformat(timespec="seconds")


def _percentile(values: list[float], q: float) -> float:
    if not values:
        return 0.0
    ordered = sorted(values)
    if len(ordered) == 1:
        return ordered[0]
    idx = q * (len(ordered) - 1)
    lo = int(idx)
    hi = min(lo + 1, len(ordered) - 1)
    return ordered[lo] + (ordered[hi] - ordered[lo]) * (idx - lo)


class MetricsStore:
    def __init__(self, path: str | Path = "results/hil.db") -> None:
        self.path = str(path)
        Path(self.path).parent.mkdir(parents=True, exist_ok=True)
        self._lock = threading.Lock()
        self.conn = sqlite3.connect(self.path, check_same_thread=False)
        self.conn.row_factory = sqlite3.Row
        self.conn.executescript(SCHEMA)
        self.conn.commit()

    # -- lifecycle -------------------------------------------------------
    def close(self) -> None:
        with self._lock:
            self.conn.close()

    def __enter__(self) -> MetricsStore:
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    # -- runs ------------------------------------------------------------
    def new_run(self, kind: str, notes: str = "", git_sha: str | None = None) -> int:
        with self._lock:
            cur = self.conn.execute(
                "INSERT INTO runs(kind, started_at, git_sha, notes) VALUES (?,?,?,?)",
                (kind, _utcnow(), git_sha, notes),
            )
            self.conn.commit()
            return int(cur.lastrowid or 0)

    def finish_run(self, run_id: int) -> None:
        with self._lock:
            self.conn.execute("UPDATE runs SET finished_at=? WHERE run_id=?", (_utcnow(), run_id))
            self.conn.commit()

    def last_run_id(self, kind: str | None = None) -> int | None:
        with self._lock:
            if kind:
                row = self.conn.execute(
                    "SELECT run_id FROM runs WHERE kind=? AND finished_at IS NOT NULL "
                    "ORDER BY run_id DESC LIMIT 1",
                    (kind,),
                ).fetchone()
            else:
                row = self.conn.execute(
                    "SELECT run_id FROM runs WHERE finished_at IS NOT NULL "
                    "ORDER BY run_id DESC LIMIT 1"
                ).fetchone()
        if row is None:
            return None
        run_id = row["run_id"]
        return int(run_id) if run_id is not None else None

    def run_info(self, run_id: int) -> dict:
        with self._lock:
            row = self.conn.execute("SELECT * FROM runs WHERE run_id=?", (run_id,)).fetchone()
        return dict(row) if row else {}

    # -- records ---------------------------------------------------------
    def record_measurement(
        self,
        run_id: int,
        metric: str,
        value: float,
        unit: str = "",
        tags: dict | None = None,
        cycle_index: int | None = None,
    ) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO measurements(run_id, cycle_index, metric, value, unit, tags, ts) "
                "VALUES (?,?,?,?,?,?,?)",
                (
                    run_id,
                    cycle_index,
                    metric,
                    float(value),
                    unit,
                    json.dumps(tags or {}),
                    _utcnow(),
                ),
            )
            self.conn.commit()

    def record_cycle(
        self,
        run_id: int,
        cycle_index: int,
        clean_boot: bool,
        boot_ms: float | None = None,
        flash_ms: float | None = None,
        fault_spec: str = "",
    ) -> None:
        with self._lock:
            self.conn.execute(
                "INSERT INTO cycles(run_id, cycle_index, clean_boot, boot_ms, flash_ms, fault_spec) "
                "VALUES (?,?,?,?,?,?)",
                (run_id, cycle_index, int(bool(clean_boot)), boot_ms, flash_ms, fault_spec),
            )
            self.conn.commit()

    # -- queries ---------------------------------------------------------
    def values(self, metric: str, run_id: int | None = None) -> list[float]:
        with self._lock:
            if run_id is None:
                rows = self.conn.execute(
                    "SELECT value FROM measurements WHERE metric=? ORDER BY measurement_id",
                    (metric,),
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT value FROM measurements WHERE metric=? AND run_id=? "
                    "ORDER BY measurement_id",
                    (metric, run_id),
                ).fetchall()
        return [float(r["value"]) for r in rows]

    def stats(self, metric: str, run_id: int | None = None) -> dict:
        vals = self.values(metric, run_id)
        if not vals:
            return {
                "metric": metric,
                "count": 0,
                "mean": 0.0,
                "stddev": 0.0,
                "min": 0.0,
                "max": 0.0,
                "p50": 0.0,
                "p95": 0.0,
                "p99": 0.0,
            }
        mean = sum(vals) / len(vals)
        var = sum((v - mean) ** 2 for v in vals) / len(vals)
        return {
            "metric": metric,
            "count": len(vals),
            "mean": mean,
            "stddev": var**0.5,
            "min": min(vals),
            "max": max(vals),
            "p50": _percentile(vals, 0.50),
            "p95": _percentile(vals, 0.95),
            "p99": _percentile(vals, 0.99),
        }

    def tag_values(self, metric: str, tag_key: str, run_id: int | None = None) -> dict:
        """Return {tag_value: stats} grouped by a tag key (e.g. driver name)."""
        sql = "SELECT tags, value FROM measurements WHERE metric=?"
        params: list = [metric]
        if run_id is not None:
            sql += " AND run_id=?"
            params.append(run_id)
        with self._lock:
            rows = self.conn.execute(sql, params).fetchall()
        groups: dict[str, list[float]] = {}
        for r in rows:
            tags = json.loads(r["tags"] or "{}")
            key = str(tags.get(tag_key, "unknown"))
            groups.setdefault(key, []).append(float(r["value"]))
        return {k: self.stats_from(v) for k, v in groups.items()}

    @staticmethod
    def stats_from(vals: Iterable[float]) -> dict:
        vals = list(vals)
        if not vals:
            return {"count": 0, "p50": 0.0, "p95": 0.0, "mean": 0.0, "max": 0.0}
        mean = sum(vals) / len(vals)
        return {
            "count": len(vals),
            "p50": _percentile(vals, 0.50),
            "p95": _percentile(vals, 0.95),
            "mean": mean,
            "max": max(vals),
        }

    def cycle_summary(self, run_id: int) -> dict:
        with self._lock:
            rows = self.conn.execute(
                "SELECT * FROM cycles WHERE run_id=? ORDER BY cycle_index", (run_id,)
            ).fetchall()
        cycles = [dict(r) for r in rows]
        clean = sum(1 for c in cycles if c["clean_boot"])
        boots = [c["boot_ms"] for c in cycles if c["boot_ms"] is not None and c["clean_boot"]]
        return {
            "cycles": len(cycles),
            "clean": clean,
            "clean_rate": round(100.0 * clean / len(cycles), 2) if cycles else 0.0,
            "boot_p50": _percentile(boots, 0.50) if boots else 0.0,
            "boot_p95": _percentile(boots, 0.95) if boots else 0.0,
            "boot_max": max(boots) if boots else 0.0,
            "items": cycles,
        }

    def metric_names(self, run_id: int | None = None) -> list[str]:
        with self._lock:
            if run_id is None:
                rows = self.conn.execute(
                    "SELECT DISTINCT metric FROM measurements ORDER BY metric"
                ).fetchall()
            else:
                rows = self.conn.execute(
                    "SELECT DISTINCT metric FROM measurements WHERE run_id=? ORDER BY metric",
                    (run_id,),
                ).fetchall()
        return [r["metric"] for r in rows]
