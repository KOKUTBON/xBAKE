"""
Euclidean centroid tracker with velocity estimation and disappearance timeout.
"""

import math
from typing import Dict, List, Tuple

from xbake.models import DefectType, DetectedItem
from xbake.vision.detector import RawDetection


class CentroidTracker:
    """
    Multi-object tracker associating conveyor detections across successive frames.
    Tracks that time out are moved to a "finished" queue so callers can record
    the final verdict for every product.
    """

    def __init__(self, max_disappeared: int = 15, distance_tolerance_factor: float = 1.3):
        self.max_disappeared = max_disappeared
        self.distance_tolerance_factor = distance_tolerance_factor
        self.next_object_id = 1
        self.objects: Dict[int, DetectedItem] = {}
        self.disappeared: Dict[int, int] = {}
        self.velocity: Dict[int, Tuple[float, float]] = {}  # (dx, dy) per frame
        self._finished: List[DetectedItem] = []

    def register(self, detection: RawDetection, defect: DefectType, confidence: float) -> DetectedItem:
        """Register a new item track."""
        item = DetectedItem(
            id=self.next_object_id,
            center=detection.center,
            radius=detection.radius,
            bbox=detection.bbox,
            defect=defect,
            confidence=confidence,
            avg_color=detection.avg_color,
            circularity=detection.circularity,
            aspect_ratio=detection.aspect_ratio,
        )
        if defect.is_defective:
            item.consecutive_defect_frames = 1

        self.objects[self.next_object_id] = item
        self.disappeared[self.next_object_id] = 0
        self.velocity[self.next_object_id] = (0.0, 0.0)
        self.next_object_id += 1
        return item

    def deregister(self, object_id: int) -> None:
        """Close a track that has left the camera field of view."""
        item = self.objects.pop(object_id, None)
        if item is not None:
            self._finished.append(item)
        self.disappeared.pop(object_id, None)
        self.velocity.pop(object_id, None)

    def pop_finished(self) -> List[DetectedItem]:
        """Return and clear the list of closed tracks."""
        finished, self._finished = self._finished, []
        return finished

    def flush(self) -> List[DetectedItem]:
        """Close all active tracks (e.g. at end of video) and return every closed track."""
        for obj_id in list(self.objects.keys()):
            self.deregister(obj_id)
        return self.pop_finished()

    def _mark_missing(self, obj_id: int) -> None:
        self.disappeared[obj_id] += 1
        if self.disappeared[obj_id] >= self.max_disappeared:
            self.deregister(obj_id)

    def update(
        self,
        raw_detections: List[RawDetection],
        defect_classifications: List[Tuple[DefectType, float]],
    ) -> List[DetectedItem]:
        """
        Updates tracked objects with new raw detections and classifications.
        Returns list of currently active tracked items.
        """
        if len(raw_detections) == 0:
            for obj_id in list(self.disappeared.keys()):
                self._mark_missing(obj_id)
            return list(self.objects.values())

        if len(self.objects) == 0:
            for det, (defect, conf) in zip(raw_detections, defect_classifications, strict=True):
                self.register(det, defect, conf)
            return list(self.objects.values())

        # Greedy nearest-neighbour matching of existing tracks to detections
        object_ids = list(self.objects.keys())
        matches = []
        for row, obj_id in enumerate(object_ids):
            item = self.objects[obj_id]
            max_dist = max(30.0, item.radius * self.distance_tolerance_factor)
            for col, det in enumerate(raw_detections):
                dist = math.hypot(item.center[0] - det.center[0], item.center[1] - det.center[1])
                if dist <= max_dist:
                    matches.append((dist, row, col, obj_id))
        matches.sort(key=lambda m: m[0])

        used_rows = set()
        used_cols = set()
        for _, row, col, obj_id in matches:
            if row in used_rows or col in used_cols:
                continue

            item = self.objects[obj_id]
            det = raw_detections[col]
            defect, conf = defect_classifications[col]

            prev_center = item.center
            item.update_position(det.center, det.radius, det.bbox)
            item.avg_color = det.avg_color
            item.circularity = det.circularity
            item.aspect_ratio = det.aspect_ratio
            # Once rejected, keep the verdict that triggered the rejection
            if not item.rejected:
                item.defect = defect
                item.confidence = conf
                if defect.is_defective:
                    item.consecutive_defect_frames += 1
                else:
                    item.consecutive_defect_frames = 0

            self.velocity[obj_id] = (
                float(det.center[0] - prev_center[0]),
                float(det.center[1] - prev_center[1]),
            )
            self.disappeared[obj_id] = 0
            used_rows.add(row)
            used_cols.add(col)

        for row in set(range(len(object_ids))) - used_rows:
            self._mark_missing(object_ids[row])

        for col in set(range(len(raw_detections))) - used_cols:
            defect, conf = defect_classifications[col]
            self.register(raw_detections[col], defect, conf)

        return list(self.objects.values())
