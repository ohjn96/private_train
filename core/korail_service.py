# -*- coding: utf-8 -*-
"""Korail train service implementation."""
import sys
import os
from datetime import datetime, timedelta

# Add parent directory to path for korail2 module
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from korail2 import Korail, KorailError, NeedToLoginError, SoldOutError, NoResultsError, ReserveOption, AdultPassenger
from korail2.korail2 import ReservedOnly

from core.base_service import (
    BaseTrainService,
    TrainInfo,
    TrainProvider,
    SeatOption,
    ReservationResult
)
from core.stations import ALL_STATIONS
from core.rate_limit import RateLimiter, korail_api


#: 한 번의 search() 가 넘길 수 있는 최대 페이지 수 (= 최대 API 호출 횟수)
MAX_SEARCH_PAGES = 5


class KorailService(BaseTrainService):
    """Korail train service implementation."""

    def __init__(self, limiter: RateLimiter | None = None):
        # 호출 간격 게이트. 데스크톱은 전역 게이트 하나를 같이 쓰고,
        # 서버는 사용자마다 따로 넘겨 받는다.
        self._limiter = limiter or korail_api
        self._client: Korail | None = None
        self._user_id: str | None = None
        self._password: str | None = None
        self.last_error: str | None = None

    @property
    def call_interval(self) -> float:
        """코레일 API 호출 사이 최소 간격(초)."""
        return self._limiter.min_interval

    def set_call_interval(self, seconds: float) -> None:
        self._limiter.set_interval(seconds)

    def login(self, user_id: str, password: str) -> bool:
        """Login to Korail."""
        self.last_error = None
        try:
            self._limiter.wait()
            self._client = Korail(user_id, password, auto_login=True, want_feedback=False)
            self._user_id = user_id
            self._password = password
            return self._client.logined
        except KorailError as e:
            if e.msg:
                self.last_error = f"코레일 로그인 실패: {e.msg}"
            return False

    def logout(self) -> None:
        """Logout from Korail."""
        if self._client:
            self._limiter.wait()
            self._client.logout()
        self._client = None
        self._user_id = None
        self._password = None

    def is_logged_in(self) -> bool:
        """Check if logged in."""
        return self._client is not None and self._client.logined

    @property
    def credentials(self) -> dict | None:
        """재로그인에 쓸 자격증명. 로그인한 적이 없으면 None.

        웹에서는 Flask 세션이 같은 역할을 하지만, 헤드리스 실행에는 세션이 없어
        서비스 인스턴스가 쥔 것이 유일한 출처가 된다.
        """
        if not self._user_id or not self._password:
            return None
        return {'user_id': self._user_id, 'password': self._password}

    def search(
        self,
        dep: str,
        arr: str,
        date: str,
        time: str,
        include_no_seats: bool = False,
        until_time: str | None = None,
        max_pages: int = MAX_SEARCH_PAGES,
    ) -> list[TrainInfo]:
        """Search Korail trains, paging forward from `time`.

        코레일은 한 번에 10건 정도만 돌려주므로 필요한 만큼만 페이지를 더 넘긴다.
        호출 간격은 korail_api 게이트가 강제하므로 여기서 따로 sleep 하지 않는다.

        :param until_time: 이 시각(HHMMSS)의 열차까지 나올 때까지만 페이지를 넘긴다.
            None 이면 한 페이지만 가져온다. 페이지 한 장이 API 호출 한 번이다.
        :param max_pages: 안전장치. 이 장수를 넘겨 호출하지 않는다.
        """
        if not self._client:
            raise NeedToLoginError()

        all_trains = []
        seen: set[tuple[str, str]] = set()
        current_time = time
        pages = max(1, min(max_pages, MAX_SEARCH_PAGES))

        for _ in range(pages):
            try:
                self._limiter.wait()
                trains = self._client.search_train(
                    dep=dep,
                    arr=arr,
                    date=date,
                    time=current_time,
                    include_no_seats=include_no_seats
                )
            except NoResultsError:
                break
            except Exception:
                # 첫 장부터 실패하면 "열차 없음"으로 숨기지 않고 알린다 (예약 루프는
                # 일시 오류로 보고 다시 시도한다). 뒷장 실패는 받은 데까지만 쓴다.
                if not all_trains:
                    raise
                break

            if not trains:
                break

            # 다음 장은 마지막 열차 시각 그대로 부르므로 겹치는 열차를 걸러낸다
            fresh = [t for t in trains if (t.train_no, t.dep_time) not in seen]
            if not fresh:
                break
            seen.update((t.train_no, t.dep_time) for t in fresh)
            all_trains.extend(fresh)

            last_train = trains[-1]

            # 원하는 시각까지 이미 커버했으면 더 부르지 않는다
            if until_time is None or last_train.dep_time >= until_time:
                break

            # 다음 페이지는 마지막 열차와 같은 시각부터. 1분 뒤부터 부르면 같은 분에
            # 출발하는데 다음 장으로 밀린 열차를 영영 못 본다. (겹치는 건 위에서 거른다)
            if last_train.dep_date != date:
                break
            current_time = last_train.dep_time

        return [self._to_train_info(t) for t in all_trains]

    def reserve(
        self,
        train: TrainInfo,
        seat_option: SeatOption = SeatOption.GENERAL_FIRST,
        passenger_count: int = 1
    ) -> ReservationResult:
        """Reserve a Korail train."""
        if not self._client:
            return ReservationResult(
                success=False,
                message="로그인이 필요합니다."
            )

        try:
            # Convert seat option
            korail_option = self._convert_seat_option(seat_option)

            # Get original train object from raw_data
            original_train = train.raw_data.get('_original')
            if not original_train:
                return ReservationResult(
                    success=False,
                    message="열차 정보를 찾을 수 없습니다."
                )

            passengers = [AdultPassenger(count=passenger_count)]
            # 좌석을 발견한 직후이므로 간격을 기다리지 않고 바로 예약을 건다.
            # (기다리는 사이 좌석이 사라진다) 대신 호출 시각은 게이트에 기록한다.
            self._limiter.note_call()
            reservation = self._client.reserve(original_train, passengers=passengers, option=korail_option)

            # 상세를 못 불러온 예약(ReservedOnly)은 결제할 수 없으므로 결제 정보는 비운다
            full = reservation if reservation is not None and not isinstance(reservation, ReservedOnly) else None
            return ReservationResult(
                success=True,
                message="예약 성공!",
                reservation_id=reservation.rsv_id if reservation else None,
                details={'reservation': full}
            )
        except SoldOutError:
            return ReservationResult(
                success=False,
                message="매진되었습니다."
            )
        except NeedToLoginError:
            # 세션 만료(P058). 실패로 삼키면 재로그인 없이 "좌석 있음 → 예약 실패"를
            # 끝없이 반복하므로, 예약 루프의 복구 로직이 받도록 그대로 올린다.
            raise
        except KorailError as e:
            return ReservationResult(
                success=False,
                message=str(e)
            )

    def get_stations(self) -> list[str]:
        """Get the full station list (코레일 + SRT 노선)."""
        return ALL_STATIONS

    def pay_with_card(
        self,
        reservation,
        card_number: str,
        card_password: str,
        validation_number: str,
        card_expire: str,
        installment: int = 0,
        card_type: str = "J",
    ) -> ReservationResult:
        """Pay for a Korail reservation with a credit card."""
        if not self._client:
            return ReservationResult(success=False, message="로그인이 필요합니다.")

        try:
            self._limiter.wait()
            success = self._client.pay_with_card(
                reservation,
                card_number,
                card_password,
                validation_number,
                card_expire,
                installment,
                card_type,
            )
            if success:
                return ReservationResult(success=True, message="결제 완료!")
            return ReservationResult(success=False, message="결제에 실패했습니다.")
        except KorailError as e:
            return ReservationResult(success=False, message=str(e))

    def _to_train_info(self, train) -> TrainInfo:
        """Convert Korail train to TrainInfo."""
        return TrainInfo(
            provider=TrainProvider.KORAIL,
            train_name=train.train_type_name,
            train_number=train.train_no,
            dep_date=train.dep_date,
            dep_time=train.dep_time,
            arr_date=train.arr_date,
            arr_time=train.arr_time,
            dep_station=train.dep_name,
            arr_station=train.arr_name,
            general_seat_available=train.has_general_seat(),
            special_seat_available=train.has_special_seat(),
            raw_data={'_original': train}
        )

    def _convert_seat_option(self, option: SeatOption) -> str:
        """Convert SeatOption to Korail ReserveOption."""
        mapping = {
            SeatOption.GENERAL_FIRST: ReserveOption.GENERAL_FIRST,
            SeatOption.GENERAL_ONLY: ReserveOption.GENERAL_ONLY,
            SeatOption.SPECIAL_FIRST: ReserveOption.SPECIAL_FIRST,
            SeatOption.SPECIAL_ONLY: ReserveOption.SPECIAL_ONLY,
        }
        return mapping.get(option, ReserveOption.GENERAL_FIRST)
