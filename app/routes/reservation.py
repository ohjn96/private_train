# -*- coding: utf-8 -*-
"""Reservation routes with SSE support and multi-provider session."""
import json
import random
import threading
import time
from datetime import datetime
from functools import wraps
from flask import Blueprint, request, session, redirect, url_for, Response, jsonify

from app.services import ServiceManager, SeatOption
from app.services.telegram_service import TelegramService
from app.utils.session_helper import (
    get_current_provider,
    is_logged_in,
    get_search_state,
    set_selected_indices,
    get_credentials,
    set_auth_state,
    get_card_settings,
)

# Import exception types for error detection
try:
    from korail2 import NeedToLoginError as KorailLoginError
except ImportError:
    KorailLoginError = None

try:
    from SRT.errors import SRTNotLoggedInError, SRTLoginError
except ImportError:
    SRTNotLoggedInError = None
    SRTLoginError = None

bp = Blueprint("reservation", __name__)

# Global stop flag for macro
STOP_MACRO = False

# Maximum recovery attempts
MAX_RECOVERY_ATTEMPTS = 5


def _setup_telegram_callbacks():
    """Wire up telegram bot commands to macro control."""
    tg = TelegramService.get_instance()

    # Capture current session state while in request context
    # so callbacks can work from the Telegram polling thread (no Flask context)
    try:
        _provider = None
        if is_logged_in():
            _provider = get_current_provider()
        if not _provider:
            from app.utils.session_helper import get_any_logged_in_provider

            _provider = get_any_logged_in_provider()
        if _provider:
            _credentials = get_credentials(_provider)
            if _credentials:
                tg.store_web_session(_provider, _credentials)
            tg.store_card_settings(get_card_settings(_provider))
    except Exception:
        pass  # May not be in request context

    def on_stop():
        global STOP_MACRO
        STOP_MACRO = True

    def on_status() -> str:
        running = "실행 중 ▶️" if not STOP_MACRO else "대기 중 ⏸️"
        return (
            "📊 <b>매크로 상태</b>\n"
            f"상태: {running}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )

    def on_reserve(**kwargs) -> dict:
        """Handle /reserve command from Telegram (runs in background thread)."""
        action = kwargs.get("action")

        # Create standalone service — no Flask context needed
        service, provider = tg.create_standalone_service()
        if not service:
            return {
                "success": False,
                "message": "로그인된 세션이 없습니다. 웹에서 먼저 로그인해주세요.",
            }

        if action == "search":
            try:
                train_results = service.search(
                    dep=kwargs["dep"],
                    arr=kwargs["arr"],
                    date=kwargs["date"],
                    time=kwargs["time"],
                    include_no_seats=True,
                )
                trains_data = [
                    {
                        "index": i,
                        "train_name": t.train_name,
                        "train_number": t.train_number,
                        "dep_date": t.dep_date,
                        "dep_time": t.dep_time,
                        "dep_time_formatted": t.dep_time_formatted,
                        "arr_date": t.arr_date,
                        "arr_time": t.arr_time,
                        "arr_time_formatted": t.arr_time_formatted,
                        "dep_station": t.dep_station,
                        "arr_station": t.arr_station,
                        "general_seat_available": t.general_seat_available,
                        "special_seat_available": t.special_seat_available,
                    }
                    for i, t in enumerate(train_results)
                ]
                # Store for later use by /trains
                tg._last_search_trains = trains_data
                return {"success": True, "trains": trains_data}
            except Exception as e:
                return {"success": False, "message": str(e)}

        elif action == "start":
            # Prevent concurrent macro execution
            if tg._macro_running:
                return {
                    "success": False,
                    "message": "현재 매크로가 실행 중입니다. /stop 후 다시 시도해주세요.",
                }

            # Start reservation macro in background thread
            trains = kwargs.get("trains", [])
            train_indices = kwargs.get("train_indices", [])
            selected_trains = [trains[i] for i in train_indices]

            dep = kwargs.get("dep", "")
            arr = kwargs.get("arr", "")
            date = kwargs.get("date", "")
            time_val = kwargs.get("time", "")

            # Store reserve params for /restart
            tg._last_reserve_params = {
                "dep": dep,
                "arr": arr,
                "date": date,
                "time": time_val,
                "train_indices": train_indices,
                "trains": trains,
            }

            card = tg.get_stored_card_settings()

            macro_thread = threading.Thread(
                target=run_reservation_loop,
                args=(service, provider, selected_trains, SeatOption.GENERAL_FIRST, card),
                daemon=True,
                name="tg-macro",
            )
            macro_thread.start()
            return {"success": True}

        return {"success": False, "message": "알 수 없는 액션"}

    def on_trains() -> str:
        """Return last searched trains list."""
        trains = getattr(tg, "_last_search_trains", None)
        if not trains:
            return "ℹ️ 검색된 열차가 없습니다.\n/reserve 명령어로 먼저 검색해주세요."

        msg = f"🚄 <b>마지막 검색 결과 ({len(trains)}건)</b>\n━━━━━━━━━━━━━━━━━━━\n"
        for i, t in enumerate(trains):
            seat = ""
            seat += "🟢" if t.get("general_seat_available") else "🔴"
            seat += "일반 "
            seat += "🟢" if t.get("special_seat_available") else "🔴"
            seat += "특실"
            dep_t = f"{t['dep_time'][:2]}:{t['dep_time'][2:4]}"
            arr_t = f"{t['arr_time'][:2]}:{t['arr_time'][2:4]}"
            msg += f"\n<b>{i+1}.</b> {t['train_name']} {dep_t}→{arr_t} {seat}"
        return msg

    tg.set_callbacks(
        on_stop=on_stop, on_status=on_status, on_reserve=on_reserve, on_trains=on_trains
    )


def is_login_error(error: Exception, provider: str) -> bool:
    """Check if the error is a login-related error that can be recovered."""
    if provider == "korail":
        return KorailLoginError and isinstance(error, KorailLoginError)
    elif provider == "srt":
        return (SRTNotLoggedInError and isinstance(error, SRTNotLoggedInError)) or (
            SRTLoginError and isinstance(error, SRTLoginError)
        )
    return False


def login_required(f):
    """Decorator to require login."""

    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for("auth.login"))
        return f(*args, **kwargs)

    return decorated_function


@bp.route("/reserve_select", methods=["POST"])
@login_required
def reserve_select():
    """Store selected trains for reservation."""
    provider = get_current_provider()
    selected_indices = request.form.getlist("train_indices[]")
    seat_option = request.form.get("seat_option", "GENERAL_FIRST")

    try:
        passenger_count = max(1, min(2, int(request.form.get("passenger_count", 1))))
    except (TypeError, ValueError):
        passenger_count = 1
    sequential = request.form.get("sequential", "false") == "true"

    # Store for this provider
    set_selected_indices(
        provider, [int(i) for i in selected_indices], seat_option,
        passenger_count, sequential
    )

    return jsonify({"success": True, "count": len(selected_indices)})


def attempt_recovery(provider: str, service) -> tuple[bool, str]:
    """Attempt to recover from connection/login errors.

    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        # Get stored credentials
        credentials = get_credentials(provider)
        if not credentials:
            return False, "저장된 로그인 정보가 없습니다."

        # Logout first to clear any stale state
        try:
            service.logout()
        except:
            pass

        # Attempt re-login
        success = service.login(credentials["user_id"], credentials["password"])
        if success:
            set_auth_state(provider, credentials["user_id"])
            return True, "재로그인 성공"
        else:
            return False, "재로그인 실패"

    except Exception as e:
        return False, f"리커버리 중 오류: {str(e)}"


def run_reservation_loop(
    service, provider: str, selected_trains: list, seat_option, card: dict | None,
    passenger_count: int = 1, sequential: bool = False
):
    """Reservation retry loop, run on a background daemon thread.

    Shared by the web "예약 시작" button and the Telegram /reserve macro. Running on a
    thread (rather than tied to the lifetime of an SSE HTTP response) means the macro
    keeps going even if the browser tab is closed or the page is refreshed - clients
    just reconnect to /macro_stream, which replays the shared log buffer and reports
    whether a macro is still running via TelegramService's state.

    :param passenger_count: total adult seats wanted (1 or 2)
    :param sequential: when passenger_count > 1, reserve one seat at a time instead of
        both together in a single call - each success is paid immediately and the loop
        keeps going for the remaining seat(s). Aimed at catching sporadic single-seat
        cancellations, which show up far more often than two seats freeing up at once.
    """
    global STOP_MACRO
    tg = TelegramService.get_instance()
    STOP_MACRO = False

    # Reservations already confirmed this run (only ever >1 entry in sequential mode -
    # a one-shot multi-seat reservation is a single entry that already covers every seat)
    secured_reservations: list = []
    seats_secured = 0

    tg.clear_logs()
    earliest_train = min(selected_trains, key=lambda t: t["dep_time"])
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
    tg.push_log("log", f"예약 매크로를 시작합니다{mode_note}. 대상: {trains_summary}")

    attempt = 0
    consecutive_errors = 0
    recovery_attempts = 0

    while not STOP_MACRO:
        attempt += 1
        tg.update_attempt(attempt)
        timestamp = datetime.now().strftime("%H:%M:%S")

        try:
            tg.set_macro_state(True, {"current_train": None, "current_time": None})
            tg.push_log("log", f"[{timestamp}] 시도 #{attempt}: 열차 정보 조회 중...")

            fresh_trains = service.search(
                dep=earliest_train["dep_station"],
                arr=earliest_train["arr_station"],
                date=earliest_train["dep_date"],
                time=earliest_train["dep_time"],
                include_no_seats=True,
            )
            consecutive_errors = 0

            for candidate_idx, train_info in enumerate(selected_trains):
                if STOP_MACRO:
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
                        "current_total": len(selected_trains),
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
                reserve_count = 1 if sequential else remaining

                try:
                    result = service.reserve(matching_train, seat_option, passenger_count=reserve_count)

                    if result.success:
                        secured_reservations.append(result)
                        seats_secured += reserve_count
                        progress_note = (
                            f" [{seats_secured}/{passenger_count}석]"
                            if passenger_count > 1 else ""
                        )
                        msg = f"예약 성공! {train_name} ({dep_time}){progress_note}"
                        tg.push_log("success", msg, reservation_id=result.reservation_id or "")
                        tg.send_reservation_success(
                            train_name=train_name,
                            dep_time=dep_time,
                            dep_station=train_info.get("dep_station", ""),
                            arr_station=train_info.get("arr_station", ""),
                            reservation_id=result.reservation_id or "",
                        )

                        # Pay for this reservation right away rather than waiting for the
                        # remaining seat(s) - leaving it unpaid risks hitting the
                        # duplicate-unpaid-reservation guard on the next attempt.
                        if card and card.get("auto_pay", True):
                            tg.push_log("log", "카드 자동결제 시도 중...")
                            pay_success, pay_message = attempt_payment(service, card, result)
                            if pay_success:
                                pay_msg = f"결제 완료! {train_name} ({dep_time})"
                                tg.push_log("success", pay_msg)
                                tg.send_message(f"💳 {pay_msg}")
                            else:
                                pay_msg = f"결제 실패: {pay_message}"
                                tg.push_log("error", pay_msg)
                                tg.send_message(f"⚠️ {pay_msg}")

                        if seats_secured >= passenger_count:
                            # All requested seats secured - reservation is confirmed, so stop
                            # the retry loop now (a bug further down must never cause a
                            # duplicate attempt).
                            STOP_MACRO = True
                            if passenger_count > 1:
                                tg.push_log("success", f"총 {passenger_count}석 모두 확보 완료!")
                            tg.set_macro_state(False)
                            return
                        else:
                            # Sequential mode, still need more seats - keep the loop going.
                            tg.push_log(
                                "log",
                                f"{seats_secured}/{passenger_count}석 확보. 나머지 좌석 계속 시도 중...",
                            )
                            break
                    else:
                        tg.push_log("log", f"[{timestamp}] {train_name} ({dep_time}): {result.message}")

                except Exception as reserve_error:
                    error_msg = str(reserve_error)
                    tg.push_log("log", f"[{timestamp}] {train_name} ({dep_time}): 예약 오류 - {error_msg}")
                    if is_login_error(reserve_error, provider):
                        consecutive_errors += 1

            if attempt % 1000 == 0:
                tg.send_message(f"🔄 시도 #{attempt} 진행 중...")

        except Exception as e:
            error_msg = str(e)
            error_type = type(e).__name__
            msg = f"[{timestamp}] 오류 ({error_type}): {error_msg}"
            tg.push_log("error", msg)

            if is_login_error(e, provider):
                consecutive_errors += 1

                if recovery_attempts < MAX_RECOVERY_ATTEMPTS:
                    recovery_attempts += 1
                    tg.push_log(
                        "warning",
                        f"[{timestamp}] 로그인 오류 감지 - 자동 복구 시도 중... ({recovery_attempts}/{MAX_RECOVERY_ATTEMPTS})",
                    )
                    success, recovery_msg = attempt_recovery(provider, service)

                    if success:
                        tg.push_log("success", f"[{timestamp}] {recovery_msg} - 예약 재시작")
                        consecutive_errors = 0
                        time.sleep(1)
                        continue
                    else:
                        tg.push_log("error", f"[{timestamp}] {recovery_msg}")
                        if recovery_attempts >= MAX_RECOVERY_ATTEMPTS:
                            tg.push_log("error", f"[{timestamp}] 최대 복구 시도 횟수 초과. 예약을 중단합니다.")
                            STOP_MACRO = True
                            break
            else:
                tg.push_log("warning", f"[{timestamp}] 일시적 오류 - 재시도 중...")
                if attempt % 1000 == 0:
                    tg.send_message(f"⚠️ {msg}")
                time.sleep(1)

        time.sleep(random.uniform(1, 1.5))

    tg.set_macro_state(False)
    tg.send_macro_stopped()
    if seats_secured and seats_secured < passenger_count:
        tg.push_log(
            "stopped",
            f"{seats_secured}/{passenger_count}석 확보한 상태로 예약이 중단되었습니다.",
        )
    else:
        tg.push_log("stopped", "예약이 중단되었습니다.")


def attempt_payment(service, card: dict, result) -> tuple[bool, str]:
    """Attempt card auto-payment for a just-succeeded reservation.

    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        reservation_obj = result.details.get("reservation")
        if reservation_obj is None:
            return False, "예약 정보를 찾을 수 없어 결제를 시도할 수 없습니다."

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


@bp.route("/start_reservation")
@login_required
def start_reservation():
    """Start the reservation macro on a background thread and return immediately.

    The client watches progress via /macro_stream (the same mechanism used for
    Telegram-triggered macros) instead of an SSE stream tied to this request, so
    closing the tab or refreshing no longer stops the macro - it keeps running on
    the server, and reconnecting just replays the shared log buffer.
    """
    tg = TelegramService.get_instance()
    if tg._macro_running:
        return jsonify({"success": False, "message": "이미 매크로가 실행 중입니다."})

    provider = get_current_provider()
    service = ServiceManager.get_service(provider)

    # Get provider-specific search state
    search_state = get_search_state(provider)
    selected_indices = search_state.get("selected_indices", [])
    seat_option_str = search_state.get("seat_option", "GENERAL_FIRST")
    trains_data = search_state.get("trains", [])
    passenger_count = max(1, min(2, search_state.get("passenger_count", 1)))
    sequential = bool(search_state.get("sequential", False)) and passenger_count > 1

    selected_trains = [
        trains_data[idx] for idx in selected_indices if idx < len(trains_data)
    ]
    if not selected_trains:
        return jsonify({"success": False, "message": "선택된 열차가 없습니다."})

    # Convert seat option
    seat_option_map = {
        "GENERAL_FIRST": SeatOption.GENERAL_FIRST,
        "GENERAL_ONLY": SeatOption.GENERAL_ONLY,
        "SPECIAL_FIRST": SeatOption.SPECIAL_FIRST,
        "SPECIAL_ONLY": SeatOption.SPECIAL_ONLY,
    }
    seat_option = seat_option_map.get(seat_option_str, SeatOption.GENERAL_FIRST)

    # Must read from Flask session here (request context) - the background thread
    # below has no request context once this view function returns.
    card = get_card_settings(provider)

    # Setup telegram remote control callbacks (also captures session state needed
    # by the thread, e.g. stored credentials/card settings for recovery/payment)
    _setup_telegram_callbacks()

    global STOP_MACRO
    STOP_MACRO = False

    macro_thread = threading.Thread(
        target=run_reservation_loop,
        args=(service, provider, selected_trains, seat_option, card),
        kwargs={"passenger_count": passenger_count, "sequential": sequential},
        daemon=True,
        name="web-macro",
    )
    macro_thread.start()

    return jsonify({"success": True})


@bp.route("/macro_stream")
def macro_stream():
    """SSE endpoint for streaming macro logs (Telegram-initiated macros visible on web)."""
    tg = TelegramService.get_instance()

    def generate():
        last_id = 0
        idle_count = 0
        while True:
            # Collect new events
            new_events = [e for e in tg._macro_logs if e["id"] > last_id]
            for event in new_events:
                last_id = event["id"]
                yield f"data: {json.dumps(event)}\n\n"
                idle_count = 0

            # Check if macro has ended
            if not tg._macro_running:
                # Flush remaining
                remaining = [e for e in tg._macro_logs if e["id"] > last_id]
                for event in remaining:
                    last_id = event["id"]
                    yield f"data: {json.dumps(event)}\n\n"
                if not new_events:
                    idle_count += 1
                if idle_count >= 2:
                    yield f"data: {json.dumps({'type': 'stream_end'})}\n\n"
                    break

            # Wait for new log events
            tg._log_event.clear()
            tg._log_event.wait(timeout=2)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


@bp.route("/stop_macro", methods=["POST"])
@login_required
def stop_macro():
    """Stop the reservation macro."""
    global STOP_MACRO
    STOP_MACRO = True
    return jsonify({"success": True})
