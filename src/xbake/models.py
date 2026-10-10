"""
Domain models and data structures for xBAKE.
"""

from __future__ import annotations

import time
from dataclasses import dataclass, field
from enum import Enum
from typing import List, Optional, Tuple


class DefectType(str, Enum):
    """Classification of a baked product's color and geometry."""

    GOOD = "good"
    BURNT = "burnt"
    UNDERCOOKED = "undercooked"
    DEFORMED = "deformed"
    UNKNOWN = "unknown"

    @property
    def is_defective(self) -> bool:
        return self in (DefectType.BURNT, DefectType.UNDERCOOKED, DefectType.DEFORMED)


@dataclass
class BoundingBox:
    """Bounding box coordinates for a detected object."""

    x: int
    y: int
    w: int
    h: int

    @property
    def x2(self) -> int:
        return self.x + self.w

    @property
    def y2(self) -> int:
        return self.y + self.h

    @property
    def area(self) -> int:
        return self.w * self.h


@dataclass
class DetectedItem:
    """A single product tracked across frames on the conveyor."""

    id: int
    center: Tuple[int, int]
    radius: float
    bbox: BoundingBox
    defect: DefectType = DefectType.UNKNOWN
    confidence: float = 0.0
    avg_color: float = 0.0
    circularity: float = 1.0
    aspect_ratio: float = 1.0
    frames_seen: int = 1
    first_seen_ts: float = field(default_factory=time.time)
    last_seen_ts: float = field(default_factory=time.time)
    consecutive_defect_frames: int = 0
    rejected: bool = False
    snapshot_path: Optional[str] = None

    def update_position(self, center: Tuple[int, int], radius: float, bbox: BoundingBox) -> None:
        self.center = center
        self.radius = radius
        self.bbox = bbox
        self.frames_seen += 1
        self.last_seen_ts = time.time()


@dataclass
class FrameInspectionResult:
    """Result of processing a single frame through the inspection pipeline."""

    frame_index: int
    timestamp: float
    items: List[DetectedItem]
    fps: float
    # Items confirmed defective and rejected during this frame
    new_defects: List[DetectedItem] = field(default_factory=list)
    # Items that left the field of view without being rejected (final verdict: passed)
    passed_items: List[DetectedItem] = field(default_factory=list)


@dataclass
class ProductionMetrics:
    """Aggregated real-time metrics for the current session."""

    total_good: int = 0
    total_burnt: int = 0
    total_undercooked: int = 0
    total_deformed: int = 0
    start_time: float = field(default_factory=time.time)
    current_fps: float = 0.0

    @property
    def total_defects(self) -> int:
        return self.total_burnt + self.total_undercooked + self.total_deformed

    @property
    def total_rejected(self) -> int:
        return self.total_defects

    @property
    def total_inspected(self) -> int:
        return self.total_good + self.total_defects

    @property
    def defect_rate_pct(self) -> float:
        if self.total_inspected == 0:
            return 0.0
        return round((self.total_defects / self.total_inspected) * 100.0, 2)

    @property
    def good_rate_pct(self) -> float:
        if self.total_inspected == 0:
            return 100.0
        return round((self.total_good / self.total_inspected) * 100.0, 2)

    @property
    def uptime_seconds(self) -> float:
        return time.time() - self.start_time

    @property
    def throughput_per_minute(self) -> float:
        mins = self.uptime_seconds / 60.0
        if mins <= 0.01:
            return 0.0
        return round(self.total_inspected / mins, 1)

    def record_defect(self, defect: DefectType) -> None:
        if defect == DefectType.BURNT:
            self.total_burnt += 1
        elif defect == DefectType.UNDERCOOKED:
            self.total_undercooked += 1
        elif defect == DefectType.DEFORMED:
            self.total_deformed += 1

    def reset(self) -> None:
        self.total_good = 0
        self.total_burnt = 0
        self.total_undercooked = 0
        self.total_deformed = 0
        self.start_time = time.time()

    def to_dict(self) -> dict:
        return {
            "total_inspected": self.total_inspected,
            "total_good": self.total_good,
            "total_burnt": self.total_burnt,
            "total_undercooked": self.total_undercooked,
            "total_deformed": self.total_deformed,
            "total_rejected": self.total_rejected,
            "defect_rate_pct": self.defect_rate_pct,
            "good_rate_pct": self.good_rate_pct,
            "throughput_per_minute": self.throughput_per_minute,
            "uptime_seconds": round(self.uptime_seconds, 1),
            "current_fps": round(self.current_fps, 1),
        }
