# -*- coding: utf-8 -*-
"""폰 앱(안드로이드·iPhone) 안에서 도는 파이썬 공통 런타임.

공통 웹 화면(webui/)을 폰 안의 127.0.0.1 에 띄우고, 화면은 각 플랫폼의 WebView 가 보여준다.
코레일 호출은 이 폰에서, 이 폰의 IP 로 나간다.

플랫폼과는 bridge 하나로만 주고받는다 (set_bridge 로 꽂는다):
- bridge.onMacroState(running, summary)   매크로가 돌기 시작/멈춤 → 절전 방지, 상단 표시
- bridge.notifyEvent(kind, title, body)   예약 성공·결제·중단 → 폰 알림
- bridge.saveJob(json) / bridge.clearJob() 되살릴 작업 저장/삭제 (플랫폼이 암호화해 둔다)
- bridge.onServerReady(port)              (있으면) 서버가 실제로 연 포트. port=0 으로 시작하면
                                          운영체제가 빈 포트를 고르므로 플랫폼은 이걸로 주소를 안다.
                                          없는 플랫폼은 server_port() 로 물어봐도 된다.
- bridge.batteryStatus() -> json 문자열    (있으면) 배터리 최적화 예외 여부·제조사별 안내.
  bridge.requestBatteryExemption()        (있으면) 예외 요청 화면을 띄운다. 화면은 /__app/battery 로 쓴다.

서버 확인 (/__hello):
- 토큰을 쿠키로 보내기 전에, 그 포트에 떠 있는 게 정말 우리 서버인지 확인한다.
  GET /__hello?nonce=<무작위> → {"mac": hex(HMAC-SHA256(key=token, msg=nonce))}.
  토큰 없이 부를 수 있고, 토큰 자체는 알려주지 않는다. 플랫폼은 mac 이 맞을 때만 쿠키를 싣는다.
안드로이드는 Kotlin Bridge(android_main.py), iPhone 은 파이썬 구현(ios app)을 꽂는다.

자동 복구:
- 매크로를 시작하면 작업 내용(열차·좌석·간격·로그인·카드)을 bridge.saveJob 으로 넘긴다.
  플랫폼이 암호화해 저장한다 (안드로이드 Keystore, iPhone 데이터 보호).
- 정상적으로 끝나면(예약 성공·사용자 중단·로그인 포기) bridge.clearJob.
- 좌석을 잡는 순간(결제 전) 바로 bridge.clearJob. 결제 도중 죽어도 같은 열차를 다시 예약하지
  않는다 (중복 예약 방지). 그때 결제는 사용자가 직접 해야 한다 (예약 성공 알림에 안내돼 있다).
  2인 순차 예약에서 첫 좌석만 잡고 죽었다면 나머지 좌석도 자동으로 이어 잡지 않는다.
- 사용자가 로그아웃하면 작업을 지우고, 카드를 지우면 작업(과 도는 매크로)에서도 카드를 뺀다.
- 예기치 않은 오류로 끝나면 잠시 뒤 같은 작업으로 다시 시작한다 (횟수 제한).
- 프로세스가 죽었다 살아나면 플랫폼이 저장된 작업으로 resume_job() 을 부른다.
- /__health 로 서버가 살아 있는지, 매크로가 멈춰 있지 않은지 알려준다 (안드로이드는 30초마다 확인).
  로그·조회가 있을 때마다 진행 시각(heartbeat)을 새로 찍고, 예약·결제 중에는 phase 를 실어
  플랫폼이 그동안은 절대 프로세스를 다시 띄우지 않게 한다 (느린 결제를 멈춘 것으로 오해하지 않게).
"""
import hashlib
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

#: 자동 재개가 서버 준비(_wire_platform)를 기다리는 최대 시간(초)
READY_TIMEOUT = 120

#: 예기치 않은 오류로 끝난 매크로를 다시 시작하는 한도: CRASH_WINDOW 초 안에 CRASH_LIMIT 번
CRASH_LIMIT = 3
CRASH_WINDOW = 30 * 60
_crash_times: list[float] = []

#: 마지막으로 매크로가 뭔가 한 시각 (조회·로그). 헬스체크가 "멈췄는지" 판단하는 데 쓴다
_last_progress = {'at': time.monotonic()}

#: 되돌릴 수 없는 단계: 'reserve'(예약 요청 중) / 'payment'(결제 중·결제 대기). None 이면 조회 중.
#: 이게 있는 동안 플랫폼은 매크로가 느려도 프로세스를 다시 띄우지 않는다.
_phase = {'name': None, 'since': None}
_phase_lock = threading.Lock()

#: 서버가 실제로 연 포트 (start 가 채운다)
_port = {'value': None}
_port_ready = threading.Event()

#: 지금 도는 매크로의 작업. job: 저장한 작업(dict), reserved: 이번 실행에서 좌석을 잡았나
_active = {'job': None, 'reserved': False, 'card': None}
_active_lock = threading.Lock()

#: /__hello 의 nonce 최대 길이
_MAX_NONCE = 128


def start(files_dir: str, port: int, token: str, version: str, debug: bool = False) -> None:
    if _started.is_set():
        return
    _started.set()

    # 설정·세션 키 파일(~/.train_reservation)이 앱 전용 폴더에 생기도록
    os.environ['HOME'] = files_dir
    os.environ['TRAIN_APP_VERSION'] = version
    logging.basicConfig(level=logging.INFO)
    _quiet_request_logs()

    from webui import create_app

    app = create_app(server_mode=False)
    _require_token(app, token)
    _wire_platform()
    _add_health_route(app)
    _add_battery_routes(app)
    if debug:
        _add_debug_routes(app)
    _ready.set()

    _bind(app, port).serve_forever()


def _quiet_request_logs() -> None:
    """요청 로그에 첫 주소 /__auth?t=<토큰> 이 그대로 찍히지 않게 (logcat 은 다른 앱도 읽을 수 있다)."""
    logging.getLogger('werkzeug').setLevel(logging.WARNING)


def _bind(app, port):
    """127.0.0.1 에 서버를 열고 실제 포트를 플랫폼에 알린다.

    port=0: 운영체제가 빈 포트를 고른다. 고정 포트는 다른 앱이 먼저 차지하고 우리 행세를
    할 수 있다 (WebView 가 그쪽에 토큰 쿠키를 보내게 된다).
    """
    from werkzeug.serving import make_server

    server = make_server('127.0.0.1', int(port or 0), app, threaded=True)
    actual = server.server_port
    _port['value'] = actual
    _port_ready.set()
    logger.info('app server on 127.0.0.1:%s', actual)
    _report_port(actual)
    return server


def server_port(timeout: float | None = None) -> int | None:
    """서버가 실제로 연 포트. 아직 안 떴으면 timeout 초까지 기다린다 (None=계속)."""
    _port_ready.wait(timeout)
    return _port['value']


def hello_mac(token: str, nonce: str) -> str:
    """/__hello 응답. 플랫폼 쪽도 같은 식으로 계산해 비교한다."""
    return hmac.new(token.encode(), nonce.encode(), hashlib.sha256).hexdigest()


def _report_port(port: int) -> None:
    try:
        ready = getattr(_bridge(), 'onServerReady', None)
    except RuntimeError:
        return
    if ready is None:
        return
    try:
        ready(port)
    except Exception:  # noqa: BLE001
        logger.exception('onServerReady failed')


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
        if request.endpoint in ('app_auth', 'app_hello'):
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

    @app.route('/__hello', endpoint='app_hello')
    def app_hello():
        """이 포트의 서버가 토큰을 아는 우리 서버인지 증명한다 (토큰은 내보내지 않는다)."""
        nonce = request.args.get('nonce', '')
        if not nonce or len(nonce) > _MAX_NONCE:
            return {'error': 'nonce'}, 400
        resp = {'mac': hello_mac(token, nonce)}, 200, {'Cache-Control': 'no-store'}
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
    original_log = tg.push_log

    def update_attempt(attempt):
        _heartbeat()
        original_attempt(attempt)

    def push_log(event_type, message, **extra):
        # 로그가 나온다 = 매크로가 살아서 뭔가 하고 있다 (재로그인 대기·예약·결제 포함)
        _heartbeat()
        original_log(event_type, message, **extra)

    def try_start_macro_tracked(owner=None):
        started = try_start_macro(owner=owner)
        if started:
            _heartbeat()
        return started

    tg.set_macro_state = set_macro_state
    tg.try_start_macro = try_start_macro_tracked
    tg.update_attempt = update_attempt
    tg.push_log = push_log
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


def _departed(train: dict, now: datetime) -> bool:
    """이미 떠난 열차인가 (날짜만 있으면 그 날이 지났을 때)."""
    dep_date = str(train.get('dep_date') or '')
    dep_time = str(train.get('dep_time') or '')
    if not dep_date:
        return False
    try:
        if len(dep_time) >= 4:
            dep = datetime.strptime(dep_date + dep_time[:4], '%Y%m%d%H%M')
            return dep <= now
        return datetime.strptime(dep_date, '%Y%m%d').date() < now.date()
    except ValueError:
        return False


def _job_is_stale(job: dict, now: datetime | None = None) -> bool:
    """고른 열차가 모두 이미 떠났다면 되살리지 않는다."""
    now = now or datetime.now()
    trains = job.get('trains') or []
    return not trains or all(_departed(t, now) for t in trains)


def _give_up(message: str) -> None:
    try:
        _bridge().clearJob()
        _bridge().notifyEvent('gave_up', '⚠️ 예약 매크로를 다시 시작하지 못했어요', message)
    except Exception:  # noqa: BLE001
        logger.exception('gave_up 알림 실패')


def _start_job(job: dict, resumed: bool) -> None:
    """저장된 작업으로 로그인하고 매크로를 띄운다. 인터넷이 없으면 생길 때까지 기다린다."""
    import requests

    import webui.routes.reservation as reservation
    from core.base_service import SeatOption
    from core.korail_service import KorailService
    from webui.services import ServiceManager
    from webui.services.telegram_service import TelegramService

    # 감시(_supervise_macros)가 붙기 전에 돌리면 작업 저장·삭제·재시작이 빠진다
    if not _ready.wait(READY_TIMEOUT):
        logger.error('서버가 준비되지 않아 작업을 되살리지 않습니다')
        _give_up('앱이 제대로 시작되지 않았어요. 앱을 열어 매크로를 다시 시작해 주세요.')
        return
    if _job_is_stale(job):
        logger.info('이미 떠난 열차라 되살리지 않습니다')
        _bridge().clearJob()
        return

    try:
        service = _FakeService() if job.get('fake') else KorailService()
        wait = 5
        while True:
            try:
                if service.login(job['user_id'], job['password']):
                    break
                # 비밀번호가 바뀌었거나 계정이 막혔다: 계속 시도하면 계정만 잠긴다
                _give_up('코레일 로그인이 거부됐어요. 앱을 열어 다시 로그인해 주세요.')
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
        if not tg.try_start_macro(owner=job.get('owner') or uid):
            return
        if resumed:
            tg.resumed_at = datetime.now().isoformat(timespec='seconds')
            # 로그인까지 되고 매크로 자리를 얻은 뒤에 알린다 (그 전엔 아직 "다시 시작" 이 아니다)
            try:
                _bridge().notifyEvent('resumed', '🔄 예약 매크로를 다시 시작했어요',
                                      '휴대폰이 앱을 정리했거나 재부팅돼서, 하던 매크로를 자동으로 이어서 돌려요.')
            except Exception:  # noqa: BLE001
                logger.exception('resumed 알림 실패')
        reservation.STOP_MACRO = False
        seat_option = SeatOption(job.get('seat_option') or 'GENERAL_FIRST')
    except Exception as e:  # noqa: BLE001 - 깨진 작업·예상 못 한 오류: 조용히 죽지 말고 알린다
        logger.exception('작업을 되살리지 못했습니다')
        tg = TelegramService.get_instance()
        if tg._macro_running and tg.macro_owner == (job.get('owner') or job.get('user_id')):
            tg.set_macro_state(False)
        _give_up(f'예상하지 못한 오류로 다시 시작하지 못했어요 ({type(e).__name__}). 앱을 열어 다시 시작해 주세요.')
        return

    reservation.run_reservation_loop(
        service, 'korail', job['trains'], seat_option,
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

    from webui.utils.session_helper import add_session_listener

    # 여러 번 불려도(테스트) 한 겹만 감싼다
    original = getattr(reservation.run_reservation_loop, '__wrapped_loop__',
                       reservation.run_reservation_loop)

    def supervised(service, provider, selected_trains, seat_option, card,
                   passenger_count=1, sequential=False, call_interval=None, owner=None):
        job = _job_from(service, selected_trains, seat_option, card, passenger_count,
                        sequential, call_interval, owner)
        with _active_lock:
            _active.update(job=job, reserved=False, card=card)
        if job:
            _bridge().saveJob(json.dumps(job, ensure_ascii=False))
        untrack = _track_phases(service, card)
        try:
            reason = original(service, provider, selected_trains, seat_option, card,
                              passenger_count=passenger_count, sequential=sequential,
                              call_interval=call_interval, owner=owner)
        finally:
            untrack()
            _set_phase(None)
            with _active_lock:
                job = _active['job']        # 로그아웃 등으로 도중에 지워졌을 수 있다
                reserved = _active['reserved']
                _active.update(job=None, reserved=False, card=None)
        # 좌석을 이미 잡았다면 절대 다시 돌리지 않는다 (같은 열차를 또 예약하게 된다)
        if reason == END_CRASH and job and not reserved and _crash_budget_left():
            tg = TelegramService.get_instance()
            tg.push_log('warning', '10초 뒤 자동으로 다시 시작합니다...')
            threading.Timer(10, _start_job, args=(job, False)).start()
        else:
            # 예약 성공·사용자 중단·로그인 포기, 또는 재시작 한도 초과: 되살리지 않는다
            TelegramService.get_instance().resumed_at = None
            _bridge().clearJob()
        return reason

    supervised.__wrapped_loop__ = original
    # 라우트와 텔레그램은 모듈 전역 이름으로 부르므로 여기만 바꾸면 모두 감싸진다
    reservation.run_reservation_loop = supervised
    reservation.add_macro_listener(_on_macro_event)
    add_session_listener(_on_session_event)


def _on_macro_event(owner, kind, title, body) -> None:
    """좌석을 잡는 순간(결제 전) 저장된 작업을 지운다.

    결제 도중 프로세스가 죽어도 되살아나서 같은 열차를 또 예약하지 않게 한다.
    결제가 남았다면 사용자가 직접 한다 (예약 성공 알림에 결제 기한과 함께 안내된다).
    """
    if kind != 'reserved':
        return
    with _active_lock:
        if _active['job'] is None:
            return
        _active['reserved'] = True
    _bridge().clearJob()


def _on_session_event(event: str, user_id: str | None) -> None:
    """로그아웃하면 되살릴 작업을 지우고, 카드를 지우면 작업·도는 매크로에서도 카드를 뺀다."""
    with _active_lock:
        job = _active['job']
        if job is None:
            return
        if user_id is not None and user_id not in (job.get('owner'), job.get('user_id')):
            return  # 남의 로그아웃
        if event == 'logout':
            _active['job'] = None
            action = 'clear'
        elif event == 'card_cleared':
            job['card'] = None
            card = _active.get('card')
            if card:
                card.clear()  # 도는 매크로가 쥔 카드도 비운다 → 자동결제 안 함
            # 좌석을 잡은 뒤라면 저장된 작업은 이미 지워졌다. 다시 살리지 않는다.
            action = None if _active['reserved'] else 'save'
            data = json.dumps(job, ensure_ascii=False)
        else:
            return
    if action == 'clear':
        _bridge().clearJob()
    elif action == 'save':
        _bridge().saveJob(data)


def _add_health_route(app) -> None:
    from webui.services.telegram_service import TelegramService

    @app.route('/__health')
    def health():
        tg = TelegramService.get_instance()
        running = bool(tg._macro_running)
        phase, since = current_phase()
        return {
            'ok': True,
            'macro_running': running,
            'attempt': tg._macro_attempt,
            'stalled_seconds': round(time.monotonic() - _last_progress['at']) if running else 0,
            # 예약·결제 중이면 그 이름. 플랫폼은 이 동안 절대 프로세스를 다시 띄우지 않는다
            'phase': phase,
            'phase_seconds': round(time.monotonic() - since) if phase else 0,
        }


def _add_battery_routes(app) -> None:
    """배터리 최적화 예외 상태·요청 (안드로이드만). 브리지에 없으면 404 → 화면이 줄을 숨긴다."""
    from flask import request

    from webui.routes.reservation import is_same_origin_request

    @app.route('/__app/battery', methods=['GET', 'POST'])
    def app_battery():
        bridge = _bridge()
        status = getattr(bridge, 'batteryStatus', None)
        ask = getattr(bridge, 'requestBatteryExemption', None)
        if status is None or ask is None:
            return {'supported': False}, 404
        if request.method == 'POST':
            if not is_same_origin_request():
                return {'error': 'origin'}, 403
            try:
                ask()
            except Exception:  # noqa: BLE001
                logger.exception('requestBatteryExemption failed')
                return {'ok': False}, 500
            return {'ok': True}
        try:
            data = json.loads(str(status()))
        except Exception:  # noqa: BLE001
            logger.exception('batteryStatus failed')
            return {'supported': False}, 500
        data['supported'] = True
        return data, 200, {'Cache-Control': 'no-store'}


# ──────────────────────────────────────────────── 진행 표시 (헬스체크용)

def _heartbeat() -> None:
    _last_progress['at'] = time.monotonic()


def _set_phase(name: str | None) -> None:
    with _phase_lock:
        if _phase['name'] != name:
            _phase['name'] = name
            _phase['since'] = time.monotonic() if name else None
    _heartbeat()


def current_phase() -> tuple[str | None, float | None]:
    with _phase_lock:
        return _phase['name'], _phase['since']


def _track_phases(service, card):
    """이번 매크로 동안 service 의 예약·결제 호출을 단계로 표시한다. 되돌리는 함수를 돌려준다.

    - reserve() 동안 'reserve'. 좌석을 잡았고 자동결제가 남았으면 결제가 끝날 때까지 'payment' 로 둔다
      (예약 성공 알림을 보내는 사이에 다시 띄워지면 결제를 못 한다).
    - pay_with_card() 동안 'payment'. 끝나면 풀린다.
    - 다음 조회(search)가 시작되면 풀린다.
    인스턴스에만 덮어쓰고 끝나면 지우므로, 같은 서비스를 쓰는 다른 곳에는 흔적이 남지 않는다.
    """
    wrapped = []

    def wrap(method_name, around):
        original = getattr(service, method_name, None)
        if original is None or getattr(original, '__phase_tracked__', False):
            return
        shadowed = method_name in getattr(service, '__dict__', {})

        def tracked(*args, **kwargs):
            return around(original, *args, **kwargs)

        tracked.__phase_tracked__ = True
        try:
            setattr(service, method_name, tracked)
        except (AttributeError, TypeError):
            return
        wrapped.append((method_name, original, shadowed))

    def around_reserve(original, *args, **kwargs):
        autopay = bool(card and card.get('auto_pay', True))
        _set_phase('reserve')
        result = None
        try:
            result = original(*args, **kwargs)
            return result
        finally:
            _set_phase('payment' if autopay and getattr(result, 'success', False) is True else None)

    def around_pay(original, *args, **kwargs):
        _set_phase('payment')
        try:
            return original(*args, **kwargs)
        finally:
            _set_phase(None)

    def around_search(original, *args, **kwargs):
        _set_phase(None)
        return original(*args, **kwargs)

    wrap('reserve', around_reserve)
    wrap('pay_with_card', around_pay)
    wrap('search', around_search)

    def untrack():
        for method_name, original, shadowed in reversed(wrapped):
            try:
                if shadowed:
                    setattr(service, method_name, original)
                else:
                    delattr(service, method_name)
            except (AttributeError, TypeError):
                pass

    return untrack


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
