# -*- coding: utf-8 -*-
"""Base service abstraction layer for train reservation."""
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from typing import Any


class TrainProvider(Enum):
    """Train service provider enum."""
    SRT = "srt"
    KORAIL = "korail"


class SeatOption(Enum):
    """Seat reservation option enum."""
    GENERAL_FIRST = "GENERAL_FIRST"
    GENERAL_ONLY = "GENERAL_ONLY"
    SPECIAL_FIRST = "SPECIAL_FIRST"
    SPECIAL_ONLY = "SPECIAL_ONLY"


@dataclass
class TrainInfo:
    """Unified train information dataclass."""
    provider: TrainProvider
    train_name: str
    train_number: str
    dep_date: str
    dep_time: str
    arr_date: str
    arr_time: str
    dep_station: str
    arr_station: str
    general_seat_available: bool
    special_seat_available: bool
    raw_data: dict = field(default_factory=dict)

    @property
    def dep_time_formatted(self) -> str:
        """Return formatted departure time (HH:MM)."""
        return f"{self.dep_time[:2]}:{self.dep_time[2:4]}"

    @property
    def arr_time_formatted(self) -> str:
        """Return formatted arrival time (HH:MM)."""
        return f"{self.arr_time[:2]}:{self.arr_time[2:4]}"

    @property
    def duration_minutes(self) -> int:
        """Calculate travel duration in minutes."""
        try:
            dep = datetime.strptime(f"{self.dep_date}{self.dep_time}", "%Y%m%d%H%M%S")
            arr = datetime.strptime(f"{self.arr_date}{self.arr_time}", "%Y%m%d%H%M%S")
            return int((arr - dep).total_seconds() // 60)
        except (ValueError, TypeError):
            return 0

    @property
    def duration_formatted(self) -> str:
        """Format duration as '2시간 40분'."""
        mins = self.duration_minutes
        if mins <= 0:
            return ""
        hours, remaining_mins = divmod(mins, 60)
        if hours > 0:
            return f"{hours}시간 {remaining_mins}분" if remaining_mins > 0 else f"{hours}시간"
        return f"{remaining_mins}분"

    def has_seat(self) -> bool:
        """Check if any seat is available."""
        return self.general_seat_available or self.special_seat_available


@dataclass
class ReservationResult:
    """Reservation result dataclass."""
    success: bool
    message: str
    reservation_id: str | None = None
    details: dict = field(default_factory=dict)


class BaseTrainService(ABC):
    """Abstract base class for train services."""

    @abstractmethod
    def login(self, user_id: str, password: str) -> bool:
        """Login to the train service.

        Args:
            user_id: User ID (membership number, email, or phone)
            password: User password

        Returns:
            True if login successful, False otherwise
        """
        pass

    @abstractmethod
    def logout(self) -> None:
        """Logout from the train service."""
        pass

    @abstractmethod
    def is_logged_in(self) -> bool:
        """Check if user is logged in."""
        pass

    @abstractmethod
    def search(
        self,
        dep: str,
        arr: str,
        date: str,
        time: str,
        include_no_seats: bool = False
    ) -> list[TrainInfo]:
        """Search for available trains.

        Args:
            dep: Departure station name
            arr: Arrival station name
            date: Departure date (YYYYMMDD)
            time: Departure time (HHMMSS)
            include_no_seats: Include sold-out trains

        Returns:
            List of TrainInfo objects
        """
        pass

    @abstractmethod
    def reserve(
        self,
        train: TrainInfo,
        seat_option: SeatOption = SeatOption.GENERAL_FIRST,
        passenger_count: int = 1
    ) -> ReservationResult:
        """Reserve a train ticket.

        Args:
            train: Train to reserve
            seat_option: Seat preference option
            passenger_count: Number of adult passengers to reserve in this single call

        Returns:
            ReservationResult with success status and details
        """
        pass

    @abstractmethod
    def get_stations(self) -> list[str]:
        """Get list of available stations.

        Returns:
            List of station names
        """
        pass

    @abstractmethod
    def pay_with_card(
        self,
        reservation: Any,
        card_number: str,
        card_password: str,
        validation_number: str,
        card_expire: str,
        installment: int = 0,
        card_type: str = "J",
    ) -> ReservationResult:
        """Pay for a reservation with a credit card.

        Args:
            reservation: The provider-native reservation object
                (e.g. ReservationResult.details['reservation'] from reserve())
            card_number: Card number, no hyphens
            card_password: First 2 digits of the card password
            validation_number: Birth date YYMMDD (card_type='J') or business number (card_type='S')
            card_expire: Card expiry date (YYMM)
            installment: Number of installments (0 = lump sum)
            card_type: 'J' (personal) or 'S' (corporate)

        Returns:
            ReservationResult with success status and message
        """
        pass
