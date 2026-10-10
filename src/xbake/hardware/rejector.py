"""
Conveyor-synchronized rejector scheduler and actuator factory.
"""

import logging
import threading
import time

from xbake.config import HardwareConfig
from xbake.hardware.base import ActuatorInterface
from xbake.hardware.mock_actuator import MockActuator
from xbake.hardware.robotic_arm import RoboticArmActuator, RoboticArmDriver
from xbake.hardware.rpi_gpio import RaspberryPiActuator

logger = logging.getLogger(__name__)


def create_actuator(config: HardwareConfig) -> ActuatorInterface:
    """Factory to instantiate the appropriate actuator based on environment."""
    if not config.enabled or config.driver == "mock":
        logger.info("Using MockActuator (hardware disabled or driver='mock')")
        return MockActuator(name="MockRejector")

    if config.driver == "rpi_gpio":
        return RaspberryPiActuator(pin=config.gpio_pin)

    if config.driver == "robotic_arm":
        arm = RoboticArmDriver(port=config.arm_port, baudrate=config.arm_baudrate, mock=False)
        return RoboticArmActuator(arm, pick_x=float(config.rejector_trigger_line_x))

    if config.driver != "auto":
        logger.warning(f"Unknown hardware driver '{config.driver}', using MockActuator")
        return MockActuator(name="MockRejector")

    # "auto": use GPIO when gpiozero is installed, otherwise simulate
    try:
        import gpiozero  # noqa: F401
    except ImportError:
        logger.info("gpiozero not available, falling back to MockActuator")
        return MockActuator(name="AutoFallbackMockRejector")
    logger.info("gpiozero available, using RaspberryPiActuator")
    return RaspberryPiActuator(pin=config.gpio_pin)


class ConveyorRejector:
    """
    Coordinates defect rejection with conveyor belt timing.

    Each request is delayed by the time the item needs to reach the rejector line.
    Requests are never dropped: pulses are serialized so that consecutive items
    get their own pulse, separated by at least ``debounce_s`` of idle time.
    """

    # A pulse pushed later than this by a previous one may miss its item.
    LATE_WARNING_S = 0.25

    def __init__(self, actuator: ActuatorInterface, config: HardwareConfig):
        self.actuator = actuator
        self.config = config
        self._next_free_time = 0.0
        self._lock = threading.Lock()
        self.late_pulses = 0

    def travel_delay(self, current_x: float) -> float:
        """Seconds until an item at ``current_x`` reaches the rejector line."""
        speed = self.config.conveyor_speed_px_per_s
        target_x = self.config.rejector_trigger_line_x
        if speed > 0 and target_x > current_x:
            return (target_x - current_x) / speed
        return 0.0

    def schedule_rejection(self, item_id: int, current_x: float, reason: str = "burnt") -> bool:
        """Book a pulse for an item. Returns True once the pulse is scheduled."""
        now = time.time()
        desired = now + self.travel_delay(current_x)
        pulse = self.config.pulse_duration_s
        with self._lock:
            fire_at = max(desired, self._next_free_time)
            self._next_free_time = fire_at + pulse + self.config.debounce_s

        lateness = fire_at - desired
        if lateness > self.LATE_WARNING_S:
            self.late_pulses += 1
            logger.warning(
                f"Rejection for item #{item_id} delayed {lateness:.2f}s by the previous pulse; "
                "items may be too close together for the current pulse_duration_s/debounce_s"
            )

        label = f"{reason} (item #{item_id})"
        delay = fire_at - now
        if delay <= 0.02:
            logger.info(f"Triggering immediate rejection for item #{item_id} ({reason})")
            self._fire(pulse, label)
            return True

        logger.info(f"Scheduled rejection for item #{item_id} in {delay:.2f}s")
        threading.Thread(target=self._fire, args=(pulse, label, fire_at), daemon=True).start()
        return True

    def _fire(self, pulse: float, label: str, fire_at: float = 0.0) -> None:
        wait = fire_at - time.time()
        if wait > 0:
            time.sleep(wait)
        # The actuator may still be busy (e.g. a manual test pulse): retry briefly
        deadline = time.time() + max(0.5, pulse)
        while not self.actuator.trigger(duration_s=pulse, reason=label):
            if time.time() >= deadline:
                logger.error(f"Actuator busy, rejection pulse for {label} was not delivered")
                return
            time.sleep(0.01)

    def cleanup(self) -> None:
        self.actuator.cleanup()
