"""
Tests for hardware abstraction, mock actuator, and conveyor rejector.
"""

import time
import unittest

from xbake.config import HardwareConfig
from xbake.hardware.mock_actuator import MockActuator
from xbake.hardware.rejector import ConveyorRejector, create_actuator


class TestHardware(unittest.TestCase):
    def test_default_factory_is_mock_safe(self):
        actuator = create_actuator(HardwareConfig())
        self.assertIsInstance(actuator, MockActuator)
        actuator.cleanup()

    def test_mock_actuator_trigger(self):
        actuator = MockActuator(name="UnitTestActuator")
        self.assertFalse(actuator.is_active())

        res = actuator.trigger(duration_s=0.1, reason="test")
        self.assertTrue(res)
        self.assertTrue(actuator.is_active())
        self.assertEqual(len(actuator.trigger_history), 1)

        # Wait for non-blocking release
        time.sleep(0.15)
        self.assertFalse(actuator.is_active())

    def test_conveyor_rejector_immediate(self):
        actuator = MockActuator()
        cfg = HardwareConfig(
            conveyor_speed_px_per_s=0.0,
            rejector_trigger_line_x=100,
            pulse_duration_s=0.05,
            debounce_s=0.01,
        )
        rejector = ConveyorRejector(actuator, cfg)
        res = rejector.schedule_rejection(item_id=1, current_x=50, reason="burnt")
        self.assertTrue(res)
        self.assertTrue(actuator.is_active())

    def test_close_rejections_are_serialized_not_dropped(self):
        actuator = MockActuator()
        cfg = HardwareConfig(conveyor_speed_px_per_s=0.0, pulse_duration_s=0.05, debounce_s=0.02)
        rejector = ConveyorRejector(actuator, cfg)
        for item_id in (1, 2, 3):
            self.assertTrue(rejector.schedule_rejection(item_id=item_id, current_x=0, reason="burnt"))
        time.sleep(0.4)
        self.assertEqual(len(actuator.trigger_history), 3)
        starts = [t for t, _, _ in actuator.trigger_history]
        gaps = [b - a for a, b in zip(starts, starts[1:], strict=False)]
        self.assertTrue(all(g >= 0.065 for g in gaps), gaps)

    def test_delay_matches_travel_time(self):
        actuator = MockActuator()
        cfg = HardwareConfig(
            conveyor_speed_px_per_s=100.0, rejector_trigger_line_x=120, pulse_duration_s=0.05
        )
        rejector = ConveyorRejector(actuator, cfg)
        self.assertAlmostEqual(rejector.travel_delay(100), 0.2)
        self.assertEqual(rejector.travel_delay(200), 0.0)  # already past the line
        start = time.time()
        rejector.schedule_rejection(item_id=1, current_x=100)
        time.sleep(0.1)
        self.assertEqual(actuator.trigger_history, [])
        time.sleep(0.25)
        self.assertEqual(len(actuator.trigger_history), 1)
        self.assertGreaterEqual(actuator.trigger_history[0][0] - start, 0.19)

    def test_retries_when_actuator_busy(self):
        actuator = MockActuator()
        cfg = HardwareConfig(conveyor_speed_px_per_s=0.0, pulse_duration_s=0.05, debounce_s=0.0)
        rejector = ConveyorRejector(actuator, cfg)
        actuator.trigger(duration_s=0.1, reason="manual test")  # occupy the actuator
        rejector.schedule_rejection(item_id=7, current_x=0)
        time.sleep(0.3)
        self.assertEqual([r for _, _, r in actuator.trigger_history][-1], "burnt (item #7)")


if __name__ == "__main__":
    unittest.main()
