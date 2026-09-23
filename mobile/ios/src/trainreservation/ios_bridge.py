# -*- coding: utf-8 -*-
"""iPhone 연결부 중 iOS API 가 필요 없는 부분 (리눅스에서도 테스트한다: tests/test_ios_bridge.py).

- JobStore        되살릴 매크로 작업을 앱 데이터 폴더의 JSON 파일에 원자적으로 저장
- load_or_create_token  설치마다 만든 무작위 토큰 (WebView 만 서버에 붙게)
- IOSBridge       mobile_runtime.set_bridge() 에 꽂는 객체 (안드로이드 Bridge.kt 와 같은 네 메서드)
- ServerHost      공통 런타임을 백그라운드 스레드로 띄우고, 응답을 확인하고, 필요하면 소켓을 다시 연다

iOS 호출(절전 방지, 알림, 파일 보호)은 native 객체로 주입한다 (실제 구현은 ios_native.py).
"""
import json
import logging
import os
import secrets
import threading
import time
import urllib.error
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


class IOSBridge:
    """mobile_runtime 이 부르는 플랫폼 연결부. 어느 스레드에서든 불린다.

    native: set_idle_timer_disabled(bool), notify(kind, title, body) 를 가진 객체.
            둘 다 UIKit 을 만지므로 call_on_main 으로 메인 스레드에서 부른다.
    call_on_main: 메인 스레드에서 fn(*args) 를 돌리게 예약하는 함수 (app.loop.call_soon_threadsafe).
    on_state: 매크로 상태가 바뀔 때 메인 스레드에서 부를 콜백 (제목 표시 등). 없어도 된다.
    on_server_ready: 서버가 실제로 연 포트를 받을 콜백 (ServerHost.set_port). 없어도 된다.
    """

    def __init__(self, store: JobStore, native, call_on_main, on_state=None, on_server_ready=None):
        self.store = store
        self.native = native
        self._call_on_main = call_on_main
        self._on_state = on_state
        self._on_server_ready = on_server_ready
        self.macro_running = False
        self.macro_summary = ''

    # ── mobile_runtime 이 부르는 네 메서드 (이름은 안드로이드 Bridge 와 같다)

    def onMacroState(self, running, summary):  # noqa: N802 - 공통 bridge 인터페이스
        """매크로가 도는 동안 화면이 저절로 꺼지지 않게 한다 (꺼지면 iOS 가 앱을 멈춘다)."""
        self.macro_running = bool(running)
        self.macro_summary = str(summary or '')
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


class ServerHost:
    """공통 런타임(mobile_runtime)을 띄우고 지켜본다.

    iOS 는 앱이 뒤로 가서 멈춰(suspend) 있는 동안 듣고 있던 소켓을 회수할 수 있다
    (Apple TN2277). 그러면 다시 앞으로 왔을 때 WebView 가 서버에 붙지 못한다.
    그래서 werkzeug.serving.make_server 를 감싸 서버 객체를 잡아 두고, 응답이 없으면
    같은 Flask 앱으로 소켓만 새로 연다 (매크로·로그인 상태는 그대로 메모리에 남아 있다).
    """

    def __init__(self, runtime, data_dir, port: int, token: str, version: str):
        self.runtime = runtime            # mobile_runtime 모듈 (테스트에선 가짜)
        self.data_dir = str(data_dir)
        self.port = int(port)
        self.token = token
        self.version = version
        self._server = None
        self._make_server_args = None
        self._lock = threading.Lock()

    def set_port(self, port: int) -> None:
        self.port = int(port)

    @property
    def base_url(self) -> str:
        return f'http://127.0.0.1:{self.port}'

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
            self.runtime.start(self.data_dir, self.port, self.token, self.version, debug=False)
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
                host.port = server.server_port  # 포트 0(무작위)으로 띄워도 실제 포트를 안다
            return server

        make_server._ios_original = original
        serving.make_server = make_server  # mobile_runtime.start 가 함수 안에서 import 한다

    def is_up(self, timeout: float = 2.0) -> bool:
        req = urllib.request.Request(f'{self.base_url}/__health',
                                     headers={'Cookie': f'{TOKEN_COOKIE}={self.token}'})
        try:
            with urllib.request.urlopen(req, timeout=timeout) as resp:  # noqa: S310 - 127.0.0.1
                return resp.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def wait_until_up(self, timeout: float = 90.0, interval: float = 0.3) -> bool:
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.is_up():
                return True
            time.sleep(interval)
        return False

    def reopen_listener(self) -> bool:
        """듣는 소켓만 새로 연다. 새로 열었으면 True (WebView 를 다시 불러야 한다)."""
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
        server = original(*args, **kwargs)  # allow_reuse_address 라 같은 포트를 바로 다시 쓴다
        with self._lock:
            self._server = server
            self.port = server.server_port
        threading.Thread(target=server.serve_forever, daemon=True, name='python-server-2').start()
        logger.warning('reopened listening socket on %s', self.base_url)
        return True
