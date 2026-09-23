# -*- coding: utf-8 -*-
"""폰 앱(안드로이드·iPhone) 안에서 도는 파이썬 공통 런타임.

공통 웹 화면(webui/)을 폰 안의 127.0.0.1 에 띄우고, 화면은 각 플랫폼의 WebView 가 보여준다.
코레일 호출은 이 폰에서, 이 폰의 IP 로 나간다.

플랫폼과는 bridge 하나로만 주고받는다 (set_bridge 로 꽂는다):
- bridge.onMacroState(running, summary)   매크로가 돌기 시작/멈춤 → 절전 방지, 상단 표시
- bridge.notifyEvent(kind, title, body)   예약 성공·결제·중단 → 폰 알림
- bridge.saveJob(json) / bridge.clearJob() 되살릴 작업 저장/삭제 (플랫폼이 암호화해 둔다)
안드로이드는 Kotlin Bridge(android_main.py), iPhone 은 파이썬 구현(ios app)을 꽂는다.

자동 복구:
- 매크로를 시작하면 작업 내용(열차·좌석·간격·로그인·카드)을 bridge.saveJob 으로 넘긴다.
  플랫폼이 암호화해 저장한다 (안드로이드 Keystore, iPhone 데이터 보호).
- 정상적으로 끝나면(예약 성공·사용자 중단·로그인 포기) bridge.clearJob.
- 예기치 않은 오류로 끝나면 잠시 뒤 같은 작업으로 다시 시작한다 (횟수 제한).
- 프로세스가 죽었다 살아나면 플랫폼이 저장된 작업으로 resume_job() 을 부른다.
- /__health 로 서버가 살아 있는지, 매크로가 멈춰 있지 않은지 알려준다 (안드로이드는 30초마다 확인).
"""
import hmac
import json
import logging
import os
import threading
import time
from datetime import date, datetime

logger = logging.getLogger(__name__)

#: WebView 가 쿠키로 들고 오는 토큰. 같은 폰의 다른 앱이 127.0.0.1 로 붙는 걸 막는다.
#: (안드로이드는 쿠키를 미리 심고, iPhone 은 첫 주소 /__auth?t=… 로 받는다)
TOKEN_COOKIE = 'app_token'

#: 플랫폼 연결부 (set_bridge)
_platform = {'bridge': None}


def set_bridge(bridge) -> None:
    _platform['bridge'] = bridge

_started = threading.Event()
#: _wire_platform 이 끝났다 (자동 재개는 이걸 기다린 뒤 시작한다)
_ready = threading.Event()

#: 예기치 않은 오류로 끝난 매크로를 다시 시작하는 한도: CRASH_WINDOW 초 안에 CRASH_LIMIT 번
CRASH_LIMIT = 3
CRASH_WINDOW = 30 * 60
_crash_times: list[float] = []

#: 마지막으로 조회를 시도한 시각 (헬스체크가 "멈췄는지" 판단하는 데 쓴다)
_last_progress = {'at': time.monotonic()}


def start(files_dir: str, port: int, token: str, version: str, debug: bool = False) -> None:
    if _started.is_set():
        return
    _started.set()

    # 설정·세션 키 파일(~/.train_reservation)이 앱 전용 폴더에 생기도록
    os.environ['HOME'] = files_dir
    os.environ['TRAIN_APP_VERSION'] = version
    logging.basicConfig(level=logging.INFO)

    from werkzeug.serving import make_server

    from webui import create_app

    app = create_app(server_mode=False)
    _require_token(app, token)
    _wire_platform()
    _add_health_route(app)
    if debug:
        _add_debug_routes(app)
    _ready.set()

    server = make_server('127.0.0.1', int(port), app, threaded=True)
    logger.info('app server on 127.0.0.1:%s', port)
    server.serve_forever()


def resume_job(job_json: str) -> None:
    """프로세스가 다시 뜬 뒤 저장돼 있던 매크로를 이어서 돌린다 (ServerService 가 부른다)."""
    try:
        job = json.loads(job_json)
    except ValueError:
        logger.warning('저장된 작업을 읽지 못했습니다')
        _bridge().clearJob()
        return
    threading.Thread(target=_start_job, args=(job, True), daemon=True, name='resume-job').start()


def stop_macro() -> None:
    """상단 알림의 [매크로 중단]. 프로세스를 죽이지 않고 매크로만 정상적으로 멈춘다."""
    import webui.routes.reservation as reservation
    reservation.STOP_MACRO = True


def _require_token(app, token: str) -> None:
    from flask import redirect, request

    @app.before_request
    def check_token():
        if request.endpoint == 'app_auth':
            return None
        given = request.cookies.get(TOKEN_COOKIE, '')
        if not hmac.compare_digest(given.encode(), token.encode()):
            return 'forbidden', 403
        return None

    @app.route('/__auth', endpoint='app_auth')
    def app_auth():
        """첫 화면: 주소에 실린 토큰을 쿠키로 바꿔 준다 (쿠키를 미리 못 심는 WebView 용)."""
        given = request.args.get('t', '')
        if not hmac.compare_digest(given.encode(), token.encode()):
            return 'forbidden', 403
        resp = redirect('/')
        resp.set_cookie(TOKEN_COOKIE, token, httponly=True, samesite='Strict', path='/')
        return resp


def _wire_platform() -> None:
    from webui.routes.reservation import add_macro_listener
    from webui.services.telegram_service import TelegramService

    bridge = _bridge()

    # 예약 성공·결제·중단 → 폰 알림
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

    original_attempt = tg.update_attempt

    def update_attempt(attempt):
        _last_progress['at'] = time.monotonic()
        original_attempt(attempt)

    def try_start_macro_tracked(owner=None):
        started = try_start_macro(owner=owner)
        if started:
            _last_progress['at'] = time.monotonic()
        return started

    tg.set_macro_state = set_macro_state
    tg.try_start_macro = try_start_macro_tracked
    tg.update_attempt = update_attempt
    _supervise_macros()


def _add_debug_routes(app) -> None:
    """디버그 빌드 전용: 코레일 없이 매크로 상태·알림 경로를 시험한다 (토큰은 여전히 필요)."""
    from flask import request

    from webui.routes.reservation import _macro_listeners
    from webui.services.telegram_service import TelegramService

    @app.route('/__debug/macro')
    def debug_macro():
        tg = TelegramService.get_instance()
        if request.args.get('state') == 'on':
            tg.try_start_macro(owner='debug')
            tg.set_macro_state(True, {'trains': 'KTX 101(08:00)'})
        else:
            tg.set_macro_state(False)
        return {'running': tg._macro_running}

    @app.route('/__debug/fake_job')
    def debug_fake_job():
        """코레일 없이 가짜 매크로를 띄운다 (자동 복구 시험). ?stall=1 이면 조회가 멈춘 흉내."""
        _FakeService.stall = request.args.get('stall') == '1'
        today = date.today().strftime('%Y%m%d')
        job = {
            'user_id': 'debug', 'password': 'debug', 'owner': 'debug', 'fake': True,
            'trains': [{'train_name': 'KTX', 'train_number': '101', 'dep_date': today,
                        'dep_time': '235900', 'dep_station': '서울', 'arr_station': '부산'}],
            'seat_option': 'GENERAL_FIRST', 'card': None, 'passenger_count': 1,
            'sequential': False, 'call_interval': 1,
        }
        threading.Thread(target=_start_job, args=(job, False), daemon=True).start()
        return {'started': True, 'stall': _FakeService.stall}

    @app.route('/__debug/net')
    def debug_net():
        """폰 안의 파이썬이 지금 바깥(코레일)에 닿는지. 절전(Doze) 시험용, 요청 1번."""
        import time as _time
        import requests
        started = _time.monotonic()
        try:
            status = requests.get('https://www.letskorail.com/', timeout=8).status_code
        except Exception as e:  # noqa: BLE001
            status = f'error: {type(e).__name__}'
        return {'status': status, 'seconds': round(_time.monotonic() - started, 2)}

    @app.route('/__debug/notify')
    def debug_notify():
        for listener in list(_macro_listeners):
            listener('debug', 'reserved', '🎉 예약 성공', 'KTX 101 08:00 서울→부산\n결제 기한 안에 결제를 확인하세요.')
        return {'sent': len(_macro_listeners)}


# ──────────────────────────────────────────────── 자동 복구

def _bridge():
    bridge = _platform['bridge']
    if bridge is None:
        raise RuntimeError('mobile_runtime.set_bridge() 를 먼저 불러야 합니다')
    return bridge


def _job_from(service, selected_trains, seat_option, card, passenger_count, sequential,
              call_interval, owner) -> dict | None:
    credentials = getattr(service, 'credentials', None)
    if not credentials:
        return None  # 로그인 정보가 없으면 되살릴 수 없다
    return {
        'user_id': credentials['user_id'],
        'password': credentials['password'],
        'trains': selected_trains,
        'seat_option': getattr(seat_option, 'value', seat_option),
        'card': card,
        'passenger_count': passenger_count,
        'sequential': sequential,
        'call_interval': call_interval,
        'owner': owner,
        'fake': isinstance(service, _FakeService),
    }


def _job_is_stale(job: dict) -> bool:
    """이미 떠난 날짜의 열차라면 되살리지 않는다."""
    today = date.today().strftime('%Y%m%d')
    return all(t.get('dep_date', today) < today for t in job.get('trains') or [{}])


def _start_job(job: dict, resumed: bool) -> None:
    """저장된 작업으로 로그인하고 매크로를 띄운다. 인터넷이 없으면 생길 때까지 기다린다."""
    import requests

    import webui.routes.reservation as reservation
    from core.base_service import SeatOption
    from core.korail_service import KorailService
    from webui.services import ServiceManager
    from webui.services.telegram_service import TelegramService

    _ready.wait(60)
    if _job_is_stale(job):
        logger.info('지난 날짜의 작업이라 되살리지 않습니다')
        _bridge().clearJob()
        return

    service = _FakeService() if job.get('fake') else KorailService()
    wait = 5
    while True:
        try:
            if service.login(job['user_id'], job['password']):
                break
            # 비밀번호가 바뀌었거나 계정이 막혔다: 계속 시도하면 계정만 잠긴다
            _bridge().clearJob()
            _bridge().notifyEvent('gave_up', '⚠️ 예약 매크로를 다시 시작하지 못했어요',
                                  '코레일 로그인이 거부됐어요. 앱을 열어 다시 로그인해 주세요.')
            return
        except requests.RequestException:
            time.sleep(wait)  # 인터넷이 돌아올 때까지 (최대 5분 간격)
            wait = min(wait * 2, 300)

    uid = job['user_id']
    with ServiceManager._cache_lock:
        ServiceManager._services[('korail', uid)] = service  # 다시 로그인하면 이 인스턴스를 같이 쓴다
    tg = TelegramService.get_instance()
    tg.store_web_session('korail', {'user_id': uid, 'password': job['password']})
    tg.store_card_settings(job.get('card'))
    if resumed:
        tg.resumed_at = datetime.now().isoformat(timespec='seconds')
    if not tg.try_start_macro(owner=job.get('owner') or uid):
        return
    reservation.STOP_MACRO = False
    reservation.run_reservation_loop(
        service, 'korail', job['trains'], SeatOption(job.get('seat_option') or 'GENERAL_FIRST'),
        job.get('card'),
        passenger_count=job.get('passenger_count') or 1,
        sequential=bool(job.get('sequential')),
        call_interval=job.get('call_interval'),
        owner=job.get('owner') or uid,
    )


def _crash_budget_left() -> bool:
    now = time.monotonic()
    _crash_times[:] = [t for t in _crash_times if now - t < CRASH_WINDOW]
    if len(_crash_times) >= CRASH_LIMIT:
        return False
    _crash_times.append(now)
    return True


def _supervise_macros() -> None:
    """모든 매크로 시작을 감싸서 작업을 저장하고, 끝난 이유에 따라 지우거나 다시 시작한다."""
    import webui.routes.reservation as reservation
    from core.reservation import END_CRASH
    from webui.services.telegram_service import TelegramService

    original = reservation.run_reservation_loop

    def supervised(service, provider, selected_trains, seat_option, card,
                   passenger_count=1, sequential=False, call_interval=None, owner=None):
        job = _job_from(service, selected_trains, seat_option, card, passenger_count,
                        sequential, call_interval, owner)
        if job:
            _bridge().saveJob(json.dumps(job, ensure_ascii=False))
        reason = original(service, provider, selected_trains, seat_option, card,
                          passenger_count=passenger_count, sequential=sequential,
                          call_interval=call_interval, owner=owner)
        if reason == END_CRASH and job and _crash_budget_left():
            tg = TelegramService.get_instance()
            tg.push_log('warning', '10초 뒤 자동으로 다시 시작합니다...')
            threading.Timer(10, _start_job, args=(job, False)).start()
        else:
            # 예약 성공·사용자 중단·로그인 포기, 또는 재시작 한도 초과: 되살리지 않는다
            TelegramService.get_instance().resumed_at = None
            _bridge().clearJob()
        return reason

    # 라우트와 텔레그램은 모듈 전역 이름으로 부르므로 여기만 바꾸면 모두 감싸진다
    reservation.run_reservation_loop = supervised


def _add_health_route(app) -> None:
    from webui.services.telegram_service import TelegramService

    @app.route('/__health')
    def health():
        tg = TelegramService.get_instance()
        running = bool(tg._macro_running)
        return {
            'ok': True,
            'macro_running': running,
            'attempt': tg._macro_attempt,
            'stalled_seconds': round(time.monotonic() - _last_progress['at']) if running else 0,
        }


class _FakeService:
    """디버그 빌드 전용: 코레일 없이 복구·헬스체크를 시험하는 가짜 서비스."""

    stall = False

    def __init__(self):
        from core.rate_limit import RateLimiter
        self._limiter = RateLimiter(2.0)
        self._user_id = self._password = None

    @property
    def call_interval(self):
        return self._limiter.min_interval

    def set_call_interval(self, seconds):
        self._limiter.set_interval(seconds)

    @property
    def credentials(self):
        return {'user_id': self._user_id, 'password': self._password} if self._user_id else None

    def login(self, user_id, password):
        self._user_id, self._password = user_id, password
        return True

    def logout(self):
        pass

    def is_logged_in(self):
        return True

    def search(self, **kwargs):
        while _FakeService.stall:
            time.sleep(1)  # 네트워크가 멈춘 흉내
        self._limiter.wait()
        return []
