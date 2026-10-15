"""
Tests for multi-object centroid tracker.
"""

import unittest

from xbake.models import BoundingBox, DefectType
from xbake.vision.detector import RawDetection
from xbake.vision.tracker import CentroidTracker


def make_raw_det(center=(100, 100), radius=50.0, avg_color=120.0, circularity=0.9):
    return RawDetection(
        center=center,
        radius=radius,
        bbox=BoundingBox(x=center[0] - 25, y=center[1] - 25, w=50, h=50),
        circularity=circularity,
        aspect_ratio=1.0,
        avg_color=avg_color,
        center_roi=None,
        contour=None,
    )


class TestTracker(unittest.TestCase):
    def test_tracker_registration(self):
        tracker = CentroidTracker(max_disappeared=5)
        det1 = make_raw_det((100, 100))
        items = tracker.update([det1], [(DefectType.GOOD, 0.95)])

        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, 1)
        self.assertEqual(items[0].center, (100, 100))

    def test_tracker_movement_and_velocity(self):
        tracker = CentroidTracker(max_disappeared=5)

        # Frame 1
        tracker.update([make_raw_det((100, 100))], [(DefectType.GOOD, 0.95)])

        # Frame 2: moved 10px in X direction
        items = tracker.update([make_raw_det((110, 100))], [(DefectType.GOOD, 0.95)])
        self.assertEqual(len(items), 1)
        self.assertEqual(items[0].id, 1)
        self.assertEqual(items[0].center, (110, 100))
        self.assertEqual(tracker.velocity[1], (10.0, 0.0))

    def test_tracker_deregistration(self):
        tracker = CentroidTracker(max_disappeared=3)
        tracker.update([make_raw_det((100, 100))], [(DefectType.GOOD, 0.95)])
        self.assertIn(1, tracker.objects)

        # 3 frames without detection
        tracker.update([], [])
        tracker.update([], [])
        tracker.update([], [])

        self.assertNotIn(1, tracker.objects)


if __name__ == "__main__":
    unittest.main()
