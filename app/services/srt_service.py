# -*- coding: utf-8 -*-
"""SRT train service implementation."""
import sys
import os
import time
from datetime import datetime, timedelta

# Add parent directory to path for SRT module
sys.path.insert(
    0, os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
)

from SRT import SRT, SRTError, SRTNotLoggedInError
from SRT.constants import STATION_NAME
from SRT.seat_type import SeatType

from app.services.base_service import (
    BaseTrainService,
    TrainInfo,
    TrainProvider,
    SeatOption,
    ReservationResult,
)


class SRTService(BaseTrainService):
    """SRT train service implementation."""

    def __init__(self):
        self._client: SRT | None = None
        self._user_id: str | None = None
        self._password: str | None = None

    def login(self, user_id: str, password: str) -> bool:
        """Login to SRT."""
        self.last_error: str | None = None
        try:
            self._client = SRT(user_id, password, auto_login=True, verbose=False)
            self._user_id = user_id
            self._password = password
            return self._client.is_login
        except SRTError as e:
            self.last_error = str(e)
            return False

    def logout(self) -> None:
        """Logout from SRT."""
        if self._client:
            self._client.logout()
        self._client = None
        self._user_id = None
        self._password = None

    def is_logged_in(self) -> bool:
        """Check if logged in."""
        return self._client is not None and self._client.is_login

    def search(
        self, dep: str, arr: str, date: str, time: str, include_no_seats: bool = False
    ) -> list[TrainInfo]:
        """
        Search for SRT trains with pagination-like logic.
        Fetches approx 20 trains (2 iterations).
        """
        if not self._client:
            raise SRTNotLoggedInError()

        all_trains = []
        current_time = time

        # Fetch up to 2 pages (approx 20 trains)
        for _ in range(2):
            try:
                # search_train in srt.py fetches a batch.
                # We don't use time_limit here to avoid fetching too many.
                trains = self._client.search_train(
                    dep=dep,
                    arr=arr,
                    date=date,
                    time=current_time,
                    available_only=not include_no_seats,
                )

                if not trains:
                    break

                all_trains.extend(trains)

                # Add 1.5 second delay to avoid rate limiting (max 40 API calls per minute)
                time.sleep(1.5)

                # Update time for next iteration
                last_train = trains[-1]
                # SRT dep_time is HHMMSS
                last_dt = datetime.strptime(
                    f"{last_train.dep_date}{last_train.dep_time}", "%Y%m%d%H%M%S"
                )
                next_dt = last_dt + timedelta(seconds=1)  # SRT logic usually adds 1 sec
                current_time = next_dt.strftime("%H%M%S")

                # Check if we crossed into next day (though SRT search_train usually handles date)
                # But search params take date and time.
                if next_dt.strftime("%Y%m%d") != date:
                    break

            except Exception:
                break

        return [self._to_train_info(t) for t in all_trains]

    def reserve(
        self, train: TrainInfo, seat_option: SeatOption = SeatOption.GENERAL_FIRST
    ) -> ReservationResult:
        """Reserve an SRT train."""
        if not self._client:
            return ReservationResult(success=False, message="로그인이 필요합니다.")

        try:
            # Convert seat option
            srt_seat_type = self._convert_seat_option(seat_option)

            # Get original train object from raw_data
            original_train = train.raw_data.get("_original")
            if not original_train:
                return ReservationResult(
                    success=False, message="열차 정보를 찾을 수 없습니다."
                )

            reservation = self._client.reserve(
                original_train, special_seat=srt_seat_type
            )

            return ReservationResult(
                success=True,
                message="예약 성공!",
                reservation_id=reservation.reservation_number if reservation else None,
                details={"reservation": reservation},
            )
        except SRTError as e:
            return ReservationResult(success=False, message=str(e))

    def get_stations(self) -> list[str]:
        """Get list of SRT stations."""
        return sorted(STATION_NAME.values())

    def _to_train_info(self, train) -> TrainInfo:
        """Convert SRT train to TrainInfo."""
        return TrainInfo(
            provider=TrainProvider.SRT,
            train_name=train.train_name,
            train_number=train.train_number,
            dep_date=train.dep_date,
            dep_time=train.dep_time,
            arr_date=train.arr_date,
            arr_time=train.arr_time,
            dep_station=train.dep_station_name,
            arr_station=train.arr_station_name,
            general_seat_available=train.general_seat_available(),
            special_seat_available=train.special_seat_available(),
            raw_data={"_original": train, **train.__dict__},
        )

    def _convert_seat_option(self, option: SeatOption) -> SeatType:
        """Convert SeatOption to SRT SeatType."""
        mapping = {
            SeatOption.GENERAL_FIRST: SeatType.GENERAL_FIRST,
            SeatOption.GENERAL_ONLY: SeatType.GENERAL_ONLY,
            SeatOption.SPECIAL_FIRST: SeatType.SPECIAL_FIRST,
            SeatOption.SPECIAL_ONLY: SeatType.SPECIAL_ONLY,
        }
        return mapping.get(option, SeatType.GENERAL_FIRST)
