"""
Product contour detection, morphological filtering, and geometric feature extraction.
"""

import math
from dataclasses import dataclass
from typing import Any, List, Tuple

import numpy as np

from xbake.config import VisionConfig
from xbake.models import BoundingBox


@dataclass
class RawDetection:
    """Candidate detection before tracking and classification (ROI coordinates)."""

    center: Tuple[int, int]
    radius: float
    bbox: BoundingBox
    circularity: float
    aspect_ratio: float
    avg_color: float
    center_roi: Any
    contour: Any


def _odd(k: int) -> int:
    k = max(1, int(k))
    return k if k % 2 == 1 else k + 1


class ItemDetector:
    """
    Detects round baked products on a conveyor belt using HSV color segmentation
    and contour geometry.
    """

    def __init__(self, config: VisionConfig, crop_margin: float = 0.20):
        self.config = config
        self.crop_margin = crop_margin

    def compute_roi(self, frame_shape: Tuple[int, ...]) -> Tuple[int, int, int, int]:
        """Convert the normalized ROI to pixel coordinates (x, y, w, h), clamped to the frame."""
        h_full, w_full = frame_shape[:2]
        norm = max(1, self.config.norm_resolution)
        roi_x = int((self.config.roi_x * w_full) / norm)
        roi_w = int((self.config.roi_w * w_full) / norm)
        roi_y = int((self.config.roi_y * h_full) / norm)
        roi_h = int((self.config.roi_h * h_full) / norm)

        roi_x = max(0, min(roi_x, w_full - 10))
        roi_y = max(0, min(roi_y, h_full - 10))
        roi_w = max(10, min(roi_w, w_full - roi_x))
        roi_h = max(10, min(roi_h, h_full - roi_y))
        return roi_x, roi_y, roi_w, roi_h

    def segment(self, roi_frame: np.ndarray) -> np.ndarray:
        """Return the binary operative mask for an ROI frame."""
        import cv2

        hsv = cv2.cvtColor(roi_frame, cv2.COLOR_BGR2HSV)
        lower = np.array(self.config.lower_hsv, dtype=np.uint8)
        upper = np.array(self.config.upper_hsv, dtype=np.uint8)
        mask = cv2.inRange(hsv, lower, upper)

        k_open = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, tuple(max(1, int(v)) for v in self.config.morph_op)
        )
        k_close = cv2.getStructuringElement(
            cv2.MORPH_ELLIPSE, tuple(max(1, int(v)) for v in self.config.morph_cl)
        )
        blur_k = (_odd(self.config.blur[0]), _odd(self.config.blur[1]))

        mode = self.config.filter_mode
        if mode == 0:  # Open + Close + Blur
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_open)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_close)
            mask = cv2.GaussianBlur(mask, blur_k, 0)
        elif mode == 1:  # Morph only
            mask = cv2.morphologyEx(mask, cv2.MORPH_OPEN, k_open)
            mask = cv2.morphologyEx(mask, cv2.MORPH_CLOSE, k_close)
        else:  # Blur only
            mask = cv2.GaussianBlur(mask, blur_k, 0)
        if mode != 1:
            # Re-binarize after blurring: soft edges would otherwise be traced as part of
            # the contour, producing ragged outlines and underestimated circularity.
            _, mask = cv2.threshold(mask, 127, 255, cv2.THRESH_BINARY)
        return mask

    def process_frame(
        self, frame: np.ndarray
    ) -> Tuple[List[RawDetection], np.ndarray, np.ndarray, Tuple[int, int, int, int]]:
        """
        Processes a raw camera frame.
        Returns:
            - List of RawDetection items (coordinates relative to the ROI)
            - Cropped ROI frame
            - Binary operative mask
            - ROI coordinates (roi_x, roi_y, roi_w, roi_h) in full-frame pixels
        """
        import cv2

        roi_x, roi_y, roi_w, roi_h = self.compute_roi(frame.shape)
        roi_frame = frame[roi_y : roi_y + roi_h, roi_x : roi_x + roi_w]
        operative_mask = self.segment(roi_frame)

        contours, _ = cv2.findContours(operative_mask, cv2.RETR_EXTERNAL, cv2.CHAIN_APPROX_NONE)

        # Radius bounds are normalized against the ROI height
        norm = max(1, self.config.norm_resolution)
        min_rad = (self.config.min_radius * roi_h) / norm
        max_rad = (self.config.max_radius * roi_h) / norm

        detections: List[RawDetection] = []
        for cnt in contours:
            x, y, w, h = cv2.boundingRect(cnt)
            # Skip products cut by the ROI border: they are only partially visible and
            # would be misjudged as deformed. They are picked up once fully inside.
            if x <= 0 or y <= 0 or x + w >= roi_w or y + h >= roi_h:
                continue
            rr = (w + h) / 4.0
            if not (min_rad <= rr <= max_rad):
                continue

            area = cv2.contourArea(cnt)
            perimeter = cv2.arcLength(cnt, True)
            circularity = (4.0 * math.pi * area) / (perimeter * perimeter) if perimeter > 0 else 0.0
            circularity = min(1.0, circularity)
            aspect_ratio = min(w, h) / max(w, h) if max(w, h) > 0 else 0.0

            # Central crop for color sampling (inscribed rectangle)
            margin = self.crop_margin
            x1 = max(0, int(x + (w * margin)))
            y1 = max(0, int(y + (h * margin)))
            x2 = min(roi_frame.shape[1], int(x + (w * (1.0 - margin))))
            y2 = min(roi_frame.shape[0], int(y + (h * (1.0 - margin))))
            center_crop = roi_frame[y1:y2, x1:x2]
            avg_val = float(center_crop.mean()) if center_crop.size > 0 else 0.0

            detections.append(
                RawDetection(
                    center=(int(x + w / 2.0), int(y + h / 2.0)),
                    radius=rr,
                    bbox=BoundingBox(x=x, y=y, w=w, h=h),
                    circularity=round(circularity, 3),
                    aspect_ratio=round(aspect_ratio, 3),
                    avg_color=round(avg_val, 1),
                    center_roi=center_crop,
                    contour=cnt,
                )
            )

        return detections, roi_frame, operative_mask, (roi_x, roi_y, roi_w, roi_h)
