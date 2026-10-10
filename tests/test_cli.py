"""
Tests for CLI commands and argument parsing.
"""

import contextlib
import io
import tempfile
import unittest
from pathlib import Path

from tests.synthetic import write_synthetic_video
from xbake.cli import main


class TestCLI(unittest.TestCase):
    def test_version(self):
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
            main(["--version"])
        self.assertEqual(cm.exception.code, 0)

    def test_help(self):
        with self.assertRaises(SystemExit) as cm, contextlib.redirect_stdout(io.StringIO()):
            main(["--help"])
        self.assertEqual(cm.exception.code, 0)

    def test_init_config(self):
        with tempfile.TemporaryDirectory() as tmp:
            out = Path(tmp) / "config.json"
            self.assertEqual(main(["init-config", "-o", str(out)]), 0)
            self.assertTrue(out.exists())
            # Refuses to overwrite without --force
            self.assertEqual(main(["init-config", "-o", str(out)]), 1)
            self.assertEqual(main(["init-config", "-o", str(out), "--force"]), 0)

    def test_benchmark_and_export(self):
        with tempfile.TemporaryDirectory() as tmp:
            video = write_synthetic_video(Path(tmp) / "belt.avi")
            db = Path(tmp) / "bench.db"
            cfg = Path(tmp) / "config.json"
            out = io.StringIO()
            with contextlib.redirect_stdout(out):
                code = main(["benchmark", str(video), "-c", str(cfg), "--db", str(db)])
            self.assertEqual(code, 0)
            report = out.getvalue()
            self.assertIn("BENCHMARK RESULTS", report)
            self.assertIn("Burnt                  : 1", report)
            self.assertIn("Good                   : 1", report)
            self.assertNotIn("delayed", report)

            csv_path = Path(tmp) / "export.csv"
            self.assertEqual(main(["export", "-c", str(cfg), "--db", str(db), "-o", str(csv_path)]), 0)
            rows = csv_path.read_text().strip().splitlines()
            self.assertEqual(len(rows), 3)  # header + 2 items

    def test_benchmark_missing_video(self):
        self.assertEqual(main(["benchmark", "does-not-exist.mp4"]), 1)


if __name__ == "__main__":
    unittest.main()
