# -*- coding: utf-8 -*-
"""Reservation routes with SSE support."""
import json
import logging
import threading

import requests
from datetime import datetime
from functools import wraps
from flask import Blueprint, current_app, request, session, redirect, url_for, Response, jsonify

from webui.services import ServiceManager, SeatOption
from webui.services.telegram_service import TelegramService
from webui.utils.session_helper import (
    current_user_id,
    mask_user,
    get_current_provider,
    is_logged_in,
    get_search_state,
    set_selected_indices,
    get_credentials,
    set_auth_state,
    get_card_settings,
)

from core.rate_limit import DEFAULT_MIN_INTERVAL, clamp_call_interval
from core.reservation import (  # noqa: F401  (attempt_payment 는 예전 경로 호환)
    END_CRASH,
    MAX_RECOVERY_ATTEMPTS,
    NotifyingReporter,
    attempt_payment,
    is_login_error,
    run_reservation,
)

bp = Blueprint("reservation", __name__)
logger = logging.getLogger(__name__)

# Global stop flag for macro
STOP_MACRO = False

#: 매크로의 중요한 순간(예약 성공·결제·중단)을 추가로 받는 곳. 서버의 웹 푸시가 등록한다.
#: fn(owner, kind, title, body)
_macro_listeners: list = []


def add_macro_listener(fn) -> None:
    if fn not in _macro_listeners:
        _macro_listeners.append(fn)


def owns_macro(tg=None) -> bool:
    """지금 요청한 사람이 매크로(와 그 로그)의 주인인가.

    주인이 기록되지 않은 매크로(헤드리스 등)는 누구나 볼 수 있다.
    """
    tg = tg or TelegramService.get_instance()
    owner = tg.macro_owner
    return owner is None or owner == current_user_id()


def busy_message(tg) -> str:
    if owns_macro(tg):
        return "이미 매크로가 실행 중입니다."
    return f"지금 {mask_user(tg.macro_owner)} 님이 사용 중입니다. 끝나면 다시 시도해주세요."


def _setup_telegram_callbacks():
    """Wire up telegram bot commands to macro control."""
    tg = TelegramService.get_instance()

    # Capture current session state while in request context
    # so callbacks can work from the Telegram polling thread (no Flask context)
    # 서버 모드는 텔레그램을 쓰지 않는다. 여러 사람의 비밀번호·카드를 전역 한 곳에
    # 복사해 두면 마지막 사람 것만 남아 섞이고 로그아웃해도 남으므로 아예 두지 않는다.
    try:
        if current_app.config.get("SERVER_MODE"):
            raise LookupError("server mode")
        _provider = None
        if is_logged_in():
            _provider = get_current_provider()
        if not _provider:
            from webui.utils.session_helper import get_any_logged_in_provider

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

            owner = (tg._stored_credentials or {}).get("user_id")
            if not tg.try_start_macro(owner=owner):
                return {
                    "success": False,
                    "message": "현재 매크로가 실행 중입니다. /stop 후 다시 시도해주세요.",
                }
            global STOP_MACRO
            STOP_MACRO = False  # 스레드를 띄우기 전에 (루프 안에서 하면 먼저 온 중단이 지워진다)

            macro_thread = threading.Thread(
                target=run_reservation_loop,
                args=(service, provider, selected_trains, SeatOption.GENERAL_FIRST, card),
                kwargs={"owner": owner},
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
    call_interval = clamp_call_interval(request.form.get("call_interval", DEFAULT_MIN_INTERVAL))

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
    get_search_state(provider)["call_interval"] = call_interval
    session.modified = True

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


def attempt_recovery(provider: str, service, credentials: dict | None = None) -> tuple[bool, str]:
    """Attempt to recover from connection/login errors.

    :param credentials: 매크로를 시작할 때 복사해 둔 자격증명. 아래 logout() 이 서비스가
        쥔 자격증명을 지우므로, 이걸 넘기지 않으면 한 번 실패한 뒤로는 복구할 수 없다.

    Returns:
        tuple: (success: bool, message: str)
    """
    try:
        credentials = credentials or _recovery_credentials(provider, service)
        if not credentials:
            return False, "저장된 로그인 정보가 없습니다."

        # Logout first to clear any stale state
        try:
            service.logout()
        except:
            pass

        # Attempt re-login
        try:
            success = service.login(credentials["user_id"], credentials["password"])
        except requests.RequestException as e:
            # 인터넷 문제는 비밀번호 오류와 다르다: 포기하지 말고 계속 시도하라고 알린다
            return None, f"인터넷 연결 문제로 다시 로그인하지 못했습니다 ({type(e).__name__})"
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
    passenger_count: int = 1, sequential: bool = False, call_interval: float | None = None,
    owner: str | None = None,
):
    """Run the reservation loop and always hand the macro slot back.

    루프 본체는 core.reservation 에 있다. 여기선 데스크톱 앱의 전역 중단 플래그와
    TelegramService(로그·알림)를 붙여 준다.

    스레드 본체에서 예상 못 한 예외가 나도 실행 슬롯(_macro_running)이 True 로
    남아 다시는 매크로를 시작하지 못하는 일이 없도록 해제를 보장한다.
    """
    global STOP_MACRO
    tg = TelegramService.get_instance()
    # STOP_MACRO 는 여기서 초기화하지 않는다. 시작하는 쪽이 스레드를 띄우기 전에 한다.
    # (여기서 하면 스레드가 뜨기 전에 눌린 "중단" 이 지워진다)
    credentials = _recovery_credentials(provider, service)

    # 앱을 다시 열었을 때 보여줄 "마지막 결과". 예약·결제·중단 순간을 NotifyingReporter 로 받는다.
    result = {"reason": None, "reserved": None, "paid": None, "pay_message": None, "detail": None}

    def notify(kind, title, body):
        if kind == "reserved":
            result["reserved"] = body.split("\n")[0]
        elif kind == "paid":
            result["paid"], result["pay_message"] = True, body
        elif kind == "pay_failed":
            result["paid"], result["pay_message"] = False, body.split("\n")[0]
        elif kind == "stopped":
            result["detail"] = body
        if owner:
            for listener in list(_macro_listeners):
                listener(owner, kind, title, body)

    reporter = NotifyingReporter(tg, notify)
    reason = END_CRASH

    try:
        reason = run_reservation(
            service, selected_trains, seat_option, card,
            reporter=reporter,
            should_stop=lambda: STOP_MACRO,
            recover=lambda: attempt_recovery(provider, service, credentials),
            provider=provider,
            passenger_count=passenger_count,
            sequential=sequential,
            call_interval=call_interval,
        )
    except Exception as e:  # noqa: BLE001 - 스레드가 조용히 죽지 않게
        logger.exception("reservation loop crashed")
        result["detail"] = str(e)
        try:
            tg.push_log("error", f"예약 매크로가 예기치 않게 멈췄습니다: {e}")
            reporter.send_macro_stopped()
            tg.push_log("stopped", "예약이 중단되었습니다.")
        except Exception:  # noqa: BLE001
            pass
    finally:
        result["reason"] = reason
        result["pay_deadline"] = reporter.pay_deadline
        result["autopay"] = reporter.autopay
        result["ended_at"] = datetime.now().isoformat(timespec="seconds")
        tg.last_result = result
        # 성공·복구 포기로 끝났을 때도 /status 가 '대기 중' 을 보이도록
        STOP_MACRO = True
        tg.set_macro_state(False)
    return reason


def is_same_origin_request() -> bool:
    """다른 사이트가 몰래 보낸 요청(CSRF)인가를 가린다.

    브라우저가 붙이는 Sec-Fetch-Site 를 먼저 보고, 없으면(오래된 브라우저) Origin 의
    호스트가 이 서버 주소와 같은지 본다. 둘 다 없으면 브라우저 밖(스크립트·테스트)이라 통과.
    """
    from urllib.parse import urlsplit

    site = request.headers.get("Sec-Fetch-Site")
    if site is not None:
        return site in ("same-origin", "none")
    origin = request.headers.get("Origin")
    if origin is None:
        return True
    return urlsplit(origin).netloc.lower() == (request.host or "").lower()


@bp.route("/start_reservation", methods=["POST"])
@login_required
def start_reservation():
    """Start the reservation macro on a background thread and return immediately.

    The client watches progress via /macro_stream (the same mechanism used for
    Telegram-triggered macros) instead of an SSE stream tied to this request, so
    closing the tab or refreshing no longer stops the macro - it keeps running on
    the server, and reconnecting just replays the shared log buffer.
    """
    # 예전엔 GET 이라 다른 사이트의 <img src=".../start_reservation"> 만으로도 매크로가
    # 시작됐다. POST 로만 받고, 다른 사이트에서 온 요청은 거절한다.
    if not is_same_origin_request():
        return jsonify({"success": False, "message": "다른 사이트에서 온 요청은 받지 않습니다."}), 403

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
    call_interval = clamp_call_interval(search_state.get("call_interval", DEFAULT_MIN_INTERVAL))

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

    # 검사와 점유를 원자적으로. 버튼 연타나 탭 여러 개에서 동시에 들어와도
    # 매크로가 두 개 뜨지 않는다 (두 개가 뜨면 같은 열차를 중복 예약하게 된다).
    owner = current_user_id()
    if not tg.try_start_macro(owner=owner):
        return jsonify({"success": False, "message": busy_message(tg)})

    # Setup telegram remote control callbacks (also captures session state needed
    # by the thread, e.g. stored credentials/card settings for recovery/payment).
    # 자리를 얻은 뒤에 한다: 바쁠 때 들어온 남의 요청이 전역 정보를 덮어쓰지 않게.
    _setup_telegram_callbacks()

    global STOP_MACRO
    STOP_MACRO = False

    macro_thread = threading.Thread(
        target=run_reservation_loop,
        args=(service, provider, selected_trains, seat_option, card),
        kwargs={
            "passenger_count": passenger_count,
            "sequential": sequential,
            "call_interval": call_interval,
            "owner": owner,
        },
        daemon=True,
        name="web-macro",
    )
    macro_thread.start()

    return jsonify({"success": True})


@bp.route("/macro_stream")
def macro_stream():
    """SSE endpoint for streaming macro logs (Telegram-initiated macros visible on web)."""
    tg = TelegramService.get_instance()

    if not owns_macro(tg):
        # 남의 매크로 로그는 보여주지 않는다
        def nothing():
            yield f"data: {json.dumps({'type': 'stream_end'})}\n\n"
        return Response(nothing(), mimetype="text/event-stream")

    def generate():
        last_id = 0
        idle_count = 0
        while True:
            # Collect new events
            # 매크로 스레드가 동시에 로그를 붙이므로 복사본을 돈다 (deque mutated during iteration)
            new_events = [e for e in list(tg._macro_logs) if e["id"] > last_id]
            for event in new_events:
                last_id = event["id"]
                yield f"data: {json.dumps(event)}\n\n"
                idle_count = 0

            # Check if macro has ended
            if not tg._macro_running:
                # Flush remaining
                remaining = [e for e in list(tg._macro_logs) if e["id"] > last_id]
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
    tg = TelegramService.get_instance()
    if tg._macro_running and not owns_macro(tg):
        return jsonify({"success": False, "message": "다른 사용자의 매크로는 멈출 수 없습니다."}), 403
    STOP_MACRO = True
    return jsonify({"success": True})
