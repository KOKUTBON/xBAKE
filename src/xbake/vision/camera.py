"""
Threaded camera stream with non-blocking frame retrieval, auto-reconnect, and video loop support.
"""

import logging
import threading
import time
from typing import Any, Optional, Tuple

from xbake.config import CameraConfig

logger = logging.getLogger(__name__)


def parse_source(source: Any) -> Any:
    """Camera indices may arrive as strings ("0"); convert them to int."""
    if isinstance(source, str) and source.strip().isdigit():
        return int(source)
    return source


class CameraStream:
    """
    Acquires frames in a dedicated worker thread so the inspection loop always
    works on the most recent frame (no buffer bloat).
    """

    def __init__(self, config: CameraConfig, loop_video: bool = True):
        self.config = config
        self.loop_video = loop_video
        self.source = parse_source(config.source)

        self._cap = None
        self._frame = None
        self._frame_id = 0
        self._lock = threading.Lock()
        self._running = False
        self._settings_dirty = False
        self._finished = False
        self._thread: Optional[threading.Thread] = None
        self._file_frame_interval = 1.0 / 30.0
        self.fps: float = 0.0
        self._frame_count: int = 0
        self._fps_start_time: float = time.time()

    @property
    def is_live_camera(self) -> bool:
        return isinstance(self.source, int)

    @property
    def finished(self) -> bool:
        """True when a non-looping video file has been fully read."""
        return self._finished

    @property
    def is_opened(self) -> bool:
        return self._cap is not None and self._cap.isOpened()

    def start(self) -> "CameraStream":
        """Initialize camera and start background capture thread."""
        self._running = True
        self._open_capture()
        self._thread = threading.Thread(target=self._capture_loop, name="CameraWorker", daemon=True)
        self._thread.start()
        logger.info(f"Camera stream started for source: {self.source}")
        return self

    def apply_settings(self) -> None:
        """Request re-application of camera properties (brightness, contrast, ...)."""
        self._settings_dirty = True

    def _apply_properties(self) -> None:
        import cv2

        if self._cap is None or not self.is_live_camera:
            return
        props = [
            (cv2.CAP_PROP_BRIGHTNESS, self.config.brightness),
            (cv2.CAP_PROP_CONTRAST, self.config.contrast),
            (cv2.CAP_PROP_SATURATION, self.config.saturation),
            (cv2.CAP_PROP_GAMMA, self.config.gamma),
            (cv2.CAP_PROP_HUE, self.config.hue),
            (cv2.CAP_PROP_AUTO_WB, self.config.auto_wb),
        ]
        for prop, value in props:
            self._cap.set(prop, value)

    def _open_capture(self) -> None:
        try:
            import cv2
        except ImportError:
            logger.error("OpenCV (cv2) is required for CameraStream")
            return

        if self._cap is not None:
            self._cap.release()
        self._cap = cv2.VideoCapture(self.source)
        if self.is_live_camera:
            self._apply_properties()
            if self.config.target_width > 0:
                self._cap.set(cv2.CAP_PROP_FRAME_WIDTH, self.config.target_width)
            if self.config.target_height > 0:
                self._cap.set(cv2.CAP_PROP_FRAME_HEIGHT, self.config.target_height)
        else:
            src_fps = self._cap.get(cv2.CAP_PROP_FPS) or 0.0
            if 1.0 <= src_fps <= 240.0:
                self._file_frame_interval = 1.0 / src_fps

        if not self._cap.isOpened():
            logger.warning(f"Could not open video source: {self.source}")

    def _capture_loop(self) -> None:
        try:
            import cv2
        except ImportError:
            return

        while self._running:
            if self._cap is None or not self._cap.isOpened():
                time.sleep(1.0)
                self._open_capture()
                continue

            if self._settings_dirty:
                self._settings_dirty = False
                self._apply_properties()

            ret, frame = self._cap.read()
            if not ret or frame is None:
                if not self.is_live_camera:
                    if self.loop_video:
                        self._cap.set(cv2.CAP_PROP_POS_FRAMES, 0)
                        time.sleep(0.01)
                        continue
                    logger.info("Video file reached end of stream.")
                    self._finished = True
                    self._running = False
                    break
                logger.warning("Frame read failure, attempting reconnect in 1s...")
                time.sleep(1.0)
                self._open_capture()
                continue

            with self._lock:
                self._frame = frame
                self._frame_id += 1
                self._frame_count += 1
                now = time.time()
                elapsed = now - self._fps_start_time
                if elapsed >= 1.0:
                    self.fps = round(self._frame_count / elapsed, 1)
                    self._frame_count = 0
                    self._fps_start_time = now

            # Play video files back at their native frame rate
            if not self.is_live_camera:
                time.sleep(self._file_frame_interval)

    def read(self) -> Tuple[int, Optional[Any]]:
        """Return (frame_id, frame) for the most recent frame; frame_id is 0 before the first frame."""
        with self._lock:
            if self._frame is None:
                return 0, None
            return self._frame_id, self._frame

    def stop(self) -> None:
        """Stop background worker and release camera hardware."""
        self._running = False
        if self._thread and self._thread.is_alive():
            self._thread.join(timeout=1.5)
        if self._cap is not None:
            self._cap.release()
            self._cap = None
        logger.info("Camera stream stopped.")
