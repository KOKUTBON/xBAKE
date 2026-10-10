"""
Tests for geometric classification and defect detection logic.
"""

import unittest

import cv2
import numpy as np

from xbake.config import InspectionConfig, VisionConfig
from xbake.models import BoundingBox, DefectType
from xbake.vision.classifier import ItemClassifier
from xbake.vision.detector import ItemDetector, RawDetection


def _create_det(avg_color=120.0, circularity=0.90, aspect_ratio=0.95):
    return RawDetection(
        center=(50, 50),
        radius=25.0,
        bbox=BoundingBox(25, 25, 50, 50),
        circularity=circularity,
        aspect_ratio=aspect_ratio,
        avg_color=avg_color,
        center_roi=None,
        contour=None,
    )


class TestClassifier(unittest.TestCase):
    def test_classify_good(self):
        cfg = InspectionConfig(m_color=0, M_color=95, undercooked_min_color=195, min_circularity=0.65)
        classifier = ItemClassifier(cfg)

        det = _create_det(avg_color=140.0, circularity=0.90)
        defect, conf = classifier.classify(det)
        self.assertEqual(defect, DefectType.GOOD)
        self.assertGreaterEqual(conf, 0.9)

    def test_classify_burnt(self):
        cfg = InspectionConfig(m_color=0, M_color=95)
        classifier = ItemClassifier(cfg)

        det = _create_det(avg_color=45.0, circularity=0.90)
        defect, conf = classifier.classify(det)
        self.assertEqual(defect, DefectType.BURNT)
        self.assertGreater(conf, 0.6)

    def test_classify_undercooked(self):
        cfg = InspectionConfig(undercooked_min_color=195)
        classifier = ItemClassifier(cfg)

        det = _create_det(avg_color=210.0, circularity=0.90)
        defect, conf = classifier.classify(det)
        self.assertEqual(defect, DefectType.UNDERCOOKED)

    def test_classify_deformed(self):
        cfg = InspectionConfig(min_circularity=0.70)
        classifier = ItemClassifier(cfg)

        det = _create_det(avg_color=140.0, circularity=0.52)
        defect, conf = classifier.classify(det)
        self.assertEqual(defect, DefectType.DEFORMED)


class TestDetector(unittest.TestCase):
    def _frame_with_disk(self, center, radius=50, jagged=True):
        rng = np.random.default_rng(0)
        frame = np.full((480, 640, 3), 15, np.uint8)
        pts = []
        for a in np.linspace(0, 2 * np.pi, 64, endpoint=False):
            r = radius * (1 + (rng.normal(0, 0.03) if jagged else 0))
            pts.append((center[0] + r * np.cos(a), center[1] + r * np.sin(a)))
        cv2.fillPoly(frame, [np.array(pts, np.int32)], (100, 160, 210))
        return frame

    def test_blurred_mask_is_rebinarized(self):
        cfg = VisionConfig(lower_hsv=[0, 0, 50], filter_mode=0)
        detector = ItemDetector(cfg)
        dets, _, mask, _ = detector.process_frame(self._frame_with_disk((320, 240)))
        self.assertEqual(set(np.unique(mask)) - {0, 255}, set())
        self.assertEqual(len(dets), 1)
        self.assertGreater(dets[0].circularity, 0.8)

    def test_items_cut_by_roi_border_are_ignored(self):
        cfg = VisionConfig(lower_hsv=[0, 0, 50])
        detector = ItemDetector(cfg)
        dets, _, _, _ = detector.process_frame(self._frame_with_disk((20, 240)))
        self.assertEqual(dets, [])


if __name__ == "__main__":
    unittest.main()
