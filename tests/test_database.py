"""
Tests for local SQLite database operations.
"""

import tempfile
import unittest
from pathlib import Path

from xbake.models import BoundingBox, DefectType, DetectedItem, ProductionMetrics
from xbake.storage.database import Database


class TestDatabase(unittest.TestCase):
    def test_database_lifecycle(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            db_file = str(Path(tmp_dir) / "test.db")
            db = Database(db_file)

            # Insert test inspection
            item = DetectedItem(
                id=42,
                center=(150, 150),
                radius=40.0,
                bbox=BoundingBox(110, 110, 80, 80),
                defect=DefectType.BURNT,
                confidence=0.92,
                avg_color=45.0,
                circularity=0.88,
                aspect_ratio=0.95,
                rejected=True,
            )
            db.insert_inspection(item)

            # Retrieve recent
            recent = db.get_recent_inspections(limit=10)
            self.assertEqual(len(recent), 1)
            self.assertEqual(recent[0]["item_id"], 42)
            self.assertEqual(recent[0]["defect_type"], "burnt")
            self.assertEqual(recent[0]["rejected"], 1)

            # Unsynced queue
            unsynced = db.get_unsynced_inspections()
            self.assertEqual(len(unsynced), 1)

            # Mark synced
            db.mark_as_synced([recent[0]["id"]])
            self.assertEqual(len(db.get_unsynced_inspections()), 0)

            # Insert telemetry
            metrics = ProductionMetrics(total_good=95, total_burnt=5)
            db.insert_telemetry(metrics)

            # Summary and CSV export
            summary = db.get_summary()
            self.assertEqual(summary["total_records"], 1)
            self.assertEqual(summary["by_defect_type"], {"burnt": 1})
            csv_text = db.export_csv_string()
            lines = csv_text.strip().splitlines()
            self.assertEqual(len(lines), 2)
            self.assertTrue(lines[0].startswith("datetime,id,item_id"))
            self.assertIn(",burnt,", lines[1])

    def test_migrates_legacy_schema(self):
        import sqlite3

        with tempfile.TemporaryDirectory() as tmp_dir:
            db_file = str(Path(tmp_dir) / "old.db")
            conn = sqlite3.connect(db_file)
            conn.execute(
                "CREATE TABLE inspections (id INTEGER PRIMARY KEY AUTOINCREMENT, item_id INTEGER NOT NULL, "
                "timestamp REAL NOT NULL, defect_type TEXT NOT NULL, confidence REAL NOT NULL, "
                "avg_color REAL NOT NULL, circularity REAL NOT NULL, aspect_ratio REAL NOT NULL, "
                "rejected INTEGER NOT NULL, snapshot_path TEXT, synced INTEGER DEFAULT 0)"
            )
            conn.commit()
            conn.close()
            db = Database(db_file)
            item = DetectedItem(id=1, center=(0, 0), radius=10.0, bbox=BoundingBox(0, 0, 20, 20))
            db.insert_inspection(item)
            self.assertEqual(db.get_recent_inspections()[0]["radius_px"], 10.0)


if __name__ == "__main__":
    unittest.main()
