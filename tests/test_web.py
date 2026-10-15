"""
Tests for the embedded web dashboard and HTTP API.
"""

import json
import tempfile
import time
import unittest
import urllib.error
import urllib.request
from pathlib import Path

from tests.synthetic import synthetic_config, synthetic_frames
from xbake.storage.database import Database
from xbake.vision.pipeline import InspectionPipeline
from xbake.web.app import WebDashboardServer


class _NoStream:
    source = "test"
    finished = False
    is_opened = True
    fps = 0.0
    settings_applied = 0

    def start(self):
        return self

    def stop(self):
        pass

    def read(self):
        return 0, None

    def apply_settings(self):
        self.settings_applied += 1


class TestWebDashboard(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        tmp = Path(self.tmp.name)
        self.cfg = synthetic_config()
        self.cfg.path = str(tmp / "config.json")
        self.cfg.storage.save_snapshots = True
        self.cfg.storage.snapshot_dir = str(tmp / "snaps")
        self.cfg.web.port = 0  # ephemeral port
        self.stream = _NoStream()
        self.pipeline = InspectionPipeline(self.cfg, stream=self.stream)
        self.db = Database(str(tmp / "test.db"))
        self.server = WebDashboardServer(self.pipeline, self.db, self.cfg).start()
        self.base = f"http://127.0.0.1:{self.server.port}"

        for frame in synthetic_frames():
            result, annotated = self.pipeline.process_frame(frame)
            for item in result.new_defects + result.passed_items:
                self.db.insert_inspection(item)
            self.server._last_encode = 0.0
            self.server.update_frame(annotated)

    def tearDown(self):
        self.server.stop()
        self.pipeline.stop()
        self.tmp.cleanup()

    def _get(self, path):
        with urllib.request.urlopen(self.base + path, timeout=5) as r:
            return r.status, r.headers, r.read()

    def _post(self, path, body=None, token=None):
        data = json.dumps(body or {}).encode()
        req = urllib.request.Request(self.base + path, data=data, method="POST")
        req.add_header("Content-Type", "application/json")
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        try:
            with urllib.request.urlopen(req, timeout=5) as r:
                return r.status, json.loads(r.read())
        except urllib.error.HTTPError as e:
            return e.code, json.loads(e.read())

    def test_dashboard_and_frames(self):
        status, headers, body = self._get("/")
        self.assertEqual(status, 200)
        self.assertIn(b"/logo.png", body)
        status, headers, body = self._get("/logo.png")
        self.assertEqual(headers["Content-Type"], "image/png")
        self.assertTrue(body.startswith(b"\x89PNG"))
        status, headers, body = self._get("/frame.jpg")
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        self.assertTrue(body.startswith(b"\xff\xd8"))
        status, headers, body = self._get("/frame.jpg?view=mask")
        self.assertTrue(body.startswith(b"\xff\xd8"))

    def test_mjpeg_stream_sends_a_frame(self):
        with urllib.request.urlopen(self.base + "/stream", timeout=5) as r:
            self.assertIn("multipart/x-mixed-replace", r.headers["Content-Type"])
            chunk = r.read(64)
        self.assertTrue(chunk.startswith(b"--frame"))

    def test_metrics_status_recent_summary(self):
        metrics = json.loads(self._get("/api/metrics")[2])
        self.assertEqual(metrics["total_inspected"], 2)
        status = json.loads(self._get("/api/status")[2])
        self.assertEqual(status["actuator"]["type"], "MockActuator")
        self.assertFalse(status["auth_required"])

        recent = json.loads(self._get("/api/recent?limit=10")[2])
        self.assertEqual(len(recent), 2)
        defects = json.loads(self._get("/api/recent?defects=1")[2])
        self.assertEqual([r["defect_type"] for r in defects], ["burnt"])
        summary = json.loads(self._get("/api/summary")[2])
        self.assertEqual(summary["total_records"], 2)

    def test_snapshot_endpoint(self):
        defect = json.loads(self._get("/api/recent?defects=1")[2])[0]
        status, headers, body = self._get(f"/api/snapshot/{defect['id']}")
        self.assertEqual(headers["Content-Type"], "image/jpeg")
        good = [r for r in json.loads(self._get("/api/recent")[2]) if not r["rejected"]][0]
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get(f"/api/snapshot/{good['id']}")
        self.assertEqual(cm.exception.code, 404)

    def test_csv_export(self):
        status, headers, body = self._get("/api/export.csv")
        self.assertIn("text/csv", headers["Content-Type"])
        self.assertIn("attachment", headers["Content-Disposition"])
        self.assertEqual(len(body.decode().strip().splitlines()), 3)

    def test_config_update_save_and_validation(self):
        cfg = json.loads(self._get("/api/config")[2])
        self.assertIn("inspection", cfg["tunable"])
        self.assertEqual(cfg["config"]["inspection"]["M_color"], 95)

        code, res = self._post("/api/config", {"inspection": {"M_color": 80}, "camera": {"contrast": 40}})
        self.assertEqual(code, 200)
        self.assertEqual(self.cfg.inspection.M_color, 80)
        self.assertEqual(self.stream.settings_applied, 1)

        code, res = self._post("/api/config", {"storage": {"db_path": "x"}})
        self.assertEqual(code, 400)
        self.assertIn("error", res)

        code, res = self._post("/api/config/save")
        self.assertEqual(code, 200)
        saved = json.loads(Path(self.cfg.path).read_text())
        self.assertEqual(saved["inspection"]["M_color"], 80)

    def _wait_actuator_idle(self):
        deadline = time.time() + 2.0
        while self.pipeline.actuator.is_active() and time.time() < deadline:
            time.sleep(0.02)

    def test_rejector_and_reset(self):
        self._wait_actuator_idle()
        code, res = self._post("/api/rejector/test")
        self.assertEqual(code, 200)
        self.assertTrue(res["triggered"])
        code, res = self._post("/api/metrics/reset")
        self.assertEqual(code, 200)
        self.assertEqual(self.pipeline.metrics.total_inspected, 0)

    def test_auth_token_required_for_writes(self):
        self.cfg.web.auth_token = "s3cret"
        code, _ = self._post("/api/rejector/test")
        self.assertEqual(code, 401)
        code, _ = self._post("/api/rejector/test", token="wrong")
        self.assertEqual(code, 401)
        self._wait_actuator_idle()
        code, _ = self._post("/api/rejector/test", token="s3cret")
        self.assertEqual(code, 200)
        # Token never leaks through the config endpoint
        cfg = json.loads(self._get("/api/config")[2])
        self.assertEqual(cfg["config"]["web"]["auth_token"], "***")

    def test_unknown_route(self):
        with self.assertRaises(urllib.error.HTTPError) as cm:
            self._get("/nope")
        self.assertEqual(cm.exception.code, 404)


if __name__ == "__main__":
    unittest.main()
