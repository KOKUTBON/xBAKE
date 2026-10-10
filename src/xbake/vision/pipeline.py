"""
End-to-end inspection pipeline: detection, classification, tracking, rejection, and overlays.
"""

import logging
import threading
import time
from pathlib import Path
from typing import List, Optional, Tuple

import cv2
import numpy as np

from xbake.config import AppConfig
from xbake.hardware.rejector import ConveyorRejector, create_actuator
from xbake.models import DefectType, DetectedItem, FrameInspectionResult, ProductionMetrics
from xbake.vision.camera import CameraStream
from xbake.vision.classifier import ItemClassifier
from xbake.vision.detector import ItemDetector
from xbake.vision.tracker import CentroidTracker

logger = logging.getLogger(__name__)

# BGR overlay colors
COLORS = {
    DefectType.GOOD: (0, 200, 0),
    DefectType.BURNT: (0, 0, 255),
    DefectType.UNDERCOOKED: (0, 220, 255),
    DefectType.DEFORMED: (0, 140, 255),
    DefectType.UNKNOWN: (200, 200, 200),
}


class InspectionPipeline:
    """
    Coordinates camera ingestion, detection, tracking, classification,
    actuation, and production metrics.
    """

    def __init__(self, config: AppConfig, stream: Optional[CameraStream] = None, actuate: bool = True):
        self.config = config
        # When False (offline analysis), defects are recorded but no rejector pulses are scheduled
        self.actuate = actuate
        self.stream = stream if stream is not None else CameraStream(config.camera)
        self.detector = ItemDetector(config.vision, crop_margin=config.inspection.crop_margin)
        self.classifier = ItemClassifier(config.inspection)
        self.tracker = CentroidTracker(
            max_disappeared=config.inspection.max_disappeared,
            distance_tolerance_factor=config.inspection.tracking_distance_factor,
        )
        self.actuator = create_actuator(config.hardware)
        self.rejector = ConveyorRejector(self.actuator, config.hardware)
        self.metrics = ProductionMetrics()

        self.frame_index = 0
        self._last_frame_id = 0
        self._fps_time = time.time()
        self._fps_counter = 0
        self._lock = threading.Lock()
        self.latest_mask_view: Optional[np.ndarray] = None

        if self.config.storage.save_snapshots:
            Path(self.config.storage.snapshot_dir).mkdir(parents=True, exist_ok=True)

    def start(self) -> "InspectionPipeline":
        self.stream.start()
        return self

    def stop(self) -> None:
        self.stream.stop()
        self.rejector.cleanup()

    def reset_metrics(self) -> None:
        with self._lock:
            self.metrics.reset()

    def process_next_frame(self) -> Tuple[Optional[FrameInspectionResult], Optional[np.ndarray]]:
        """
        Processes the newest camera frame if one arrived since the last call.
        Returns (result, annotated_frame) or (None, None) when there is no new frame.
        """
        frame_id, frame = self.stream.read()
        if frame is None or frame_id == self._last_frame_id:
            return None, None
        self._last_frame_id = frame_id
        return self.process_frame(frame)

    def process_frame(self, frame: np.ndarray) -> Tuple[FrameInspectionResult, np.ndarray]:
        """Runs one inspection cycle on a BGR frame."""
        with self._lock:
            return self._process(frame)

    def flush(self) -> List[DetectedItem]:
        """Close all open tracks (e.g. at end of a video) and return those counted as passed."""
        with self._lock:
            return self._collect_passed(self.tracker.flush())

    def _collect_passed(self, finished: List[DetectedItem]) -> List[DetectedItem]:
        passed = []
        for item in finished:
            if item.rejected:
                continue  # already counted when it was rejected
            if item.frames_seen >= self.config.inspection.min_frames_to_count:
                self.metrics.total_good += 1
                passed.append(item)
        return passed

    def _process(self, frame: np.ndarray) -> Tuple[FrameInspectionResult, np.ndarray]:
        insp = self.config.inspection
        self.frame_index += 1
        now = time.time()

        self._fps_counter += 1
        if now - self._fps_time >= 1.0:
            self.metrics.current_fps = round(self._fps_counter / (now - self._fps_time), 1)
            self._fps_counter = 0
            self._fps_time = now

        # Keep runtime-tunable parameters in sync
        self.detector.crop_margin = insp.crop_margin
        self.tracker.max_disappeared = insp.max_disappeared
        self.tracker.distance_tolerance_factor = insp.tracking_distance_factor

        # 1. Detection & ROI extraction
        raw_dets, _, mask, (rx, ry, rw, rh) = self.detector.process_frame(frame)

        # 2. Classification
        classifications = [self.classifier.classify(det) for det in raw_dets]

        # 3. Tracking
        active_items = self.tracker.update(raw_dets, classifications)

        # 4. Rejection rules
        new_defects: List[DetectedItem] = []
        for item in active_items:
            if (
                item.defect.is_defective
                and not item.rejected
                and item.consecutive_defect_frames >= insp.min_frames_to_reject
            ):
                item.rejected = True
                new_defects.append(item)
                if self.actuate:
                    self.rejector.schedule_rejection(
                        item_id=item.id,
                        current_x=float(item.center[0] + rx),
                        reason=item.defect.value,
                    )
                self._save_defect_snapshot(frame, item)
                self.metrics.record_defect(item.defect)

        passed_items = self._collect_passed(self.tracker.pop_finished())

        # 5. Visual output
        # Only draw items detected in this frame; tracks kept open while briefly missing
        # would otherwise show up as empty "ghost" circles.
        visible = [item for item in active_items if self.tracker.disappeared.get(item.id, 1) == 0]
        annotated = self._render_overlay(frame, visible, (rx, ry, rw, rh))
        mask_view = np.zeros(frame.shape[:2], dtype=np.uint8)
        mask_view[ry : ry + rh, rx : rx + rw] = mask
        self.latest_mask_view = cv2.cvtColor(mask_view, cv2.COLOR_GRAY2BGR)

        result = FrameInspectionResult(
            frame_index=self.frame_index,
            timestamp=now,
            items=active_items,
            fps=self.metrics.current_fps,
            new_defects=new_defects,
            passed_items=passed_items,
        )
        return result, annotated

    def _save_defect_snapshot(self, full_frame: np.ndarray, item: DetectedItem) -> None:
        """Save the raw frame of a rejected item, grouped by date."""
        if not self.config.storage.save_snapshots:
            return
        day_dir = Path(self.config.storage.snapshot_dir) / time.strftime("%Y-%m-%d")
        filename = f"{time.strftime('%H%M%S')}_id{item.id}_{item.defect.value}.jpg"
        save_path = day_dir / filename
        try:
            day_dir.mkdir(parents=True, exist_ok=True)
            if cv2.imwrite(str(save_path), full_frame):
                item.snapshot_path = str(save_path)
                logger.info(f"Saved defect snapshot: {save_path}")
        except Exception as e:
            logger.error(f"Failed to save snapshot: {e}")

    def _render_overlay(
        self,
        full_frame: np.ndarray,
        items: List[DetectedItem],
        roi_box: Tuple[int, int, int, int],
    ) -> np.ndarray:
        """Draw ROI, reject line, tracked items and a HUD on a copy of the frame."""
        display = full_frame.copy()
        rx, ry, rw, rh = roi_box
        font = cv2.FONT_HERSHEY_SIMPLEX

        cv2.rectangle(display, (rx, ry), (rx + rw, ry + rh), (255, 180, 0), 2)

        target_x = self.config.hardware.rejector_trigger_line_x
        if 0 < target_x < display.shape[1] and self.config.hardware.enabled:
            cv2.line(display, (target_x, 0), (target_x, display.shape[0]), (0, 0, 255), 2)
            cv2.putText(display, "REJECT LINE", (target_x + 5, 60), font, 0.5, (0, 0, 255), 1)

        for item in items:
            cx = item.center[0] + rx
            cy = item.center[1] + ry
            r = int(item.radius)
            color = COLORS.get(item.defect, COLORS[DefectType.UNKNOWN])

            if item.defect == DefectType.BURNT:
                status_txt = f"BURNT ({int(item.avg_color)})"
            elif item.defect == DefectType.UNDERCOOKED:
                status_txt = f"RAW ({int(item.avg_color)})"
            elif item.defect == DefectType.DEFORMED:
                status_txt = f"DEF ({item.circularity:.2f})"
            elif item.defect == DefectType.GOOD:
                status_txt = f"OK ({int(item.avg_color)})"
            else:
                status_txt = "?"
            if item.rejected:
                status_txt += " REJ"

            cv2.circle(display, (cx, cy), r, color, 3 if item.defect.is_defective else 2)
            cv2.circle(display, (cx, cy), 3, color, -1)
            label_pos = (cx - r, max(55, cy - r - 8))
            cv2.putText(display, f"#{item.id} {status_txt}", label_pos, font, 0.45, color, 1)

        m = self.metrics
        cv2.rectangle(display, (0, 0), (display.shape[1], 40), (20, 20, 20), -1)
        hud_text = (
            f"FPS: {m.current_fps:.1f} | Total: {m.total_inspected} | "
            f"Good: {m.total_good} ({m.good_rate_pct}%) | Defects: {m.total_defects} | "
            f"{m.throughput_per_minute}/min"
        )
        cv2.putText(display, hud_text, (15, 25), font, 0.5, (255, 255, 255), 1)
        return display
