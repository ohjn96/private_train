# -*- coding: utf-8 -*-
"""Reservation routes with SSE support."""
import json
import threading
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

from core.reservation import (  # noqa: F401  (attempt_payment 는 예전 경로 호환)
    MAX_RECOVERY_ATTEMPTS,
    attempt_payment,
    is_login_error,
    run_reservation,
)

bp = Blueprint("reservation", __name__)

# Global stop flag for macro
STOP_MACRO = False


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

            if not tg.try_start_macro():
                return {
                    "success": False,
                    "message": "현재 매크로가 실행 중입니다. /stop 후 다시 시도해주세요.",
                }

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

    # 같은 열차가 두 번 넘어오면(데스크톱 행과 모바일 카드가 둘 다 DOM 에 있어서
    # 창 크기를 바꾸며 고르면 생길 수 있다) 같은 열차에 예약을 두 번 걸게 되므로
    # 순서를 지키면서 중복을 제거한다.
    indices: list[int] = []
    for raw in selected_indices:
        try:
            idx = int(raw)
        except (TypeError, ValueError):
            continue
        if idx not in indices:
            indices.append(idx)

    # Store for this provider
    set_selected_indices(provider, indices, seat_option, passenger_count, sequential)

    return jsonify({"success": True, "count": len(indices)})


def _recovery_credentials(provider: str, service) -> dict | None:
    """재로그인에 쓸 자격증명을 찾는다.

    웹에서는 Flask 세션이 출처지만, 헤드리스 실행에는 요청 컨텍스트가 없다.
    그때는 서비스 인스턴스가 로그인할 때 쥔 것을 쓴다.
    """
    try:
        credentials = get_credentials(provider)
        if credentials:
            return credentials
    except RuntimeError:
        pass  # 요청 컨텍스트 밖 (헤드리스)
    return getattr(service, "credentials", None)


def attempt_recovery(provider: str, service) -> tuple[bool, str]:
    """Attempt to recover from connection/login errors.

    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        credentials = _recovery_credentials(provider, service)
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
            try:
                set_auth_state(provider, credentials["user_id"])
            except RuntimeError:
                pass  # 헤드리스에는 갱신할 세션이 없다
            return True, "재로그인 성공"
        else:
            return False, "재로그인 실패"

    except Exception as e:
        return False, f"리커버리 중 오류: {str(e)}"


def run_reservation_loop(
    service, provider: str, selected_trains: list, seat_option, card: dict | None,
    passenger_count: int = 1, sequential: bool = False
):
    """Run the reservation loop and always hand the macro slot back.

    루프 본체는 core.reservation 에 있다. 여기선 데스크톱 앱의 전역 중단 플래그와
    TelegramService(로그·알림)를 붙여 준다.

    스레드 본체에서 예상 못 한 예외가 나도 실행 슬롯(_macro_running)이 True 로
    남아 다시는 매크로를 시작하지 못하는 일이 없도록 해제를 보장한다.
    """
    global STOP_MACRO
    tg = TelegramService.get_instance()
    STOP_MACRO = False
    try:
        run_reservation(
            service, selected_trains, seat_option, card,
            reporter=tg,
            should_stop=lambda: STOP_MACRO,
            recover=lambda: attempt_recovery(provider, service),
            provider=provider,
            passenger_count=passenger_count,
            sequential=sequential,
        )
    finally:
        # 성공·복구 포기로 끝났을 때도 /status 가 '대기 중' 을 보이도록
        STOP_MACRO = True
        tg.set_macro_state(False)


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

    # 검사와 점유를 원자적으로. 버튼 연타나 탭 여러 개에서 동시에 들어와도
    # 매크로가 두 개 뜨지 않는다 (두 개가 뜨면 같은 열차를 중복 예약하게 된다).
    if not tg.try_start_macro():
        return jsonify({"success": False, "message": "이미 매크로가 실행 중입니다."})

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
