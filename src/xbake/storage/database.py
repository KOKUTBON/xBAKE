"""
Local-first SQLite persistence for inspection records and production telemetry.
"""

import csv
import io
import logging
import sqlite3
import time
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Dict, Iterator, List, Optional, TextIO

from xbake.models import DetectedItem, ProductionMetrics

logger = logging.getLogger(__name__)

INSPECTION_COLUMNS = [
    "id",
    "item_id",
    "timestamp",
    "defect_type",
    "confidence",
    "avg_color",
    "circularity",
    "aspect_ratio",
    "radius_px",
    "frames_seen",
    "first_seen_ts",
    "last_seen_ts",
    "rejected",
    "snapshot_path",
    "session_id",
    "synced",
]

# Columns added after the first schema version: name -> SQL type/default
_MIGRATION_COLUMNS = {
    "radius_px": "REAL DEFAULT 0",
    "frames_seen": "INTEGER DEFAULT 0",
    "first_seen_ts": "REAL",
    "last_seen_ts": "REAL",
    "session_id": "TEXT",
}


class Database:
    """
    SQLite store for every inspected product (passed and rejected) and periodic
    telemetry snapshots. A new connection is opened per call, so it is safe to
    use from multiple threads.
    """

    def __init__(self, db_path: str = "data/xbake.db", session_id: Optional[str] = None):
        self.db_path = db_path
        self.session_id = session_id or time.strftime("%Y%m%dT%H%M%S")
        Path(self.db_path).parent.mkdir(parents=True, exist_ok=True)
        self._init_schema()

    @contextmanager
    def _connection(self):
        conn = sqlite3.connect(self.db_path, timeout=10.0)
        conn.row_factory = sqlite3.Row
        try:
            with conn:
                yield conn
        finally:
            conn.close()

    def _init_schema(self) -> None:
        with self._connection() as conn:
            conn.executescript(
                """
                CREATE TABLE IF NOT EXISTS inspections (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    item_id INTEGER NOT NULL,
                    timestamp REAL NOT NULL,
                    defect_type TEXT NOT NULL,
                    confidence REAL NOT NULL,
                    avg_color REAL NOT NULL,
                    circularity REAL NOT NULL,
                    aspect_ratio REAL NOT NULL,
                    radius_px REAL DEFAULT 0,
                    frames_seen INTEGER DEFAULT 0,
                    first_seen_ts REAL,
                    last_seen_ts REAL,
                    rejected INTEGER NOT NULL,
                    snapshot_path TEXT,
                    session_id TEXT,
                    synced INTEGER DEFAULT 0
                );

                CREATE TABLE IF NOT EXISTS telemetry_snapshots (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp REAL NOT NULL,
                    total_inspected INTEGER NOT NULL,
                    total_good INTEGER NOT NULL,
                    total_burnt INTEGER NOT NULL,
                    total_undercooked INTEGER NOT NULL,
                    total_deformed INTEGER NOT NULL,
                    defect_rate_pct REAL NOT NULL,
                    throughput_per_min REAL NOT NULL,
                    fps REAL NOT NULL,
                    synced INTEGER DEFAULT 0
                );

                CREATE INDEX IF NOT EXISTS idx_inspections_synced ON inspections(synced);
                CREATE INDEX IF NOT EXISTS idx_inspections_timestamp ON inspections(timestamp);
                """
            )
            existing = {row["name"] for row in conn.execute("PRAGMA table_info(inspections)")}
            for name, sql_type in _MIGRATION_COLUMNS.items():
                if name not in existing:
                    conn.execute(f"ALTER TABLE inspections ADD COLUMN {name} {sql_type}")

    def insert_inspection(self, item: DetectedItem) -> int:
        """Insert the final verdict for a tracked item. Returns the row id."""
        with self._connection() as conn:
            cur = conn.execute(
                """
                INSERT INTO inspections (
                    item_id, timestamp, defect_type, confidence, avg_color,
                    circularity, aspect_ratio, radius_px, frames_seen,
                    first_seen_ts, last_seen_ts, rejected, snapshot_path, session_id, synced
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    item.id,
                    time.time(),
                    item.defect.value,
                    float(item.confidence),
                    float(item.avg_color),
                    float(item.circularity),
                    float(item.aspect_ratio),
                    float(item.radius),
                    int(item.frames_seen),
                    item.first_seen_ts,
                    item.last_seen_ts,
                    1 if item.rejected else 0,
                    item.snapshot_path,
                    self.session_id,
                ),
            )
            return int(cur.lastrowid)

    def insert_telemetry(self, metrics: ProductionMetrics) -> None:
        """Record a periodic telemetry snapshot."""
        with self._connection() as conn:
            conn.execute(
                """
                INSERT INTO telemetry_snapshots (
                    timestamp, total_inspected, total_good, total_burnt,
                    total_undercooked, total_deformed, defect_rate_pct,
                    throughput_per_min, fps, synced
                ) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, 0)
                """,
                (
                    time.time(),
                    metrics.total_inspected,
                    metrics.total_good,
                    metrics.total_burnt,
                    metrics.total_undercooked,
                    metrics.total_deformed,
                    metrics.defect_rate_pct,
                    metrics.throughput_per_minute,
                    metrics.current_fps,
                ),
            )

    def get_recent_inspections(self, limit: int = 50, defects_only: bool = False) -> List[Dict[str, Any]]:
        """Fetch the most recent inspection records (newest first)."""
        where = "WHERE rejected = 1" if defects_only else ""
        with self._connection() as conn:
            cursor = conn.execute(
                f"SELECT * FROM inspections {where} ORDER BY id DESC LIMIT ?", (int(limit),)
            )
            return [dict(row) for row in cursor.fetchall()]

    def get_inspection(self, record_id: int) -> Optional[Dict[str, Any]]:
        with self._connection() as conn:
            row = conn.execute("SELECT * FROM inspections WHERE id = ?", (int(record_id),)).fetchone()
            return dict(row) if row else None

    def get_summary(self) -> Dict[str, Any]:
        """Totals per defect type across the whole database."""
        with self._connection() as conn:
            rows = conn.execute(
                "SELECT defect_type, rejected, COUNT(*) AS n FROM inspections GROUP BY defect_type, rejected"
            ).fetchall()
            span = conn.execute("SELECT MIN(timestamp), MAX(timestamp) FROM inspections").fetchone()
        by_type: Dict[str, int] = {}
        total = rejected = 0
        for row in rows:
            by_type[row["defect_type"]] = by_type.get(row["defect_type"], 0) + row["n"]
            total += row["n"]
            if row["rejected"]:
                rejected += row["n"]
        return {
            "total_records": total,
            "total_rejected": rejected,
            "by_defect_type": by_type,
            "first_timestamp": span[0],
            "last_timestamp": span[1],
        }

    def iter_inspections(
        self, since: Optional[float] = None, until: Optional[float] = None
    ) -> Iterator[Dict[str, Any]]:
        """Yield inspection records in chronological order, optionally filtered by time."""
        clauses, params = [], []
        if since is not None:
            clauses.append("timestamp >= ?")
            params.append(since)
        if until is not None:
            clauses.append("timestamp <= ?")
            params.append(until)
        where = f"WHERE {' AND '.join(clauses)}" if clauses else ""
        with self._connection() as conn:
            for row in conn.execute(f"SELECT * FROM inspections {where} ORDER BY id ASC", params):
                yield dict(row)

    def export_csv(self, out: TextIO, since: Optional[float] = None, until: Optional[float] = None) -> int:
        """Write inspection records as CSV (with an ISO timestamp column). Returns row count."""
        writer = csv.writer(out)
        writer.writerow(["datetime"] + INSPECTION_COLUMNS)
        count = 0
        for rec in self.iter_inspections(since, until):
            dt = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(rec["timestamp"]))
            writer.writerow([dt] + [rec.get(col) for col in INSPECTION_COLUMNS])
            count += 1
        return count

    def export_csv_string(self, since: Optional[float] = None, until: Optional[float] = None) -> str:
        buf = io.StringIO()
        self.export_csv(buf, since, until)
        return buf.getvalue()

    def get_unsynced_inspections(self, limit: int = 100) -> List[Dict[str, Any]]:
        """Fetch records waiting to be synchronized to Google Sheets."""
        with self._connection() as conn:
            cursor = conn.execute(
                "SELECT * FROM inspections WHERE synced = 0 ORDER BY id ASC LIMIT ?", (int(limit),)
            )
            return [dict(row) for row in cursor.fetchall()]

    def mark_as_synced(self, record_ids: List[int]) -> None:
        """Mark records as successfully synced."""
        if not record_ids:
            return
        with self._connection() as conn:
            conn.executemany(
                "UPDATE inspections SET synced = 1 WHERE id = ?",
                [(rid,) for rid in record_ids],
            )
