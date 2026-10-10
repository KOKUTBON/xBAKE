"""
Raspberry Pi GPIO Actuator implementation using non-blocking background pulses.
"""

import logging
import threading
import time

from xbake.hardware.base import ActuatorInterface

logger = logging.getLogger(__name__)


class RaspberryPiActuator(ActuatorInterface):
    """Controls a physical relay or transistor driver via Raspberry Pi GPIO pin."""

    def __init__(self, pin: int = 21, active_high: bool = True):
        self.pin = pin
        self.active_high = active_high
        self._active = False
        self._lock = threading.Lock()
        self._device = None

        try:
            from gpiozero import OutputDevice

            self._device = OutputDevice(pin, active_high=active_high, initial_value=False)
            logger.info(f"Initialized Raspberry Pi GPIO OutputDevice on pin {pin}")
        except Exception as e:
            logger.warning(
                f"Could not initialize physical GPIO pin {pin} ({e}). Operating in software emulation mode."
            )

    def trigger(self, duration_s: float = 0.40, reason: str = "defect") -> bool:
        with self._lock:
            if self._active:
                logger.debug(f"GPIO {self.pin} already high, ignoring trigger")
                return False
            self._active = True

        def _pulse():
            try:
                if self._device is not None:
                    self._device.on()
                logger.info(f"[GPIO {self.pin}] ENERGIZED for {duration_s:.2f}s (Reason: {reason})")
                time.sleep(duration_s)
            except Exception as e:
                logger.error(f"[GPIO {self.pin}] Error during pulse: {e}")
            finally:
                try:
                    if self._device is not None:
                        self._device.off()
                except Exception:
                    pass
                with self._lock:
                    self._active = False
                logger.info(f"[GPIO {self.pin}] DE-ENERGIZED")

        thread = threading.Thread(target=_pulse, daemon=True)
        thread.start()
        return True

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def cleanup(self) -> None:
        with self._lock:
            if self._device is not None:
                try:
                    self._device.off()
                    self._device.close()
                except Exception:
                    pass
            self._active = False
        logger.info(f"GPIO {self.pin} released and closed")
