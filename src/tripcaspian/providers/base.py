"""Base Provider interface and unified RouteOption data model for TripCaspian."""

from abc import ABC, abstractmethod
from dataclasses import dataclass, asdict
from typing import Any


@dataclass
class RouteOption:
    """Unified route option across trains, buses, and cabs."""

    id: str
    mode: str             # "train", "bus", "cab"
    operator: str         # e.g., "IRCTC Vande Bharat", "Zingbus", "Uber XL"
    price: float          # in INR (₹)
    depart: str           # e.g., "06:00 AM" or ISO timestamp
    arrive: str           # e.g., "10:30 AM" or ISO timestamp
    duration_minutes: int # total travel duration in minutes
    seats_left: int       # available seat count
    deep_link: str        # booking deep link / web handoff URL
    source: str           # origin city/station
    destination: str      # destination city/station
    is_mock: bool = True  # True if from mock fixture, False if from live API

    def to_dict(self) -> dict[str, Any]:
        """Convert dataclass to dictionary."""
        return asdict(self)

    @classmethod
    def from_dict(cls, data: dict[str, Any]) -> "RouteOption":
        """Reconstruct RouteOption from dictionary."""
        return cls(**data)


class Provider(ABC):
    """Abstract Base Class for travel providers (trains, buses, cabs)."""

    def __init__(self, name: str, is_live: bool = False):
        self.name = name
        self.is_live = is_live

    @abstractmethod
    def search(
        self, source: str, destination: str, depart_time: str | None = None
    ) -> list[RouteOption]:
        """Search available routes for the given route and time window."""
        pass

    @abstractmethod
    def check_availability(self, option_id: str) -> dict[str, Any]:
        """Check live seat count and updated price for a specific option ID.

        Returns:
            {"seats_left": int, "price": float, "updated": bool}
        """
        pass

    @abstractmethod
    def build_booking_link(self, option: RouteOption) -> str:
        """Build a pre-filled booking handoff deep link for the user."""
        pass

    def initiate_booking(self, option: RouteOption) -> str:
        """Initiate the booking flow by building and returning the handoff link."""
        return self.build_booking_link(option)
