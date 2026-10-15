"""
End-to-end tests for the inspection pipeline on synthetic frames.
"""

import tempfile
import unittest
from pathlib import Path

from tests.synthetic import synthetic_config, synthetic_frames
from xbake.models import DefectType
from xbake.vision.pipeline import InspectionPipeline


class _NoStream:
    source = "test"
    finished = False
    is_opened = True
    fps = 0.0

    def start(self):
        return self

    def stop(self):
        pass

    def read(self):
        return 0, None


class TestPipeline(unittest.TestCase):
    def _run(self, cfg):
        pipeline = InspectionPipeline(cfg, stream=_NoStream())
        rejected, passed = [], []
        for frame in synthetic_frames():
            result, annotated = pipeline.process_frame(frame)
            self.assertEqual(annotated.shape, frame.shape)
            rejected += result.new_defects
            passed += result.passed_items
        passed += pipeline.flush()
        pipeline.stop()
        return pipeline, rejected, passed

    def test_detects_and_rejects_burnt_item(self):
        with tempfile.TemporaryDirectory() as tmp:
            cfg = synthetic_config()
            cfg.storage.save_snapshots = True
            cfg.storage.snapshot_dir = str(Path(tmp) / "snaps")
            pipeline, rejected, passed = self._run(cfg)

            self.assertEqual(len(rejected), 1)
            self.assertEqual(rejected[0].defect, DefectType.BURNT)
            self.assertTrue(rejected[0].rejected)
            self.assertTrue(Path(rejected[0].snapshot_path).is_file())

            self.assertEqual(len(passed), 1)
            self.assertEqual(passed[0].defect, DefectType.GOOD)

            m = pipeline.metrics
            self.assertEqual(m.total_inspected, 2)
            self.assertEqual(m.total_good, 1)
            self.assertEqual(m.total_burnt, 1)
            self.assertEqual(m.defect_rate_pct, 50.0)
            self.assertEqual(len(pipeline.actuator.trigger_history), 1)
            self.assertIsNotNone(pipeline.latest_mask_view)

    def test_runtime_threshold_change_takes_effect(self):
        cfg = synthetic_config()
        # Burnt window below the burnt disk's intensity: nothing is burnt any more
        cfg.apply_updates({"inspection": {"M_color": 20}})
        pipeline, rejected, passed = self._run(cfg)
        self.assertEqual(rejected, [])
        self.assertEqual(pipeline.metrics.total_good, 2)

    def test_actuate_false_records_without_pulses(self):
        cfg = synthetic_config()
        pipeline = InspectionPipeline(cfg, stream=_NoStream(), actuate=False)
        for frame in synthetic_frames():
            pipeline.process_frame(frame)
        pipeline.flush()
        self.assertEqual(pipeline.metrics.total_burnt, 1)
        self.assertEqual(pipeline.actuator.trigger_history, [])

    def test_overlay_skips_tracks_missing_from_frame(self):
        cfg = synthetic_config()
        pipeline = InspectionPipeline(cfg, stream=_NoStream())
        frames = list(synthetic_frames(moving_frames=10, empty_frames=0))
        for frame in frames:
            pipeline.process_frame(frame)
        empty = frames[0].copy()
        empty[:] = frames[0][0, 0]  # belt only: tracks stay open but nothing is visible
        _, annotated = pipeline.process_frame(empty)
        self.assertTrue(pipeline.tracker.objects)  # tracks still open
        # Below the HUD bar the annotated frame must contain only the ROI box, no item circles
        green = (annotated[45:, :, 1] > 150) & (annotated[45:, :, 2] < 100) & (annotated[45:, :, 0] < 100)
        self.assertFalse(green.any())


if __name__ == "__main__":
    unittest.main()
