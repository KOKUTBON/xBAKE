"""
Hardware Abstraction Layer (HAL) base interfaces.
"""

from abc import ABC, abstractmethod


class ActuatorInterface(ABC):
    """Abstract interface for all rejection and signaling actuators."""

    @abstractmethod
    def trigger(self, duration_s: float = 0.40, reason: str = "defect") -> bool:
        """
        Trigger the actuator (e.g. pulse relay, fire pneumatic valve, or activate siren).
        Must be non-blocking.
        """
        pass

    @abstractmethod
    def is_active(self) -> bool:
        """Return True if the actuator is currently actively energized."""
        pass

    @abstractmethod
    def cleanup(self) -> None:
        """Safely release pins/ports and de-energize circuits upon system shutdown."""
        pass


class RoboticArmInterface(ABC):
    """Abstract interface for robotic sorting and pick-and-place manipulators."""

    @abstractmethod
    def reject_item(self, item_id: int, conveyor_x: float, conveyor_y: float) -> bool:
        """Instruct the robotic arm to pick and discard a defective item."""
        pass

    @abstractmethod
    def home(self) -> bool:
        """Return arm to safe home position."""
        pass
