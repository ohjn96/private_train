# -*- coding: utf-8 -*-
"""Flask Application Factory"""
import hmac
import ipaddress
import logging
import os
import secrets
import threading
import time
from datetime import timedelta
from pathlib import Path
from urllib.parse import urlsplit

from flask import Flask, redirect, render_template, request, session, url_for

logger = logging.getLogger(__name__)

#: FLASK_SECRET_KEY 를 안 줬을 때 무작위로 만든 키를 두는 곳 (텔레그램 설정과 같은 폴더)
SECRET_KEY_PATH = Path.home() / '.train_reservation' / 'secret_key'

#: 접근 비밀번호 없이도 열려야 하는 엔드포인트
_GATE_EXEMPT = {'gate', 'static'}


def _load_secret_key() -> str:
    """세션 서명 키. 코드에 박아 두면 누구나 세션 쿠키를 위조할 수 있다.

    환경변수가 있으면 그걸 쓰고, 없으면 처음 한 번 무작위로 만들어 파일(600)에 남긴다.
    파일에 남기는 건 재시작 뒤에도 기존 쿠키가 유효하도록 하기 위해서다.
    """
    key = os.environ.get('FLASK_SECRET_KEY')
    if key:
        return key
    try:
        return SECRET_KEY_PATH.read_text(encoding='utf-8').strip() or _write_secret_key()
    except OSError:
        return _write_secret_key()


def _write_secret_key() -> str:
    key = secrets.token_urlsafe(48)
    try:
        SECRET_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(SECRET_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(key)
    except OSError as e:
        # 저장을 못 해도 이번 실행은 이 키로 돈다. 재시작하면 다시 로그인하면 된다.
        logger.warning(f"세션 키 저장 실패: {e}")
    return key


def create_app(config_name: str = 'default', server_mode: bool | None = None) -> Flask:
    """Create and configure the Flask application.

    server_mode: 여러 명이 같이 쓰는 서버(`python -m server`)로 띄울 때 True.
    None 이면 SERVER_MODE 환경변수를 본다. 데스크톱(exe)은 늘 False.
    """
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')

    if server_mode is None:
        server_mode = os.environ.get('SERVER_MODE', '').lower() in ('1', 'true', 'yes')
    app.config['SERVER_MODE'] = server_mode

    _install_host_check(app)
    _install_security_headers(app)

    app.secret_key = _load_secret_key()
    # 자바스크립트에서 세션 쿠키를 못 읽게, 다른 사이트에서 온 POST 에는 안 실리게
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')
    if server_mode:
        # 로그인 쿠키는 마지막 요청으로부터 12시간(VAULT_IDLE_HOURS) 뒤에 끝난다.
        # 서버 메모리의 비밀번호·카드도 같은 시간 동안 안 쓰면 지운다 (session_helper).
        from webui.utils.session_helper import VAULT_IDLE_TTL
        app.config['PERMANENT_SESSION_LIFETIME'] = timedelta(seconds=VAULT_IDLE_TTL)
    # HTTPS(tailscale serve 등) 뒤에서만 쓸 땐 쿠키가 평문 HTTP 로 새지 않게
    if os.environ.get('COOKIE_SECURE', '').lower() in ('1', 'true', 'yes'):
        app.config['SESSION_COOKIE_SECURE'] = True

    # 모든 템플릿에서 버전을 쓸 수 있게 (단일 출처는 루트 VERSION 파일)
    from webui.version import get_version

    @app.context_processor
    def inject_version():
        return {'app_version': get_version(), 'server_mode': app.config['SERVER_MODE']}

    _install_access_gate(app)

    # Register blueprints
    from webui.routes import auth, search, reservation, telegram, pwa
    app.register_blueprint(auth.bp)
    app.register_blueprint(search.bp)
    app.register_blueprint(reservation.bp)
    app.register_blueprint(telegram.bp)
    app.register_blueprint(pwa.bp)

    return app


#: 늘 받아 주는 Host (이 컴퓨터·폰 안에서 여는 주소)
_LOCAL_HOSTS = {'localhost', '127.0.0.1', '::1'}


def allowed_hosts() -> tuple[set, list, bool]:
    """(정확히 맞출 이름들, '.example.com' 처럼 끝이 맞으면 되는 것들, 검사 끄기)."""
    exact, suffixes, anything = set(_LOCAL_HOSTS), [], False
    bind = os.environ.get('HOST', '').strip().lower()
    if bind and bind not in ('0.0.0.0', '::'):
        exact.add(bind.strip('[]'))
    for item in os.environ.get('ALLOWED_HOSTS', '').split(','):
        item = item.strip().lower()
        if not item:
            continue
        if item == '*':
            anything = True
        elif item.startswith('.') or item.startswith('*.'):
            suffixes.append('.' + item.lstrip('*.'))
        else:
            exact.add(item.strip('[]'))
    return exact, suffixes, anything


def host_is_allowed(host_header: str) -> bool:
    """DNS 리바인딩 막기: 공격자 도메인(evil.example → 127.0.0.1)으로 온 요청은 거절한다.

    - localhost / 127.0.0.1 / [::1], HOST 로 지정한 주소, ALLOWED_HOSTS(쉼표 구분,
      '.ts.net' 처럼 앞에 점을 붙이면 하위 도메인 전부, '*' 는 검사 끔)
    - IP 주소 그대로 온 요청은 받는다. 리바인딩은 반드시 도메인 이름을 거쳐 오므로
      IP 로 여는 LAN 접속(HOST=0.0.0.0 → http://192.168.0.10:5050)은 안전하다.
    """
    exact, suffixes, anything = allowed_hosts()
    if anything:
        return True
    host = (host_header or '').strip().lower()
    if host.startswith('['):                      # [::1]:5050
        host = host[1:].split(']', 1)[0]
    elif host.count(':') == 1:                    # name:port
        host = host.split(':', 1)[0]
    host = host.rstrip('.')
    if not host:
        return False
    if host in exact or any(host.endswith(s) for s in suffixes):
        return True
    try:
        ipaddress.ip_address(host)
        return True
    except ValueError:
        return False


def _install_host_check(app: Flask) -> None:
    @app.before_request
    def check_host():
        if not host_is_allowed(request.headers.get('Host', '')):
            logger.warning('허용하지 않은 Host 로 온 요청을 거절: %r', request.headers.get('Host'))
            return ('허용되지 않은 주소입니다. 다른 이름으로 접속하려면 ALLOWED_HOSTS 에 추가하세요.',
                    400, {'Content-Type': 'text/plain; charset=utf-8'})
        return None


def safe_next(target: str | None) -> str:
    """/gate?next= 로 받은 되돌아갈 주소. 같은 사이트 경로가 아니면 '/'.

    '//evil.example', '/\\evil.example'(브라우저가 \\ 를 / 로 읽는다), 'https://…',
    제어 문자가 섞인 값 등을 걸러 낸다.
    """
    if not target or not target.startswith('/') or target.startswith('//'):
        return '/'
    if '\\' in target or any(ord(c) < 0x20 or ord(c) == 0x7f for c in target):
        return '/'
    parts = urlsplit(target)
    if parts.scheme or parts.netloc:
        return '/'
    return target


#: 접근 비밀번호 무차별 대입 막기: IP 마다 GATE_WINDOW 초 안에 GATE_MAX_FAILURES 번 틀리면 429
GATE_MAX_FAILURES = 5
GATE_WINDOW = 5 * 60


class _FailureThrottle:
    def __init__(self, limit: int, window: float):
        self.limit, self.window = limit, window
        self._failures: dict[str, list[float]] = {}
        self._lock = threading.Lock()

    def _recent(self, key: str, now: float) -> list[float]:
        times = [t for t in self._failures.get(key, []) if now - t < self.window]
        if times:
            self._failures[key] = times
        else:
            self._failures.pop(key, None)
        return times

    def blocked(self, key: str) -> bool:
        with self._lock:
            return len(self._recent(key, time.monotonic())) >= self.limit

    def fail(self, key: str) -> None:
        with self._lock:
            now = time.monotonic()
            self._failures[key] = self._recent(key, now) + [now]

    def reset(self, key: str) -> None:
        with self._lock:
            self._failures.pop(key, None)


#: 화면이 쓰는 것만 허용하는 CSP. 템플릿에 인라인 <script>·onclick 과 style 이 많고,
#: Tailwind(vendor/tailwindcss.js)가 실행 중에 <style> 을 만들어 'unsafe-inline' 은 필요하다.
#: 그래도 바깥 스크립트 로드, 바깥으로의 fetch/폼 전송, 다른 사이트의 iframe 삽입은 막는다.
CONTENT_SECURITY_POLICY = '; '.join([
    "default-src 'self'",
    "script-src 'self' 'unsafe-inline'",
    "style-src 'self' 'unsafe-inline' https://fonts.googleapis.com",
    "font-src 'self' https://fonts.gstatic.com data:",
    "img-src 'self' data:",
    "connect-src 'self'",
    "object-src 'none'",
    "base-uri 'self'",
    "form-action 'self'",
    "frame-ancestors 'none'",
])


def _install_security_headers(app: Flask) -> None:
    @app.after_request
    def security_headers(resp):
        # 주소(예: 폰 앱의 /__auth?t=토큰)가 바깥 사이트로 Referer 에 실려 나가지 않게
        resp.headers.setdefault('Referrer-Policy', 'no-referrer')
        resp.headers.setdefault('X-Content-Type-Options', 'nosniff')
        resp.headers.setdefault('Content-Security-Policy', CONTENT_SECURITY_POLICY)
        return resp


def _install_access_gate(app: Flask) -> None:
    """APP_PASSWORD 가 설정돼 있으면 모든 페이지 앞에 접근 비밀번호를 세운다.

    서버를 외부(Tailscale 등)에 열었을 때 코레일 로그인 화면까지 아무나 오지 못하게
    막는 1차 관문이다. 로컬에서 혼자 쓸 땐 비워 두면 예전과 똑같이 동작한다.
    """
    password = os.environ.get('APP_PASSWORD', '')
    if not password:
        return

    @app.before_request
    def require_gate():
        from webui.routes.pwa import PUBLIC_ENDPOINTS
        if (request.endpoint in _GATE_EXEMPT or request.endpoint in PUBLIC_ENDPOINTS
                or session.get('gate_ok')):
            return None
        if request.path.startswith('/api/'):
            return {'success': False, 'message': '접근 비밀번호가 필요합니다.'}, 401
        return redirect(url_for('gate', next=request.full_path))

    throttle = _FailureThrottle(GATE_MAX_FAILURES, GATE_WINDOW)
    app.extensions['gate_throttle'] = throttle

    @app.route('/gate', methods=['GET', 'POST'])
    def gate():
        error = None
        if request.method == 'POST':
            ip = request.remote_addr or '?'
            if throttle.blocked(ip):
                return render_template(
                    'gate.html', error='여러 번 틀려서 잠시 막혔습니다. 5분 뒤에 다시 시도하세요.'), 429
            given = request.form.get('password', '')
            if hmac.compare_digest(given.encode(), password.encode()):
                throttle.reset(ip)
                session['gate_ok'] = True
                # 외부 주소로 튕기지 않도록 같은 사이트 경로만 허용
                return redirect(safe_next(request.args.get('next')))
            throttle.fail(ip)
            time.sleep(1)  # 무차별 대입을 느리게
            error = '비밀번호가 틀렸습니다.'
        return render_template('gate.html', error=error)
