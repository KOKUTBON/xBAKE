"""
Serial robotic arm driver for pick-and-place rejection.
"""

import logging
import threading

from xbake.hardware.base import ActuatorInterface, RoboticArmInterface

logger = logging.getLogger(__name__)


class RoboticArmDriver(RoboticArmInterface):
    """
    Serial controller for 4/5-axis hobby or industrial arms driven by a
    G-code compatible firmware (e.g. an Arduino / ESP32 controller).
    """

    def __init__(self, port: str = "/dev/ttyUSB0", baudrate: int = 115200, mock: bool = True):
        self.port = port
        self.baudrate = baudrate
        self.mock = mock
        self._serial = None

        if not self.mock:
            try:
                import serial

                self._serial = serial.Serial(self.port, self.baudrate, timeout=1.0)
                logger.info(f"Connected to robotic arm on {port} @ {baudrate} baud")
            except Exception as e:
                logger.warning(f"Could not open serial port {port} ({e}), switching to mock arm driver")
                self.mock = True

    def home(self) -> bool:
        """Send arm to safe home position."""
        if self.mock:
            logger.info("[MockArm] Homing arm to (X: 0, Y: 150, Z: 200)")
            return True
        return self._send_command("G28\n")

    def reject_item(self, item_id: int, conveyor_x: float, conveyor_y: float) -> bool:
        """
        Runs a pick-and-place sequence to discard a defective item.
        """
        logger.info(
            f"[Arm] Initiating discard sequence for item #{item_id} at ({conveyor_x:.1f}, {conveyor_y:.1f})"
        )
        if self.mock:
            logger.info(f"[MockArm] Picked item #{item_id} and placed in discard bin")
            return True

        # Send coordinate-based motion sequence
        # 1. Move above item
        self._send_command(f"G0 X{conveyor_x:.1f} Y{conveyor_y:.1f} Z120\n")
        # 2. Lower gripper
        self._send_command("G0 Z30\n")
        # 3. Close gripper
        self._send_command("M3\n")
        # 4. Lift
        self._send_command("G0 Z150\n")
        # 5. Move to discard bin
        self._send_command("G0 X-100 Y50 Z150\n")
        # 6. Open gripper
        self._send_command("M5\n")
        # 7. Return to home
        return self.home()

    def close(self) -> None:
        if self._serial is not None:
            try:
                self._serial.close()
            except Exception:
                pass
            self._serial = None

    def _send_command(self, cmd: str) -> bool:
        if self._serial is not None:
            try:
                self._serial.write(cmd.encode("utf-8"))
                return True
            except Exception as e:
                logger.error(f"[Arm] Serial error sending command '{cmd.strip()}': {e}")
                return False
        return True


class RoboticArmActuator(ActuatorInterface):
    """
    Adapts a RoboticArmInterface to the ActuatorInterface used by the rejector:
    each trigger runs one discard sequence at a fixed pick station.
    """

    def __init__(self, arm: RoboticArmInterface, pick_x: float, pick_y: float = 0.0):
        self.arm = arm
        self.pick_x = pick_x
        self.pick_y = pick_y
        self._active = False
        self._lock = threading.Lock()
        self._counter = 0

    def trigger(self, duration_s: float = 0.40, reason: str = "defect") -> bool:
        with self._lock:
            if self._active:
                logger.debug("[Arm] Busy, trigger ignored")
                return False
            self._active = True
            self._counter += 1
            seq = self._counter

        def _run():
            try:
                logger.info(f"[Arm] Discard sequence #{seq} ({reason})")
                self.arm.reject_item(seq, self.pick_x, self.pick_y)
            finally:
                with self._lock:
                    self._active = False

        threading.Thread(target=_run, daemon=True).start()
        return True

    def is_active(self) -> bool:
        with self._lock:
            return self._active

    def cleanup(self) -> None:
        try:
            self.arm.home()
        except Exception:
            pass
        closer = getattr(self.arm, "close", None)
        if callable(closer):
            closer()
