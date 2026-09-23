# -*- coding: utf-8 -*-
"""안드로이드 앱 안에서 도는 파이썬 진입점 (Chaquopy).

데스크톱 앱(app/)을 그대로 폰 안의 127.0.0.1 에 띄우고, 화면은 WebView 가 보여준다.
코레일 호출은 이 폰에서, 이 폰의 IP 로 나간다.

안드로이드 쪽과 주고받는 것:
- ServerService 가 start() 를 백그라운드 스레드에서 부른다 (서버가 도는 동안 돌아오지 않음)
- 매크로가 돌기 시작/멈추면 Bridge.onMacroState → 절전 방지 잠금, 상단 알림 문구
- 예약 성공·결제·중단은 Bridge.notifyEvent → 안드로이드 알림
"""
import hmac
import logging
import os
import threading

logger = logging.getLogger(__name__)

#: WebView 가 쿠키로 들고 오는 토큰. 같은 폰의 다른 앱이 127.0.0.1 로 붙는 걸 막는다.
TOKEN_COOKIE = 'android_token'

_started = threading.Event()


def start(files_dir: str, port: int, token: str, version: str, debug: bool = False) -> None:
    if _started.is_set():
        return
    _started.set()

    # 설정·세션 키 파일(~/.train_reservation)이 앱 전용 폴더에 생기도록
    os.environ['HOME'] = files_dir
    os.environ['TRAIN_APP_VERSION'] = version
    logging.basicConfig(level=logging.INFO)

    from werkzeug.serving import make_server

    from app import create_app

    app = create_app(server_mode=False)
    _require_token(app, token)
    _wire_android()
    if debug:
        _add_debug_routes(app)

    server = make_server('127.0.0.1', int(port), app, threaded=True)
    logger.info('android server on 127.0.0.1:%s', port)
    server.serve_forever()


def _require_token(app, token: str) -> None:
    from flask import request

    @app.before_request
    def check_token():
        given = request.cookies.get(TOKEN_COOKIE, '')
        if not hmac.compare_digest(given.encode(), token.encode()):
            return 'forbidden', 403
        return None


def _wire_android() -> None:
    from java import jclass

    from app.routes.reservation import add_macro_listener
    from app.services.telegram_service import TelegramService

    bridge = jclass('com.ohjn96.trainreservation.Bridge')

    # 예약 성공·결제·중단 → 안드로이드 알림
    def notify(owner, kind, title, body):
        bridge.notifyEvent(kind, title, body)

    add_macro_listener(notify)

    # 매크로가 도는 동안만 절전 방지 잠금을 쥔다 (안 돌 땐 배터리를 쓰지 않게)
    tg = TelegramService.get_instance()
    last = {'state': None}

    def report():
        running = bool(tg._macro_running)
        summary = (tg._macro_info or {}).get('trains', '') if running else ''
        if last['state'] != (running, summary):
            last['state'] = (running, summary)
            try:
                bridge.onMacroState(running, summary)
            except Exception:
                logger.exception('onMacroState failed')

    original_set = tg.set_macro_state
    original_try = tg.try_start_macro

    def set_macro_state(running, info=None):
        original_set(running, info)
        report()

    def try_start_macro(owner=None):
        started = original_try(owner=owner)
        if started:
            report()
        return started

    tg.set_macro_state = set_macro_state
    tg.try_start_macro = try_start_macro


def _add_debug_routes(app) -> None:
    """디버그 빌드 전용: 코레일 없이 매크로 상태·알림 경로를 시험한다 (토큰은 여전히 필요)."""
    from flask import request

    from app.routes.reservation import _macro_listeners
    from app.services.telegram_service import TelegramService

    @app.route('/__debug/macro')
    def debug_macro():
        tg = TelegramService.get_instance()
        if request.args.get('state') == 'on':
            tg.try_start_macro(owner='debug')
            tg.set_macro_state(True, {'trains': 'KTX 101(08:00)'})
        else:
            tg.set_macro_state(False)
        return {'running': tg._macro_running}

    @app.route('/__debug/notify')
    def debug_notify():
        for listener in list(_macro_listeners):
            listener('debug', 'reserved', '🎉 예약 성공', 'KTX 101 08:00 서울→부산\n결제 기한 안에 결제를 확인하세요.')
        return {'sent': len(_macro_listeners)}
