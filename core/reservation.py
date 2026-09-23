# -*- coding: utf-8 -*-
"""예약 재시도 루프 (웹 버튼, 텔레그램 /reserve, 헤드리스가 모두 이걸 쓴다).

Flask 도, 전역 중단 플래그도 모른다. 호출하는 쪽이 다음을 넘겨 준다.

- reporter: 로그·진행 상태·알림을 받는 객체 (`Reporter` 참고). 데스크톱에선
  TelegramService 가 이 역할을 한다.
- should_stop: 매 시도마다 불러서 True 면 멈춘다.
- recover: 로그인이 끊겼을 때 재로그인을 시도하고 (성공 여부, 메시지) 를 돌려준다.

서버 버전에선 사용자마다 reporter/should_stop/recover 를 따로 만들어 넘기면
여러 사람의 매크로가 서로 간섭하지 않는다.
"""
from __future__ import annotations

import random
import time
from typing import Callable, Protocol

from core.clock import all_departed, now_kst, today_kst
from core.rate_limit import clamp_call_interval

try:
    from korail2 import NeedToLoginError as KorailLoginError
except ImportError:
    KorailLoginError = None

try:
    from korail2 import KorailBlockedError
except ImportError:
    KorailBlockedError = None

try:
    import requests as _requests

    #: 요청이 코레일에 닿았는지 알 수 없는 오류 (예약이 됐을 수도 있다)
    UNCERTAIN_ERRORS: tuple = (_requests.Timeout, _requests.ConnectionError, TimeoutError, ConnectionError)
except ImportError:
    UNCERTAIN_ERRORS = (TimeoutError, ConnectionError)

#: 로그인 오류 자동 복구 최대 횟수 (인터넷 문제로 실패한 건 세지 않는다)
MAX_RECOVERY_ATTEMPTS = 5

#: 강제 로그아웃(P058)이 RELOGIN_WINDOW 초 안에 RELOGIN_LIMIT 번 나면 다른 기기(코레일 앱 등)와
#: 서로 로그인을 뺏고 있는 것이다. 계속 다시 로그인하면 그쪽이 계속 튕기므로 한참 쉰다.
RELOGIN_WINDOW = 600
RELOGIN_LIMIT = 3
RELOGIN_PAUSE = (300, 600)
KICKED_MESSAGE = "다른 기기(코레일 앱 등)에서 같은 계정으로 로그인한 것 같아요. 잠시 쉬었다가 다시 시도할게요."

#: 코레일이 막은 것 같은 응답(429/403·HTML·이상한 응답) 뒤 쉬는 시간: 30초부터 두 배씩, 최대 10분.
#: 연달아 BLOCK_NOTIFY_AFTER 번 막히면 알림도 보낸다. 조회가 한 번 성공하면 처음부터.
BLOCK_BACKOFF_START = 30
BLOCK_BACKOFF_MAX = 600
BLOCK_NOTIFY_AFTER = 3

DEPARTED_MESSAGE = "선택한 열차가 모두 출발해서 멈췄어요."

#: 강제 로그아웃 간격을 재는 시계 (테스트가 바꿔 끼운다)
_clock = time.monotonic

# run_reservation 이 돌려주는 "왜 끝났나"
END_SUCCESS = "success"    # 원하는 좌석을 모두 잡았다
END_STOPPED = "stopped"    # 중단 요청 (사용자, 알림 버튼 등)
END_GAVE_UP = "gave_up"    # 다시 로그인하지 못했다 (비밀번호 변경·계정 차단 등)
END_CRASH = "crash"        # 예기치 않은 오류 (부르는 쪽이 정한다)


class Reporter(Protocol):
    """예약 루프가 진행 상황을 알리는 곳."""

    def clear_logs(self) -> None: ...
    def push_log(self, event_type: str, message: str, **extra) -> None: ...
    def set_macro_state(self, running: bool, info: dict | None = None) -> None: ...
    def update_attempt(self, attempt: int) -> None: ...
    def send_message(self, text: str) -> bool: ...
    def send_macro_started(self, train_count: int, trains_summary: str) -> None: ...
    def send_macro_stopped(self) -> None: ...
    def send_reservation_success(self, **info) -> None: ...
    def send_payment_result(self, success: bool, message: str) -> bool: ...


class NotifyingReporter:
    """다른 reporter 에 전부 그대로 넘기면서, 사람이 알아야 할 순간만 notify 로도 알린다.

    notify(kind, title, body) 의 kind 는 'reserved' / 'paid' / 'pay_failed' / 'stopped'.
    서버에선 이걸로 웹 푸시를 보낸다. 알림이 실패해도 매크로는 멈추지 않는다.
    """

    def __init__(self, inner: Reporter, notify: Callable[[str, str, str], None]):
        self._inner = inner
        self._notify = notify
        self._last_problem: str | None = None
        # 마지막으로 잡은 예약의 결제 기한 / 자동결제 여부 (결과 카드에도 쓴다)
        self.pay_deadline: str | None = None
        self.autopay = False

    def __getattr__(self, name):
        return getattr(self._inner, name)

    def _safe_notify(self, kind: str, title: str, body: str) -> None:
        try:
            self._notify(kind, title, body)
        except Exception:
            pass

    def push_log(self, event_type: str, message: str, **extra) -> None:
        # 멈추기 직전 마지막 로그가 오류(예: 복구 포기)일 때만 중단 사유로 쓴다
        self._last_problem = message if event_type == "error" else None
        self._inner.push_log(event_type, message, **extra)

    def send_reservation_success(self, **info) -> None:
        self._inner.send_reservation_success(**info)
        self.pay_deadline = info.get("pay_deadline")
        self.autopay = bool(info.get("autopay"))
        route = f"{info.get('dep_station', '')}→{info.get('arr_station', '')}".strip("→")
        body = " ".join(p for p in (info.get("train_name", ""), info.get("dep_time", ""), route) if p)
        if self.autopay:
            guide = "등록한 카드로 자동결제를 진행하고 있어요."
        else:
            guide = pay_guide(self.pay_deadline)
        self._safe_notify("reserved", "🎉 예약 성공", body + "\n" + guide)

    def send_payment_result(self, success: bool, message: str) -> bool:
        sent = self._inner.send_payment_result(success, message)
        if success:
            self._safe_notify("paid", "💳 자동결제 완료", "자동결제까지 끝났어요. " + message)
        else:
            self._safe_notify("pay_failed", "⚠️ 자동결제 실패",
                              message + "\n" + pay_guide(self.pay_deadline))
        return sent

    def send_macro_stopped(self, *args, **kwargs) -> None:
        self._inner.send_macro_stopped(*args, **kwargs)
        self._safe_notify("stopped", "⏹️ 예약 매크로 중단", self._last_problem or "매크로가 멈췄습니다.")

    def send_alert(self, title: str, body: str) -> None:
        """매크로는 계속 도는데 사람이 알아야 할 일 (다른 기기 로그인, 코레일 차단 등)."""
        try:
            self._inner.send_message(f"{title}\n{body}")
        except Exception:
            pass
        self._safe_notify("warning", title, body)


def payment_deadline(reservation) -> str | None:
    """코레일이 알려준 결제 기한을 사람이 읽는 말로. 모르면 None.

    오늘이면 "14:25", 다른 날이면 "10월 3일 14:25".
    """
    day = getattr(reservation, "buy_limit_date", None)
    hms = getattr(reservation, "buy_limit_time", None)
    if not day or not hms or len(day) != 8 or len(hms) < 4:
        return None
    clock = f"{hms[:2]}:{hms[2:4]}"
    if day == today_kst():
        return clock
    return f"{int(day[4:6])}월 {int(day[6:8])}일 {clock}"


def pay_guide(deadline: str | None) -> str:
    """자동결제를 안 했거나 실패했을 때의 안내 문구."""
    when = f"{deadline}까지" if deadline else "결제 기한 안에"
    return f"{when} 코레일 앱이나 웹에서 직접 결제하세요. 안 하면 예약이 자동으로 취소돼요."


def is_login_error(error: Exception, provider: str = "korail") -> bool:
    """Check if the error is a login-related error that can be recovered."""
    if provider == "korail":
        return bool(KorailLoginError and isinstance(error, KorailLoginError))
    return False


def attempt_payment(service, card: dict, result) -> tuple[bool, str]:
    """Attempt card auto-payment for a just-succeeded reservation.

    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        reservation_obj = result.details.get("reservation")
        if reservation_obj is None:
            return False, "예약은 잡혔지만 결제 정보를 불러오지 못했어요. 코레일 앱에서 결제하세요."

        pay_result = service.pay_with_card(
            reservation_obj,
            card["card_number"],
            card["card_password"],
            card["validation_number"],
            card["card_expire"],
            card.get("installment", 0),
            card.get("card_type", "J"),
        )
        return pay_result.success, pay_result.message
    except Exception as e:
        return False, f"결제 중 오류: {str(e)}"


def is_blocked_error(error: Exception) -> bool:
    """코레일이 요청을 막거나 속도를 제한한 것 같은 오류인가."""
    return bool(KorailBlockedError and isinstance(error, KorailBlockedError))


def block_backoff(streak: int) -> float:
    """연달아 streak 번째 막혔을 때 쉴 시간(초): 30, 60, 120, … 최대 600."""
    return min(BLOCK_BACKOFF_START * 2 ** min(max(streak, 1) - 1, 10), BLOCK_BACKOFF_MAX)


def _duration(seconds: float) -> str:
    return f"{int(seconds)}초" if seconds < 60 else f"{round(seconds / 60)}분"


def _alert(reporter, title: str, body: str) -> None:
    """텔레그램·폰 알림·웹 푸시로 알린다 (reporter 가 send_alert 를 모르면 텔레그램만)."""
    try:
        alert = getattr(reporter, "send_alert", None)
        if callable(alert):
            alert(title, body)
        else:
            reporter.send_message(f"{title}\n{body}")
    except Exception:
        pass


def _sleep_unless_stopped(seconds: float, should_stop: Callable[[], bool]) -> None:
    """seconds 동안 쉬되 중단 요청이 오면 바로 돌아온다."""
    deadline = time.monotonic() + seconds
    while not should_stop() and time.monotonic() < deadline:
        time.sleep(min(0.5, max(0.0, deadline - time.monotonic())))


def run_reservation(
    service,
    selected_trains: list,
    seat_option,
    card: dict | None,
    *,
    reporter: Reporter,
    should_stop: Callable[[], bool],
    recover: Callable[[], tuple[bool | None, str]],
    provider: str = "korail",
    passenger_count: int = 1,
    sequential: bool = False,
    pay: Callable[[object, dict, object], tuple[bool, str]] = attempt_payment,
    call_interval: float | None = None,
) -> None:
    """Reservation retry loop, run on a background daemon thread.

    Running on a thread (rather than tied to the lifetime of an SSE HTTP response) means
    the macro keeps going even if the browser tab is closed or the page is refreshed -
    clients just reconnect and replay the reporter's log buffer.

    :param passenger_count: total adult seats wanted (1 or 2)
    :param sequential: when passenger_count > 1, reserve one seat at a time instead of
        both together in a single call - each success is paid immediately and the loop
        keeps going for the remaining seat(s). Aimed at catching sporadic single-seat
        cancellations, which show up far more often than two seats freeing up at once.
    :param call_interval: 코레일 API 호출 사이 간격(초, 1~3). 주면 이번 실행 동안만
        서비스의 호출 간격을 바꾸고 끝나면 되돌린다. None 이면 지금 설정 그대로.
    :param recover: 재로그인. (True, 메시지) 성공 / (False, 메시지) 실패 /
        (None, 메시지) 인터넷 문제 같은 일시적 실패 — 포기 횟수에 넣지 않고 계속 시도한다.
    :return: 끝난 이유 (END_SUCCESS / END_STOPPED / END_GAVE_UP). 고른 열차가 모두
        떠나서 멈추면 END_STOPPED 이고, 마지막 오류 로그(DEPARTED_MESSAGE)가 중단 사유가 된다.
    """
    previous_interval = None
    chosen_interval = None
    if call_interval is not None and hasattr(service, "set_call_interval"):
        previous_interval = service.call_interval
        chosen_interval = clamp_call_interval(call_interval)
        service.set_call_interval(chosen_interval)
    # 여러 쪽 조회 도중에도 중단이 바로 먹도록 서비스에 중단 확인을 잠시 쥐여 준다
    previous_stop = getattr(service, "should_stop", None)
    if hasattr(service, "should_stop"):
        service.should_stop = should_stop
    try:
        return _run(
            service, selected_trains, seat_option, card, reporter, should_stop, recover,
            provider, passenger_count, sequential, pay,
        )
    finally:
        if hasattr(service, "should_stop"):
            service.should_stop = previous_stop
        # 도는 동안 다른 실행이 간격을 바꿨다면(같은 게이트를 나눠 쓰는 경우) 그쪽 값을 둔다
        if previous_interval is not None and service.call_interval == chosen_interval:
            service.set_call_interval(previous_interval)


def _check_reserved(service, train, exclude: set) -> tuple[object | None, bool]:
    """예약 응답이 끊긴 열차가 실제로 예약됐는지 내 예약 목록에서 확인한다.

    :return: (찾은 ReservationResult 또는 None, 확인했는지). 확인 자체가 실패하면
        (None, False) — 모르는 채로 또 예약하지 않도록 부르는 쪽이 다음 시도에 다시 본다.
        로그인 만료·차단은 그대로 올려서 예약 루프의 복구·쉬기 로직이 받게 한다.
    """
    finder = getattr(service, "find_reservation", None)
    if finder is None:
        return None, True
    try:
        found = finder(train)
    except Exception as e:
        if is_login_error(e) or is_blocked_error(e):
            raise
        return None, False
    if found is not None and found.reservation_id and found.reservation_id in exclude:
        return None, True  # 순차 예약에서 앞서 잡은 좌석
    return found, True


def _run(
    service, selected_trains, seat_option, card, reporter, should_stop, recover,
    provider, passenger_count, sequential, pay,
) -> str:
    tg = reporter
    end_reason = END_STOPPED

    # Reservations already confirmed this run (only ever >1 entry in sequential mode -
    # a one-shot multi-seat reservation is a single entry that already covers every seat)
    secured_reservations: list = []
    seats_secured = 0

    # 순차 예약에서 첫 좌석을 잡은 열차. 일행이 서로 다른 열차에 타는 일이 없도록
    # 두 번째 좌석부터는 이 열차만 노린다.
    locked_train: dict | None = None

    tg.clear_logs()
    # 선택한 열차는 모두 같은 구간/날짜다 (한 번의 검색 결과에서 고른 것들이므로).
    # 그래서 조회는 시도마다 딱 한 번, 가장 이른 열차 시각부터 가장 늦은 열차 시각까지
    # 한 번에 훑고, 개별 열차는 그 결과 안에서 메모리로 대조한다.
    earliest_train = min(selected_trains, key=lambda t: t["dep_time"])
    latest_train = max(selected_trains, key=lambda t: t["dep_time"])
    trains_summary = ", ".join(
        f"{t['train_name']}({t['dep_time'][:2]}:{t['dep_time'][2:4]})"
        for t in selected_trains
    )
    tg.set_macro_state(
        True,
        {
            "trains": trains_summary,
            "dep": earliest_train.get("dep_station", ""),
            "arr": earliest_train.get("arr_station", ""),
            "date": earliest_train.get("dep_date", ""),
        },
    )
    tg.send_macro_started(len(selected_trains), trains_summary)

    mode_note = ""
    if passenger_count > 1:
        mode_note = " (1인씩 순차 예약)" if sequential else f" ({passenger_count}인 동시 예약)"
    interval = getattr(service, "call_interval", None)
    interval_note = f", 호출 간격 {interval:g}초" if interval else ""
    tg.push_log("log", f"예약 매크로를 시작합니다{mode_note}{interval_note}. 대상: {trains_summary}")

    attempt = 0
    recovery_attempts = 0

    # 강제 로그아웃(P058) 시각들. 다른 기기와 로그인을 뺏고 뺏기는지 본다.
    forced_logouts: list[float] = []
    session_ok = True
    # 코레일이 연달아 막은 횟수 (조회가 한 번 성공하면 0)
    blocked_streak = 0
    # 예약 응답이 끊겨 실제로 잡혔는지 아직 확인 못 한 열차: (train_info, 조회 결과 TrainInfo, 좌석 수)
    unverified: tuple[dict, object, int] | None = None

    def wait_blocked(ts: str, reason: str) -> None:
        nonlocal blocked_streak
        blocked_streak += 1
        delay = block_backoff(blocked_streak)
        tg.push_log(
            "warning",
            f"[{ts}] 코레일이 요청을 잠시 막은 것 같아요 ({reason}). "
            f"{_duration(delay)} 쉬었다가 다시 시도할게요.",
        )
        if blocked_streak == BLOCK_NOTIFY_AFTER:
            _alert(tg, "⚠️ 코레일이 요청을 막고 있어요",
                   "코레일이 요청을 계속 막고 있어요. 점점 길게 쉬면서 다시 시도할게요.")
        _sleep_unless_stopped(delay, should_stop)

    def on_reserved(result, train_info: dict, reserve_count: int) -> bool:
        """좌석을 잡았다. 알림·자동결제까지 하고, 원하는 좌석을 다 잡았으면 True."""
        nonlocal seats_secured, locked_train
        secured_reservations.append(result)
        seats_secured += reserve_count
        train_name = train_info["train_name"]
        dep_time = f"{train_info['dep_time'][:2]}:{train_info['dep_time'][2:4]}"

        # 순차 예약이면 남은 좌석도 반드시 같은 열차에서 잡는다.
        if sequential and locked_train is None:
            locked_train = train_info

        # 여기서부터 무슨 오류가 나도 좌석은 이미 잡혔다. 알림·결제 실패가 예약 루프로
        # 번지면 같은 열차를 또 예약하려 들므로 전부 여기서 삼킨다.
        autopay = bool(card and card.get("auto_pay", True))
        deadline = None
        try:
            progress_note = (
                f" [{seats_secured}/{passenger_count}석]"
                if passenger_count > 1 else ""
            )
            msg = f"예약 성공! {train_name} ({dep_time}){progress_note}"
            deadline = payment_deadline((result.details or {}).get("reservation"))
            tg.push_log("success", msg, reservation_id=result.reservation_id or "",
                        pay_deadline=deadline or "", autopay=autopay)
            if not autopay:
                tg.push_log("warning", pay_guide(deadline))
            tg.send_reservation_success(
                train_name=train_name,
                dep_time=dep_time,
                dep_station=train_info.get("dep_station", ""),
                arr_station=train_info.get("arr_station", ""),
                reservation_id=result.reservation_id or "",
                pay_deadline=deadline,
                autopay=autopay,
            )
        except Exception as e:
            try:
                tg.push_log("warning", f"예약은 잡았지만 알림을 보내지 못했어요 ({type(e).__name__})")
            except Exception:
                pass

        # Pay for this reservation right away rather than waiting for the
        # remaining seat(s) - leaving it unpaid risks hitting the
        # duplicate-unpaid-reservation guard on the next attempt.
        if autopay:
            try:
                tg.push_log("log", "카드 자동결제 시도 중...")
                try:
                    pay_success, pay_message = pay(service, card, result)
                except Exception as e:
                    pay_success, pay_message = False, f"결제 중 오류: {e}. {pay_guide(deadline)}"
                if pay_success:
                    pay_msg = f"결제 완료! {train_name} ({dep_time})"
                    tg.push_log("success", pay_msg)
                else:
                    pay_msg = f"결제 실패: {pay_message}"
                    tg.push_log("error", pay_msg)
                tg.send_payment_result(pay_success, pay_msg)
            except Exception:
                pass

        if seats_secured >= passenger_count:
            # All requested seats secured - reservation is confirmed, so stop
            # the retry loop now (a bug further down must never cause a
            # duplicate attempt).
            if passenger_count > 1:
                tg.push_log("log", f"총 {passenger_count}석 모두 확보 완료!")
            return True
        # Sequential mode, still need more seats - keep the loop going.
        tg.push_log(
            "log",
            f"{seats_secured}/{passenger_count}석 확보. 나머지 좌석은 "
            f"{train_name} ({dep_time}) 에서만 계속 시도합니다...",
        )
        return False

    def secured_ids() -> set:
        return {r.reservation_id for r in secured_reservations if r.reservation_id}

    while not should_stop():
        attempt += 1
        tg.update_attempt(attempt)
        timestamp = now_kst().strftime("%H:%M:%S")

        try:
            # 지난 시도에서 예약 응답이 끊긴 열차: 다시 예약하기 전에 잡혔는지부터 본다
            if unverified is not None:
                train_info, fresh, reserve_count = unverified
                found, checked = _check_reserved(service, fresh, secured_ids())
                label = f"{train_info['train_name']} ({train_info['dep_time'][:2]}:{train_info['dep_time'][2:4]})"
                if not checked:
                    tg.push_log("warning", f"[{timestamp}] {label}: 예약됐는지 아직 확인하지 못했어요 - 다시 확인할게요")
                    time.sleep(1)
                    continue
                unverified = None
                if found is not None:
                    tg.push_log("log", f"[{timestamp}] {label}: 확인해 보니 예약이 되어 있었어요")
                    if on_reserved(found, train_info, reserve_count):
                        return END_SUCCESS
                    continue

            if seats_secured >= passenger_count:
                return END_SUCCESS  # 안전장치: 다 잡았는데 0석 예약을 걸지 않는다

            # 고른 열차가 모두 떠났으면 더 돌 이유가 없다
            if all_departed([locked_train] if locked_train else selected_trains, now_kst()):
                tg.push_log("error", DEPARTED_MESSAGE)
                end_reason = END_STOPPED
                break

            tg.set_macro_state(True, {"current_train": None, "current_time": None})
            tg.push_log("log", f"[{timestamp}] 시도 #{attempt}: 열차 정보 조회 중...")

            # 선택한 열차 중 가장 늦은 것까지 포함될 때까지만 페이지를 넘긴다.
            # (열차를 하나만 골랐거나 열차가 고정됐으면 API 호출 1번으로 끝난다)
            search_from = locked_train or earliest_train
            search_until = (locked_train or latest_train)["dep_time"]
            fresh_trains = service.search(
                dep=earliest_train["dep_station"],
                arr=earliest_train["arr_station"],
                date=earliest_train["dep_date"],
                time=search_from["dep_time"],
                include_no_seats=True,
                until_time=search_until,
            )
            blocked_streak = 0
            candidates = [locked_train] if locked_train else selected_trains

            for candidate_idx, train_info in enumerate(candidates):
                if should_stop():
                    break

                train_name = train_info["train_name"]
                dep_time = f"{train_info['dep_time'][:2]}:{train_info['dep_time'][2:4]}"

                # Surface what's being checked right now (esp. useful with multiple
                # candidate trains) so it's visible without opening the log drawer.
                tg.set_macro_state(
                    True,
                    {
                        "current_train": train_name,
                        "current_time": dep_time,
                        "current_index": candidate_idx + 1,
                        "current_total": len(candidates),
                    },
                )

                matching_train = None
                for t in fresh_trains:
                    if (
                        t.train_number == train_info["train_number"]
                        and t.dep_time == train_info["dep_time"]
                    ):
                        matching_train = t
                        break

                if not matching_train:
                    tg.push_log("log", f"[{timestamp}] {train_name} ({dep_time}): 열차를 찾을 수 없음")
                    continue

                if not matching_train.has_seat():
                    tg.push_log("log", f"[{timestamp}] {train_name} ({dep_time}): 좌석 없음")
                    continue

                tg.push_log("log", f"[{timestamp}] {train_name} ({dep_time}): 좌석 있음! 예약 시도 중...")

                # In sequential mode each call only asks for the one remaining seat;
                # in one-shot mode a single call asks for every requested seat at once.
                remaining = passenger_count - seats_secured
                if remaining <= 0:
                    return END_SUCCESS
                reserve_count = 1 if sequential else remaining

                try:
                    result = service.reserve(matching_train, seat_option, passenger_count=reserve_count)
                except Exception as reserve_error:
                    if is_login_error(reserve_error, provider) or is_blocked_error(reserve_error):
                        raise  # 세션 만료·차단: 아래 복구·쉬기 로직으로
                    if not isinstance(reserve_error, UNCERTAIN_ERRORS):
                        tg.push_log("log", f"[{timestamp}] {train_name} ({dep_time}): 예약 오류 - {reserve_error}")
                        continue
                    # 요청은 코레일에 닿았는데 응답만 끊겼을 수 있다. 바로 다시 예약하면
                    # 같은 열차를 두 번 잡을 수 있으니 내 예약 목록부터 확인한다.
                    tg.push_log("warning", f"[{timestamp}] {train_name} ({dep_time}): 예약 응답이 끊겼어요. "
                                           f"실제로 예약됐는지 확인하는 중...")
                    result, checked = _check_reserved(service, matching_train, secured_ids())
                    if not checked:
                        unverified = (train_info, matching_train, reserve_count)
                        tg.push_log("warning", f"[{timestamp}] {train_name} ({dep_time}): 확인하지 못했어요 - "
                                               f"다음 시도에서 다시 확인할게요")
                        break
                    if result is None:
                        tg.push_log("log", f"[{timestamp}] {train_name} ({dep_time}): 예약되지 않았어요 - 계속 시도합니다")
                        continue
                    tg.push_log("log", f"[{timestamp}] {train_name} ({dep_time}): 확인해 보니 예약이 되어 있었어요")

                if result.success:
                    if on_reserved(result, train_info, reserve_count):
                        # 실행 슬롯은 여기서 풀지 않는다. 부르는 쪽이 정리를 다 마친 뒤
                        # 맨 마지막에 푼다 (먼저 풀면 그 틈에 다음 매크로가 뜬다).
                        return END_SUCCESS
                    break
                tg.push_log("log", f"[{timestamp}] {train_name} ({dep_time}): {result.message}")

            if attempt % 1000 == 0:
                tg.send_message(f"🔄 시도 #{attempt} 진행 중...")

        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__
            msg = f"[{timestamp}] 오류 ({error_type}): {error_msg}"

            if is_blocked_error(e):
                wait_blocked(timestamp, error_msg)
            elif is_login_error(e, provider):
                tg.push_log("error", msg)
                if session_ok:
                    # 로그인된 채로 돌다가 튕겼다. 짧은 사이 여러 번이면 다른 기기와 싸우는 중.
                    session_ok = False
                    now = _clock()
                    forced_logouts[:] = [t for t in forced_logouts if now - t < RELOGIN_WINDOW]
                    forced_logouts.append(now)
                    if len(forced_logouts) >= RELOGIN_LIMIT:
                        forced_logouts.clear()
                        pause = random.uniform(*RELOGIN_PAUSE)
                        tg.push_log("warning", f"[{timestamp}] {KICKED_MESSAGE} ({_duration(pause)} 뒤)")
                        _alert(tg, "⚠️ 다른 기기에서 로그인했어요", KICKED_MESSAGE)
                        _sleep_unless_stopped(pause, should_stop)
                        if should_stop():
                            continue

                recovery_attempts += 1
                tg.push_log(
                    "warning",
                    f"[{timestamp}] 코레일 로그인이 끊겼습니다 - 다시 로그인하는 중... ({recovery_attempts}/{MAX_RECOVERY_ATTEMPTS})",
                )
                success, recovery_msg = recover()

                if success is None:
                    # 인터넷이 잠깐 끊겼거나 코레일이 잠시 막은 것: 포기 횟수에 넣지 않고
                    # 쉬었다가 계속 시도
                    recovery_attempts -= 1
                    if getattr(service, "last_login_blocked", False) is True:
                        wait_blocked(timestamp, recovery_msg)
                    else:
                        tg.push_log("warning", f"[{timestamp}] {recovery_msg} - 잠시 후 다시 시도합니다")
                        _sleep_unless_stopped(20, should_stop)
                    continue

                if success:
                    # "success" 로 남기면 화면이 예약 성공으로 착각한다
                    tg.push_log("log", f"[{timestamp}] {recovery_msg} - 예약을 계속합니다")
                    recovery_attempts = 0  # 며칠 도는 동안 여러 번 만료돼도 매번 복구하도록
                    session_ok = True
                    time.sleep(1)
                    continue

                tg.push_log("error", f"[{timestamp}] {recovery_msg}")
                if recovery_attempts >= MAX_RECOVERY_ATTEMPTS:
                    tg.push_log("error", f"[{timestamp}] 다시 로그인하지 못해 예약을 멈춥니다. 앱에서 다시 로그인해 주세요.")
                    end_reason = END_GAVE_UP
                    break
                # 네트워크가 잠깐 끊긴 것일 수 있으니 점점 길게 쉬었다가 (중단은 바로 반영)
                _sleep_unless_stopped(min(5 * recovery_attempts, 30), should_stop)
            else:
                tg.push_log("error", msg)
                tg.push_log("warning", f"[{timestamp}] 일시적 오류 - 재시도 중...")
                if attempt % 1000 == 0:
                    tg.send_message(f"⚠️ {msg}")
                time.sleep(1)

        # 재시도 주기는 서비스의 호출 간격 게이트(사용자가 고른 1~3초)가 정한다.
        # 다음 조회가 게이트에서 그만큼 기다리므로, 여기선 기계적인 박자로 보이지
        # 않게 약간만 흔든다.
        time.sleep(random.uniform(0, 0.3))

    tg.send_macro_stopped()
    if seats_secured and seats_secured < passenger_count:
        tg.push_log(
            "stopped",
            f"{seats_secured}/{passenger_count}석 확보한 상태로 예약이 중단되었습니다.",
        )
    else:
        tg.push_log("stopped", "예약이 중단되었습니다.")
    return end_reason
