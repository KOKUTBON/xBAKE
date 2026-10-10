"""
Mock actuator for development, CI/CD, and testing without hardware.
"""

import logging
import threading
import time
from typing import List, Tuple

from xbake.hardware.base import ActuatorInterface

logger = logging.getLogger(__name__)


class MockActuator(ActuatorInterface):
    """Simulated actuator that records trigger events and simulates pulse duration."""

    def __init__(self, name: str = "MockRelay"):
        self.name = name
        self._active = False
        self._lock = threading.Lock()
        self.trigger_history: List[Tuple[float, float, str]] = []  # (timestamp, duration, reason)

    def trigger(self, duration_s: float = 0.40, reason: str = "defect") -> bool:
        with self._lock:
            if self._active:
                logger.debug(f"[{self.name}] Trigger request ignored: already energized")
                return False
            self._active = True
            now = time.time()
            self.trigger_history.append((now, duration_s, reason))
            logger.info(f"[{self.name}] TRIGGERED for {duration_s:.2f}s (Reason: {reason})")

        # Spawn background timer to simulate release without blocking caller
        def _release():
            time.sleep(duration_s)
            with self._lock:
                self._active = False
            logger.info(f"[{self.name}] Released after {duration_s:.2f}s")

        t = threading.Thread(target=_release, daemon=True)
        t.start()
        return True

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def cleanup(self) -> None:
        with self._lock:
            self._active = False
        logger.info(f"[{self.name}] Cleaned up")
