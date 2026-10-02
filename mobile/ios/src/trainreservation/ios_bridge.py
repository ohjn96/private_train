# -*- coding: utf-8 -*-
"""iPhone 연결부 중 iOS API 가 필요 없는 부분 (리눅스에서도 테스트한다: tests/test_ios_bridge.py).

- JobStore        되살릴 매크로 작업을 앱 데이터 폴더의 JSON 파일에 원자적으로 저장
- load_or_create_token  설치마다 만든 무작위 토큰 (WebView 만 서버에 붙게)
- IOSBridge       mobile_runtime.set_bridge() 에 꽂는 객체 (안드로이드 Bridge.kt 와 같은 네 메서드)
- BackgroundKeeper 매크로가 도는 동안 소리 없는 오디오로 앱을 백그라운드에서 깨워 두는 상태 관리
- ServerHost      공통 런타임을 무작위 포트로 띄우고, /__hello 로 우리 서버인지 확인한 뒤에만 토큰을 쓰고,
                  필요하면 소켓을 다시 연다
- is_own_url      WebView 안에서 열어도 되는 주소 (확인한 포트의 127.0.0.1 만)

iOS 호출(절전 방지, 알림, 파일 보호, 오디오)은 native 객체로 주입한다 (실제 구현은 ios_native.py).
"""
import hashlib
import hmac
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

logger = logging.getLogger(__name__)

TOKEN_COOKIE = 'app_token'  # mobile_runtime.TOKEN_COOKIE 와 같아야 한다


def _atomic_write(path: Path, data: bytes, protect=None) -> None:
    """임시 파일에 쓰고 fsync 한 뒤 rename. 도중에 앱이 죽어도 반쪽짜리 파일이 남지 않는다."""
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_name(f'.{path.name}.{os.getpid()}.{threading.get_ident()}.tmp')
    fd = os.open(tmp, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
    try:
        if protect is not None:
            _best_effort(protect, tmp)  # 내용을 쓰기 전에 보호 등급부터 (rename 해도 유지된다)
        with os.fdopen(fd, 'wb') as f:
            fd = None
            f.write(data)
            f.flush()
            os.fsync(f.fileno())
        os.replace(tmp, path)
    finally:
        if fd is not None:
            os.close(fd)
        if tmp.exists():
            tmp.unlink()


def _best_effort(func, *args) -> None:
    try:
        func(*args)
    except Exception:  # noqa: BLE001 - 보호 설정 실패로 저장까지 막지는 않는다
        logger.warning('file protection failed: %s', getattr(func, '__name__', func), exc_info=True)


class JobStore:
    """도는 중인 매크로 작업(열차·좌석·코레일 로그인·카드)을 파일로 둔다.

    안드로이드는 Keystore 키로 암호화한다. iPhone 은 iOS 데이터 보호에 맡긴다:
    protect(path) 로 NSFileProtectionComplete (폰이 잠겨 있으면 읽을 수 없음),
    exclude_from_backup(path) 로 iCloud/PC 백업에서 뺀다. 둘 다 실패해도 저장은 한다.
    """

    def __init__(self, path, protect=None, exclude_from_backup=None):
        self.path = Path(path)
        self._protect = protect
        self._exclude = exclude_from_backup
        self._lock = threading.Lock()

    def save(self, job_json: str) -> None:
        with self._lock:
            _atomic_write(self.path, job_json.encode('utf-8'), self._protect)
            if self._exclude is not None:
                _best_effort(self._exclude, self.path)

    def load(self) -> str | None:
        """저장된 작업. 없거나 깨졌으면 None (깨진 파일은 지운다)."""
        with self._lock:
            try:
                text = self.path.read_text(encoding='utf-8')
            except FileNotFoundError:
                return None
            except OSError:
                logger.warning('저장된 작업을 읽지 못했습니다', exc_info=True)
                return None
            try:
                if not isinstance(json.loads(text), dict):
                    raise ValueError('not an object')
            except ValueError:
                logger.warning('저장된 작업이 깨져 있어 지웁니다')
                self.path.unlink(missing_ok=True)
                return None
            return text

    def clear(self) -> None:
        with self._lock:
            self.path.unlink(missing_ok=True)


def load_or_create_token(path, protect=None) -> str:
    """설치마다 한 번 만드는 무작위 토큰 (안드로이드 AppToken.kt 와 같은 역할)."""
    path = Path(path)
    try:
        token = path.read_text(encoding='utf-8').strip()
        if len(token) >= 32:
            return token
    except FileNotFoundError:
        pass
    token = secrets.token_urlsafe(32)
    _atomic_write(path, token.encode('utf-8'), protect)
    return token


#: 백그라운드 유지(소리 없는 오디오)가 끊겨 곧 iOS 가 앱을 멈출 때 띄우는 알림
KEEPALIVE_LOST_TITLE = '⏸ 백그라운드 유지가 끊겼어요'
KEEPALIVE_LOST_BODY = '앱을 열면 이어서 찾아요.'


class BackgroundKeeper:
    """매크로가 도는 동안 앱이 백그라운드(화면 꺼짐 포함)에서도 돌게 한다.

    iOS 는 뒤로 간 앱을 몇 초 안에 멈추지만, 오디오를 재생 중인 앱(Info.plist 의
    UIBackgroundModes=audio)은 멈추지 않는다. 그래서 소리 없는 파일을 무한 반복 재생한다
    (다른 앱 음악은 끊지 않게 MixWithOthers). 재생이 켜져 있으면 화면이 잠겨도 되므로
    자동 잠금 끄기(idleTimerDisabled)는 재생에 실패했을 때만 쓴다.

    메인 스레드에서만 부른다 (IOSBridge·app.py 가 call_on_main 으로 넘겨 준다).
    audio: start() (실패하면 예외), stop() (여러 번 불러도 됨) — ios_native.AudioKeepAlive.
    set_idle_timer_disabled(bool), notify(kind, title, body), is_foreground() -> bool.
    어느 것이 실패해도 로그만 남기고 계속한다 (오디오가 안 되면 예전처럼 화면을 켜 둔다).
    """

    def __init__(self, audio, set_idle_timer_disabled, notify, is_foreground=lambda: True):
        self.audio = audio
        self._set_idle = set_idle_timer_disabled
        self._notify = notify
        self._is_foreground = is_foreground
        self.wanted = False    # 매크로가 돈다 → 깨워 두고 싶다
        self.active = False    # 지금 소리 없는 오디오가 재생 중
        self._engaged = False  # audio.start 를 부른 뒤 아직 stop 하지 않았다
        self._warned = False   # 이번에 끊긴 건 이미 알렸다

    # ── 매크로 상태

    def set_running(self, running) -> None:
        self.wanted = bool(running)
        if self.wanted:
            if not self._activate():
                self._warn_if_background()
        else:
            self._release()
            self._warned = False
        self._apply_idle()

    # ── iOS 가 알려 주는 일

    def on_interruption(self, began) -> None:
        """전화·시리·다른 앱의 독점 오디오. 시작되면 iOS 가 재생을 멈추고, 끝나면 다시 켠다."""
        if began:
            self.active = False
            if self.wanted and not self._foreground():
                self._warn()  # 끝났다는 알림을 못 받고 멈출 수 있다
        elif self.wanted and not self._activate():
            self._warn_if_background()
        self._apply_idle()

    def on_media_reset(self) -> None:
        """미디어 서비스가 다시 시작됐다: 세션·플레이어를 새로 만들어야 한다."""
        self.active = False
        if self.wanted:
            self._release()
            if not self._activate():
                self._warn_if_background()
        self._apply_idle()

    def on_background(self) -> bool:
        """앱이 뒤로 갔다. 재생 중이면 True. 매크로가 도는데 재생을 못 켜면 알린다."""
        if not self.wanted:
            return False
        if not self._activate():
            self._warn()
        self._apply_idle()
        return self.active

    def on_foreground(self) -> None:
        """다시 앞으로 왔다. 끊겨 있었으면 다시 켠다 (끝남 알림을 못 받은 경우)."""
        if self.wanted:
            self._activate()
        self._apply_idle()

    # ──

    def _activate(self) -> bool:
        if self.active:
            return True
        self._engaged = True
        try:
            self.audio.start()
        except Exception:  # noqa: BLE001 - 오디오가 안 되면 예전처럼 화면을 켜 두고 계속
            logger.warning('background audio start failed', exc_info=True)
            return False
        self.active = True
        self._warned = False
        return True

    def _release(self) -> None:
        self.active = False
        if not self._engaged:
            return
        self._engaged = False
        try:
            self.audio.stop()
        except Exception:  # noqa: BLE001
            logger.warning('background audio stop failed', exc_info=True)

    def _apply_idle(self) -> None:
        # 재생 중이면 화면을 잠가도 된다 (배터리). 재생이 안 되면 화면에 떠 있어야 돌므로 켜 둔다.
        try:
            self._set_idle(self.wanted and not self.active)
        except Exception:  # noqa: BLE001
            logger.warning('idle timer update failed', exc_info=True)

    def _foreground(self) -> bool:
        try:
            return bool(self._is_foreground())
        except Exception:  # noqa: BLE001
            return True

    def _warn_if_background(self) -> None:
        # 앱이 앞에 있으면 알리지 않는다 (화면이 켜진 채로 계속 돈다). 뒤로 갈 때 on_background 가 다시 본다.
        if not self._foreground():
            self._warn()

    def _warn(self) -> None:
        if self._warned:
            return
        self._warned = True
        try:
            self._notify('keepalive', KEEPALIVE_LOST_TITLE, KEEPALIVE_LOST_BODY)
        except Exception:  # noqa: BLE001
            logger.warning('keep-alive notification failed', exc_info=True)


class IOSBridge:
    """mobile_runtime 이 부르는 플랫폼 연결부. 어느 스레드에서든 불린다.

    native: set_idle_timer_disabled(bool), notify(kind, title, body) 를 가진 객체.
            둘 다 UIKit 을 만지므로 call_on_main 으로 메인 스레드에서 부른다.
    call_on_main: 메인 스레드에서 fn(*args) 를 돌리게 예약하는 함수 (app.loop.call_soon_threadsafe).
    on_state: 매크로 상태가 바뀔 때 메인 스레드에서 부를 콜백 (제목 표시 등). 없어도 된다.
    on_server_ready: 서버가 실제로 연 포트를 받을 콜백 (ServerHost.set_port). 없어도 된다.
    keeper: BackgroundKeeper. 있으면 매크로 상태를 여기로 넘기고(소리 없는 오디오 + 필요할 때만
            자동 잠금 끄기), 없으면 매크로가 도는 동안 자동 잠금만 끈다.
    """

    def __init__(self, store: JobStore, native, call_on_main, on_state=None, on_server_ready=None,
                 keeper=None):
        self.store = store
        self.keeper = keeper
        self.native = native
        self._call_on_main = call_on_main
        self._on_state = on_state
        self._on_server_ready = on_server_ready
        self.macro_running = False
        self.macro_summary = ''

    # ── mobile_runtime 이 부르는 네 메서드 (이름은 안드로이드 Bridge 와 같다)

    def onMacroState(self, running, summary):  # noqa: N802 - 공통 bridge 인터페이스
        """매크로가 도는 동안 앱이 멈추지 않게 한다: 소리 없는 오디오로 깨워 두고(keeper),
        그게 안 되면 화면이 저절로 꺼지지 않게 한다 (꺼지면 iOS 가 앱을 멈춘다)."""
        self.macro_running = bool(running)
        self.macro_summary = str(summary or '')
        if self.keeper is not None:
            self._on_main(self.keeper.set_running, self.macro_running)
        else:
            self._on_main(self.native.set_idle_timer_disabled, self.macro_running)
        if self._on_state is not None:
            self._on_main(self._on_state, self.macro_running, self.macro_summary)

    def notifyEvent(self, kind, title, body):  # noqa: N802
        """예약 성공·결제·중단 → 폰 알림. 막지 않는다 (예약 루프 스레드에서 불린다)."""
        self._on_main(self.native.notify, str(kind), str(title), str(body))

    def saveJob(self, job_json):  # noqa: N802
        try:
            self.store.save(str(job_json))
        except Exception:  # noqa: BLE001 - 저장 실패로 매크로를 죽이지 않는다
            logger.exception('saveJob failed')

    def clearJob(self):  # noqa: N802
        try:
            self.store.clear()
        except Exception:  # noqa: BLE001
            logger.exception('clearJob failed')

    def onServerReady(self, port):  # noqa: N802
        """런타임이 실제 포트를 알려줄 때 (포트 0 = 무작위 포트로 띄우는 경우 대비).
        지금 런타임은 부르지 않는다. ServerHost 가 make_server 에서 포트를 직접 읽기도 한다."""
        if self._on_server_ready is not None:
            self._on_server_ready(int(port))

    # ──

    def _on_main(self, fn, *args) -> None:
        def run():
            try:
                fn(*args)
            except Exception:  # noqa: BLE001 - UI 쪽 실패가 파이썬 서버로 번지지 않게
                logger.exception('bridge call failed: %s', getattr(fn, '__name__', fn))
        try:
            self._call_on_main(run)
        except Exception:  # noqa: BLE001 - 이벤트 루프가 이미 닫혔을 때 등
            logger.exception('could not schedule on main thread')


class _NoRedirect(urllib.request.HTTPRedirectHandler):
    """리다이렉트를 따라가지 않는다 (쿠키 헤더가 다른 주소로 따라가지 않게)."""

    def redirect_request(self, req, fp, code, msg, headers, newurl):
        return None


_opener = urllib.request.build_opener(_NoRedirect)


def hello_mac(token: str, nonce: str) -> str:
    """mobile_runtime.hello_mac 과 같은 계산: hex(HMAC-SHA256(key=token, msg=nonce))."""
    return hmac.new(token.encode(), nonce.encode(), hashlib.sha256).hexdigest()


def is_own_url(url: str, port) -> bool:
    """WebView 안에서 열어도 되는 주소인가: 확인을 마친 우리 서버(http://127.0.0.1:<port>)뿐.

    쿠키는 포트를 가리지 않으므로, 같은 127.0.0.1 의 다른 포트(다른 앱일 수 있음)로 가면
    토큰 쿠키가 그쪽으로 간다. 그래서 포트까지 맞아야 한다.
    """
    if not port:
        return False
    try:
        parts = urllib.parse.urlsplit(url or '')
        return (parts.scheme == 'http' and parts.hostname == '127.0.0.1'
                and parts.port == int(port) and not parts.username and not parts.password)
    except ValueError:
        return False


def is_external_web_url(url: str) -> bool:
    """사파리로 넘겨도 되는 바깥 주소 (http/https, 127.0.0.1·localhost 가 아님)."""
    try:
        parts = urllib.parse.urlsplit(url or '')
    except ValueError:
        return False
    return (parts.scheme in ('http', 'https') and bool(parts.hostname)
            and parts.hostname not in ('127.0.0.1', 'localhost', '::1'))


class ServerHost:
    """공통 런타임(mobile_runtime)을 띄우고 지켜본다.

    - 포트는 0(운영체제가 고르는 빈 포트)으로 띄운다. 실제 포트는 make_server 를 감싸 읽거나
      bridge.onServerReady(port) → set_port 로 받는다.
    - 토큰은 그 포트의 서버가 /__hello 로 "토큰을 안다" 고 증명한 뒤에만 보낸다
      (verified_port). 다른 앱이 그 포트를 차지하고 우리 행세를 해도 토큰을 주지 않는다.
    - iOS 는 앱이 뒤로 가서 멈춰(suspend) 있는 동안 듣고 있던 소켓을 회수할 수 있다
      (Apple TN2277). 응답이 없으면 같은 Flask 앱으로 소켓만 새로 연다 (포트가 바뀔 수 있어
      다시 확인한다). 매크로·로그인 상태는 그대로 메모리에 남아 있다.
    """

    def __init__(self, runtime, data_dir, port: int, token: str, version: str, port_file=None):
        self.runtime = runtime            # mobile_runtime 모듈 (테스트에선 가짜)
        self.data_dir = str(data_dir)
        self.start_port = int(port)       # 보통 0
        self.port = None                  # 서버가 실제로 연 포트 (아직 확인 전)
        self.verified_port = None         # /__hello 로 확인을 마친 포트. 토큰은 여기로만 보낸다
        self.token = token
        self.version = version
        self.port_file = Path(port_file) if port_file else None
        self._server = None
        self._make_server_args = None
        self._lock = threading.Lock()

    def set_port(self, port: int) -> None:
        """bridge.onServerReady 가 부른다. 포트가 바뀌면 다시 확인해야 한다."""
        with self._lock:
            port = int(port)
            if port != self.port:
                self.port = port
                self.verified_port = None

    @property
    def base_url(self) -> str:
        """확인을 마친 서버 주소. 확인 전엔 RuntimeError (토큰을 실어 보낼 곳이 없다)."""
        port = self.verified_port
        if not port:
            raise RuntimeError('서버를 아직 확인하지 못했습니다')
        return f'http://127.0.0.1:{port}'

    @property
    def auth_url(self) -> str:
        """WebView 첫 주소. 토큰을 쿠키로 바꿔 주고 / 로 보낸다 (mobile_runtime._require_token)."""
        return f'{self.base_url}/__auth?t={self.token}'

    def start(self, bridge, job_json: str | None = None) -> None:
        self.runtime.set_bridge(bridge)
        self._capture_make_server()
        if job_json:
            # 서버 준비를 스스로 기다렸다가 따로 돈다 (start 는 돌아오지 않으므로 먼저 부른다)
            self.runtime.resume_job(job_json)
        threading.Thread(target=self._serve, daemon=True, name='python-server').start()

    def _serve(self) -> None:
        try:
            self.runtime.start(self.data_dir, self.start_port, self.token, self.version, debug=False)
        except Exception:  # noqa: BLE001
            logger.exception('python server crashed')

    def _capture_make_server(self) -> None:
        import werkzeug.serving as serving

        original = getattr(serving.make_server, '_ios_original', serving.make_server)
        host = self

        def make_server(*args, **kwargs):
            server = original(*args, **kwargs)
            with host._lock:
                host._server = server
                host._make_server_args = (args, kwargs)
            host.set_port(server.server_port)  # 포트 0(무작위)으로 띄워도 실제 포트를 안다
            return server

        make_server._ios_original = original
        serving.make_server = make_server  # mobile_runtime._bind 가 함수 안에서 import 한다

    def _known_port(self):
        if self.port:
            return self.port
        server_port = getattr(self.runtime, 'server_port', None)
        if server_port is not None:
            try:
                port = server_port(0)
            except Exception:  # noqa: BLE001
                port = None
            if port:
                self.set_port(port)
        return self.port

    def verify(self, timeout: float = 2.0) -> bool:
        """그 포트의 서버가 토큰을 아는지(= 우리 서버인지) /__hello 로 확인한다. 쿠키는 안 싣는다."""
        port = self._known_port()
        if not port:
            return False
        nonce = secrets.token_hex(16)  # 매번 새로 (응답을 재사용할 수 없게)
        try:
            with _opener.open(f'http://127.0.0.1:{port}/__hello?nonce={nonce}', timeout=timeout) as resp:
                if resp.status != 200:
                    return False
                given = json.loads(resp.read(4096).decode('utf-8')).get('mac', '')
        except (urllib.error.URLError, OSError, ValueError, AttributeError):
            return False
        if not isinstance(given, str) or not hmac.compare_digest(given, hello_mac(self.token, nonce)):
            logger.warning('127.0.0.1:%s 의 서버가 확인을 통과하지 못했습니다', port)
            return False
        with self._lock:
            if self.port != port:
                return False  # 확인하는 사이에 포트가 바뀌었다
            changed = self.verified_port != port
            self.verified_port = port
        if changed:
            self._write_port_file(port)
        return True

    def _write_port_file(self, port: int) -> None:
        """확인을 마친 포트를 앱 데이터 폴더에 적는다 (CI 스모크 테스트·진단용, 비밀 아님)."""
        if self.port_file is None:
            return
        try:
            _atomic_write(self.port_file, str(port).encode())
        except OSError:
            logger.warning('포트 파일을 쓰지 못했습니다', exc_info=True)

    def is_up(self, timeout: float = 2.0) -> bool:
        """먼저 /__hello 로 확인하고, 확인된 서버에만 토큰을 실어 /__health 를 부른다."""
        if not self.verify(timeout):
            return False
        req = urllib.request.Request(f'{self.base_url}/__health',
                                     headers={'Cookie': f'{TOKEN_COOKIE}={self.token}'})
        try:
            with _opener.open(req, timeout=timeout) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, ValueError, RuntimeError):
            return False

    def wait_until_up(self, timeout: float = 90.0, interval: float = 0.3) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_up():
                return True
            time.sleep(interval)
        return False

    def reopen_listener(self) -> bool:
        """듣는 소켓만 새로 연다. 새로 열었으면 True (확인 후 WebView 를 다시 불러야 한다)."""
        with self._lock:
            old, captured = self._server, self._make_server_args
        if old is None or captured is None:
            return False  # 아직 첫 서버도 안 떴다
        import werkzeug.serving as serving

        original = getattr(serving.make_server, '_ios_original', serving.make_server)
        stopper = threading.Thread(target=old.shutdown, daemon=True)
        stopper.start()
        stopper.join(5)  # serve_forever 가 0.5초마다 종료 플래그를 본다
        try:
            old.server_close()
        except OSError:
            pass
        args, kwargs = captured
        server = original(*args, **kwargs)  # 포트 0 이면 새 무작위 포트
        with self._lock:
            self._server = server
        self.set_port(server.server_port)  # 확인이 풀린다 → wait_until_up 이 다시 확인
        threading.Thread(target=server.serve_forever, daemon=True, name='python-server-2').start()
        logger.warning('reopened listening socket on 127.0.0.1:%s', server.server_port)
        return True
