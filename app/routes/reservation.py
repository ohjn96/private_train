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

            def run_macro():
                global STOP_MACRO
                STOP_MACRO = False

                # Create a fresh service for the macro thread
                macro_service, _ = tg.create_standalone_service()
                if not macro_service:
                    tg.send_message("❌ 서비스 로그인에 실패했습니다.")
                    tg.push_log("error", "서비스 로그인에 실패했습니다.")
                    return

                tg.clear_logs()
                trains_summary = ", ".join(
                    f"{t['train_name']}({t['dep_time'][:2]}:{t['dep_time'][2:4]})"
                    for t in selected_trains
                )
                tg.set_macro_state(
                    True,
                    {
                        "trains": trains_summary,
                        "dep": dep or selected_trains[0].get("dep_station", ""),
                        "arr": arr or selected_trains[0].get("arr_station", ""),
                        "date": date or selected_trains[0].get("dep_date", ""),
                    },
                )
                tg.send_macro_started(len(selected_trains), trains_summary)
                tg.push_log("log", f"예약 매크로를 시작합니다. 대상: {trains_summary}")

                earliest_train = min(selected_trains, key=lambda t: t["dep_time"])
                attempt = 0
                seat_option = SeatOption.GENERAL_FIRST

                while not STOP_MACRO:
                    attempt += 1
                    tg.update_attempt(attempt)
                    timestamp = datetime.now().strftime("%H:%M:%S")
                    try:
                        tg.push_log(
                            "log",
                            f"[{timestamp}] 시도 #{attempt}: 열차 정보 조회 중...",
                        )
                        fresh_trains = macro_service.search(
                            dep=earliest_train["dep_station"],
                            arr=earliest_train["arr_station"],
                            date=earliest_train["dep_date"],
                            time=earliest_train["dep_time"],
                            include_no_seats=True,
                        )

                        for train_info in selected_trains:
                            if STOP_MACRO:
                                break

                            train_name = train_info["train_name"]
                            dep_time_fmt = f"{train_info['dep_time'][:2]}:{train_info['dep_time'][2:4]}"

                            matching_train = None
                            for t in fresh_trains:
                                if (
                                    t.train_number == train_info["train_number"]
                                    and t.dep_time == train_info["dep_time"]
                                ):
                                    matching_train = t
                                    break

                            if not matching_train or not matching_train.has_seat():
                                tg.push_log(
                                    "log",
                                    f"[{timestamp}] {train_name} ({dep_time_fmt}): 좌석 없음",
                                )
                                continue

                            tg.push_log(
                                "log",
                                f"[{timestamp}] {train_name} ({dep_time_fmt}): 좌석 있음! 예약 시도 중...",
                            )
                            try:
                                result = macro_service.reserve(
                                    matching_train, seat_option
                                )
                                if result.success:
                                    success_msg = (
                                        f"예약 성공! {train_name} ({dep_time_fmt})"
                                    )
                                    tg.push_log(
                                        "success",
                                        success_msg,
                                        reservation_id=result.reservation_id or "",
                                    )
                                    tg.send_reservation_success(
                                        train_name=train_name,
                                        dep_time=dep_time_fmt,
                                        dep_station=train_info.get("dep_station", ""),
                                        arr_station=train_info.get("arr_station", ""),
                                        reservation_id=result.reservation_id or "",
                                    )
                                    tg.set_macro_state(False)
                                    STOP_MACRO = True
                                    return
                            except Exception:
                                pass

                        # Status update every 1000 attempts
                        if attempt % 1000 == 0:
                            tg.send_message(f"🔄 시도 #{attempt} 진행 중...")

                    except Exception as e:
                        err_msg = (
                            f"[{timestamp}] 오류 발생 (시도 #{attempt}): {str(e)[:100]}"
                        )
                        tg.push_log("error", err_msg)
                        if attempt % 1000 == 0:
                            tg.send_message(f"⚠️ {err_msg}")
                        time.sleep(1)

                    time.sleep(random.uniform(1, 1.5))

                tg.set_macro_state(False)
                tg.send_macro_stopped()
                tg.push_log("stopped", "예약이 중단되었습니다.")

            macro_thread = threading.Thread(
                target=run_macro, daemon=True, name="tg-macro"
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

    # Store for this provider
    set_selected_indices(provider, [int(i) for i in selected_indices], seat_option)

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


@bp.route("/start_reservation")
@login_required
def start_reservation():
    """SSE endpoint for reservation attempts."""
    # Prevent concurrent macro execution (e.g. Telegram macro already running)
    tg = TelegramService.get_instance()
    if tg._macro_running:

        def error_stream():
            yield f"data: {json.dumps({'type': 'error', 'message': '현재 텔레그램 매크로가 실행 중입니다. 텔레그램에서 /stop 후 다시 시도해주세요.'})}\n\n"

        return Response(error_stream(), mimetype="text/event-stream")

    global STOP_MACRO
    STOP_MACRO = False

    provider = get_current_provider()
    service = ServiceManager.get_service(provider)

    # Get provider-specific search state
    search_state = get_search_state(provider)
    selected_indices = search_state.get("selected_indices", [])
    seat_option_str = search_state.get("seat_option", "GENERAL_FIRST")
    trains_data = search_state.get("trains", [])

    # Convert seat option
    seat_option_map = {
        "GENERAL_FIRST": SeatOption.GENERAL_FIRST,
        "GENERAL_ONLY": SeatOption.GENERAL_ONLY,
        "SPECIAL_FIRST": SeatOption.SPECIAL_FIRST,
        "SPECIAL_ONLY": SeatOption.SPECIAL_ONLY,
    }
    seat_option = seat_option_map.get(seat_option_str, SeatOption.GENERAL_FIRST)

    # Setup telegram remote control callbacks
    _setup_telegram_callbacks()
    tg = TelegramService.get_instance()
    tg.clear_logs()

    def generate():
        global STOP_MACRO
        attempt = 0
        consecutive_errors = 0
        recovery_attempts = 0

        # Collect selected trains info
        selected_trains = []
        for idx in selected_indices:
            if idx < len(trains_data):
                selected_trains.append(trains_data[idx])

        if not selected_trains:
            yield f"data: {json.dumps({'type': 'error', 'message': '선택된 열차가 없습니다.'})}\n\n"
            return

        # Get earliest train for search
        earliest_train = min(selected_trains, key=lambda t: t["dep_time"])

        # Notify Telegram that macro started
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

        # Store params so /restart can re-use them
        tg._last_reserve_params = {
            "dep": earliest_train.get("dep_station", ""),
            "arr": earliest_train.get("arr_station", ""),
            "date": earliest_train.get("dep_date", ""),
            "time": earliest_train.get("dep_time", ""),
            "train_indices": list(range(len(selected_trains))),
            "trains": selected_trains,
        }

        try:
            while not STOP_MACRO:
                attempt += 1
                tg.update_attempt(attempt)
                timestamp = datetime.now().strftime("%H:%M:%S")

                try:
                    # Single search to get all fresh train data
                    msg = f"[{timestamp}] 시도 #{attempt}: 열차 정보 조회 중..."
                    tg.push_log("log", msg)
                    yield f"data: {json.dumps({'type': 'log', 'message': msg})}\n\n"

                    fresh_trains = service.search(
                        dep=earliest_train["dep_station"],
                        arr=earliest_train["arr_station"],
                        date=earliest_train["dep_date"],
                        time=earliest_train["dep_time"],
                        include_no_seats=True,
                    )

                    # Reset error counter on successful query
                    consecutive_errors = 0

                    # Match selected trains with fresh data
                    for train_info in selected_trains:
                        if STOP_MACRO:
                            msg = "예약이 중단되었습니다."
                            tg.push_log("stopped", msg)
                            yield f"data: {json.dumps({'type': 'stopped', 'message': msg})}\n\n"
                            return

                        train_name = train_info["train_name"]
                        dep_time = f"{train_info['dep_time'][:2]}:{train_info['dep_time'][2:4]}"

                        # Find matching train in fresh data
                        matching_train = None
                        for t in fresh_trains:
                            if (
                                t.train_number == train_info["train_number"]
                                and t.dep_time == train_info["dep_time"]
                            ):
                                matching_train = t
                                break

                        if not matching_train:
                            msg = f"[{timestamp}] {train_name} ({dep_time}): 열차를 찾을 수 없음"
                            tg.push_log("log", msg)
                            yield f"data: {json.dumps({'type': 'log', 'message': msg})}\n\n"
                            continue

                        if not matching_train.has_seat():
                            msg = f"[{timestamp}] {train_name} ({dep_time}): 좌석 없음"
                            tg.push_log("log", msg)
                            yield f"data: {json.dumps({'type': 'log', 'message': msg})}\n\n"
                            continue

                        # Found a train with available seats - attempt reservation
                        msg = f"[{timestamp}] {train_name} ({dep_time}): 좌석 있음! 예약 시도 중..."
                        tg.push_log("log", msg)
                        yield f"data: {json.dumps({'type': 'log', 'message': msg})}\n\n"

                        try:
                            result = service.reserve(matching_train, seat_option)

                            if result.success:
                                msg = f"예약 성공! {train_name} ({dep_time})"
                                tg.push_log(
                                    "success",
                                    msg,
                                    reservation_id=result.reservation_id or "",
                                )
                                yield f"data: {json.dumps({'type': 'success', 'message': msg, 'reservation_id': result.reservation_id})}\n\n"
                                # Send Telegram notification
                                tg.send_reservation_success(
                                    train_name=train_name,
                                    dep_time=dep_time,
                                    dep_station=train_info.get("dep_station", ""),
                                    arr_station=train_info.get("arr_station", ""),
                                    reservation_id=result.reservation_id or "",
                                )
                                tg.set_macro_state(False)
                                STOP_MACRO = True
                                return
                            else:
                                msg = f"[{timestamp}] {train_name} ({dep_time}): {result.message}"
                                tg.push_log("log", msg)
                                yield f"data: {json.dumps({'type': 'log', 'message': msg})}\n\n"

                        except Exception as reserve_error:
                            error_msg = str(reserve_error)
                            msg = f"[{timestamp}] {train_name} ({dep_time}): 예약 오류 - {error_msg}"
                            tg.push_log("log", msg)
                            yield f"data: {json.dumps({'type': 'log', 'message': msg})}\n\n"

                            # Check if it's a login-related error that needs recovery
                            if is_login_error(reserve_error, provider):
                                consecutive_errors += 1

                except Exception as e:
                    error_msg = str(e)
                    error_type = type(e).__name__

                    msg = f"[{timestamp}] 오류 ({error_type}): {error_msg}"
                    tg.push_log("error", msg)
                    yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"

                    # Only attempt recovery for login-related errors
                    if is_login_error(e, provider):
                        consecutive_errors += 1

                        # Attempt auto-recovery after login error
                        if recovery_attempts < MAX_RECOVERY_ATTEMPTS:
                            recovery_attempts += 1
                            msg = f"[{timestamp}] 로그인 오류 감지 - 자동 복구 시도 중... ({recovery_attempts}/{MAX_RECOVERY_ATTEMPTS})"
                            tg.push_log("warning", msg)
                            yield f"data: {json.dumps({'type': 'warning', 'message': msg})}\n\n"

                            success, recovery_msg = attempt_recovery(provider, service)

                            if success:
                                msg = f"[{timestamp}] {recovery_msg} - 예약 재시작"
                                tg.push_log("success", msg)
                                yield f"data: {json.dumps({'type': 'success', 'message': msg})}\n\n"
                                consecutive_errors = 0
                                time.sleep(1)  # Brief pause before resuming
                                continue
                            else:
                                msg = f"[{timestamp}] {recovery_msg}"
                                tg.push_log("error", msg)
                                yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"

                                # If max recovery attempts reached, stop
                                if recovery_attempts >= MAX_RECOVERY_ATTEMPTS:
                                    msg = f"[{timestamp}] 최대 복구 시도 횟수 초과. 예약을 중단합니다."
                                    tg.push_log("error", msg)
                                    yield f"data: {json.dumps({'type': 'error', 'message': msg})}\n\n"
                                    STOP_MACRO = True
                                    return
                    else:
                        # For non-login errors, just log and continue without recovery
                        msg = f"[{timestamp}] 일시적 오류 - 재시도 중..."
                        tg.push_log("warning", msg)
                        yield f"data: {json.dumps({'type': 'warning', 'message': msg})}\n\n"
                        time.sleep(1)

                # Wait before next attempt
                time.sleep(random.uniform(1, 1.5))

            tg.set_macro_state(False)
            tg.send_macro_stopped()
            msg = "예약이 중단되었습니다."
            tg.push_log("stopped", msg)
            yield f"data: {json.dumps({'type': 'stopped', 'message': msg})}\n\n"

        finally:
            # Ensure cleanup even if client disconnects (GeneratorExit)
            STOP_MACRO = True
            tg.set_macro_state(False)

    return Response(
        generate(),
        mimetype="text/event-stream",
        headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
    )


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
