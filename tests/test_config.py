"""
Tests for configuration persistence and validated runtime updates.
"""

import json
import os
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from xbake.config import AppConfig, ConfigError, load_config


class TestConfig(unittest.TestCase):
    def test_default_config(self):
        cfg = AppConfig()
        self.assertEqual(cfg.vision.min_radius, 30)
        self.assertEqual(cfg.inspection.M_color, 95)
        self.assertEqual(cfg.hardware.gpio_pin, 21)
        self.assertEqual(cfg.web.port, 8000)
        self.assertEqual(cfg.web.host, "127.0.0.1")

    def test_save_load_roundtrip(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg_file = Path(tmp_dir) / "config.json"
            cfg = AppConfig()
            cfg.vision.min_radius = 42
            cfg.vision.morph_op = (7, 7)
            cfg.inspection.M_color = 110
            cfg.save(cfg_file)

            loaded = AppConfig.load(cfg_file)
            self.assertEqual(loaded.vision.min_radius, 42)
            self.assertEqual(loaded.vision.morph_op, (7, 7))
            self.assertEqual(loaded.inspection.M_color, 110)
            self.assertEqual(loaded.path, str(cfg_file))

    def test_unknown_keys_are_ignored(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            cfg_file = Path(tmp_dir) / "config.json"
            cfg_file.write_text(json.dumps({"vision": {"min_radius": 12, "legacy_key": 1}, "extra": {}}))
            with self.assertLogs("xbake.config", level="WARNING"):
                loaded = AppConfig.load(cfg_file)
            self.assertEqual(loaded.vision.min_radius, 12)

    def test_missing_file_gives_defaults_bound_to_path(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = str(Path(tmp_dir) / "new.json")
            cfg = load_config(target)
            self.assertEqual(cfg.path, target)
            cfg.save()
            self.assertTrue(Path(target).exists())

    def test_apply_updates_validates_and_coerces(self):
        cfg = AppConfig()
        applied = cfg.apply_updates(
            {
                "inspection": {"M_color": "100", "min_circularity": 0.5},
                "vision": {"lower_hsv": [10, 20, 30], "blur": 7},
            }
        )
        self.assertEqual(cfg.inspection.M_color, 100)
        self.assertEqual(cfg.inspection.min_circularity, 0.5)
        self.assertEqual(cfg.vision.lower_hsv, [10, 20, 30])
        self.assertEqual(cfg.vision.blur, (7, 7))
        self.assertEqual(applied["vision"]["blur"], [7, 7])

    def test_apply_updates_is_atomic(self):
        cfg = AppConfig()
        with self.assertRaises(ConfigError):
            cfg.apply_updates({"inspection": {"M_color": 50, "min_circularity": 5.0}})
        self.assertEqual(cfg.inspection.M_color, 95)

    def test_apply_updates_rejects_non_tunable(self):
        cfg = AppConfig()
        for bad in (
            {"storage": {"db_path": "/etc/passwd"}},
            {"web": {"auth_token": "x"}},
            {"vision": {"norm_resolution": 10}},
            {"vision": {"upper_hsv": [200, 0, 0]}},
            {"inspection": {"M_color": True}},
        ):
            with self.assertRaises(ConfigError, msg=str(bad)):
                cfg.apply_updates(bad)

    def test_env_token_is_not_persisted(self):
        with tempfile.TemporaryDirectory() as tmp_dir:
            target = Path(tmp_dir) / "config.json"
            with patch.dict(os.environ, {"XBAKE_WEB_TOKEN": "env-token-123"}):
                cfg = load_config(str(target))
            self.assertEqual(cfg.effective_auth_token, "env-token-123")
            cfg.save()
            self.assertEqual(json.loads(target.read_text())["web"]["auth_token"], "")
            self.assertEqual(cfg.to_dict(include_secrets=False)["web"]["auth_token"], "")


if __name__ == "__main__":
    unittest.main()
