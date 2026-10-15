"""
Background worker that mirrors local inspection records to Google Sheets.
Records stay in SQLite until the upload succeeds, so the line keeps running offline.
"""

import logging
import threading
import time
from pathlib import Path
from typing import Optional

from xbake.config import StorageConfig
from xbake.storage.database import Database

logger = logging.getLogger(__name__)

SHEET_HEADER = ["datetime", "item_id", "defect_type", "confidence", "avg_color", "circularity", "result"]


class CloudSyncWorker:
    """
    Periodically appends unsynced inspection records to a Google Sheet
    without blocking the camera inspection loop.
    """

    def __init__(self, config: StorageConfig, database: Database):
        self.config = config
        self.db = database
        self._running = False
        self._thread: Optional[threading.Thread] = None
        self._gsheet_client = None
        self._sheet = None

    def start(self) -> "CloudSyncWorker":
        self._running = True
        self._thread = threading.Thread(target=self._worker_loop, name="CloudSyncWorker", daemon=True)
        self._thread.start()
        logger.info("CloudSyncWorker background thread started.")
        return self

    def stop(self) -> None:
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.0)
        logger.info("CloudSyncWorker stopped.")

    def _init_gsheet(self) -> bool:
        creds_path = self.config.google_creds_json
        if not Path(creds_path).exists():
            logger.warning(f"Google credentials '{creds_path}' not found. Skipping Google Sheets sync.")
            return False

        try:
            import gspread
            from google.oauth2.service_account import Credentials

            scopes = [
                "https://www.googleapis.com/auth/spreadsheets",
                "https://www.googleapis.com/auth/drive",
            ]
            creds = Credentials.from_service_account_file(creds_path, scopes=scopes)
            self._gsheet_client = gspread.authorize(creds)
            spreadsheet = self._gsheet_client.open(self.config.google_sheet_name)
            self._sheet = spreadsheet.sheet1
            if not self._sheet.row_values(1):
                self._sheet.append_row(SHEET_HEADER)
            logger.info(f"Connected to Google Sheet: {self.config.google_sheet_name}")
            return True
        except Exception as e:
            logger.warning(f"Could not connect to Google Sheets ({e}). Will retry later.")
            return False

    def _worker_loop(self) -> None:
        while self._running:
            try:
                self.sync_once()
            except Exception as e:
                logger.error(f"Error in sync worker loop: {e}")

            # Sleep in small increments for responsive shutdown
            for _ in range(self.config.sync_interval_seconds):
                if not self._running:
                    break
                time.sleep(1.0)

    def sync_once(self) -> None:
        """Single sync pass."""
        unsynced = self.db.get_unsynced_inspections(limit=50)
        if not unsynced:
            return

        logger.info(f"Syncing {len(unsynced)} inspection events to remote storage...")

        # Sync to Google Sheets if possible
        synced_ids = []
        if self._sheet is None:
            self._init_gsheet()

        if self._sheet is not None:
            try:
                rows_to_insert = []
                for rec in unsynced:
                    dt = time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(rec["timestamp"]))
                    rows_to_insert.append(
                        [
                            dt,
                            rec["item_id"],
                            rec["defect_type"],
                            rec["confidence"],
                            rec["avg_color"],
                            rec["circularity"],
                            "REJECTED" if rec["rejected"] else "PASSED",
                        ]
                    )

                if rows_to_insert:
                    self._sheet.append_rows(rows_to_insert)
                    synced_ids = [rec["id"] for rec in unsynced]
                    logger.info(f"Successfully appended {len(rows_to_insert)} rows to Google Sheet")
            except Exception as e:
                logger.warning(f"Failed to append rows to Google Sheet: {e}")

        # Mark successfully transmitted records in local SQLite
        if synced_ids:
            self.db.mark_as_synced(synced_ids)
