"""
Synthetic conveyor footage for tests: one well-baked and one burnt disk moving
left to right over a dark belt. Generated at runtime so no media ships with the repo.
"""

from pathlib import Path
from typing import Iterator

import cv2
import numpy as np

from xbake.config import AppConfig

WIDTH, HEIGHT = 640, 480
GOOD_BGR = (110, 160, 200)  # mean ~157 -> good
BURNT_BGR = (35, 50, 70)  # mean ~52 -> burnt
BELT_BGR = (10, 10, 10)


def synthetic_config() -> AppConfig:
    cfg = AppConfig()
    cfg.vision.lower_hsv = [0, 0, 40]  # exclude the dark belt
    cfg.hardware.driver = "mock"
    cfg.hardware.debounce_s = 0.0
    cfg.hardware.conveyor_speed_px_per_s = 0.0  # fire the rejector immediately
    cfg.storage.save_snapshots = False
    return cfg


def synthetic_frames(moving_frames: int = 50, empty_frames: int = 20) -> Iterator[np.ndarray]:
    for i in range(moving_frames + empty_frames):
        frame = np.full((HEIGHT, WIDTH, 3), BELT_BGR, dtype=np.uint8)
        if i < moving_frames:
            x = 100 + i * 8
            cv2.circle(frame, (x, 150), 40, GOOD_BGR, -1)
            cv2.circle(frame, (x, 330), 40, BURNT_BGR, -1)
        yield frame


def write_synthetic_video(path: Path) -> Path:
    path = Path(path)
    writer = cv2.VideoWriter(str(path), cv2.VideoWriter_fourcc(*"MJPG"), 25.0, (WIDTH, HEIGHT))
    for frame in synthetic_frames():
        writer.write(frame)
    writer.release()
    synthetic_config().save(path.parent / "config.json")
    return path
