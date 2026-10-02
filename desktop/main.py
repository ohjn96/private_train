#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KTX/SRT Train Reservation System - 데스크톱(PC) 앱 진입점

기본은 웹 서버, --headless 를 붙이면 화면 없이 예약 매크로만 돌린다.
헤드리스 쪽 인자는 그대로 desktop.headless 로 넘어간다 (--headless --help 로 확인).

    python -m desktop                                  웹 UI  (= python main.py)
    python -m desktop --headless --telegram-token ...  텔레그램으로 조종
    python -m desktop --headless --dep 서울 --arr 부산 --date 20261003
"""
import os
import sys
import signal
import socket
import atexit
import shutil
import threading
import urllib.error
import urllib.request
import webbrowser
from pathlib import Path
from typing import NamedTuple

# 저장소 루트(core/, webui/, korail2/ 가 있는 곳)를 import 경로에
PROJECT_ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(PROJECT_ROOT))


def _force_utf8_output() -> None:
    """표준출력을 UTF-8 로 고정한다.

    Windows 는 출력이 콘솔이 아니라 파이프/파일이면 로캘 인코딩(cp949, cp1252)을
    쓴다. 그 상태로 한글이나 이모지를 찍으면 UnicodeEncodeError 로 프로세스가
    죽는다. exe 로 묶으면 PYTHONUTF8 환경변수도 먹지 않아 여기서 직접 세운다.
    (로그를 파일로 리디렉션하는 헤드리스 실행에서 특히 중요하다)
    """
    for stream in (sys.stdout, sys.stderr):
        try:
            stream.reconfigure(encoding='utf-8', errors='replace')
        except (AttributeError, ValueError):
            pass  # 이미 감싸였거나 reconfigure 를 지원하지 않는 스트림


_force_utf8_output()

from webui import create_app
from webui.version import __version__
from werkzeug.serving import (
    LISTEN_QUEUE, ThreadedWSGIServer, get_sockaddr, select_address_family,
)


def cleanup_cache():
    """Clean up Python cache files on exit."""
    # PyInstaller 로 패키징된 exe 에서는 임시 압축 해제 폴더라 정리할 캐시가 없음
    if getattr(sys, 'frozen', False):
        return

    print("\n🧹 Cleaning up Python cache...")
    project_root = PROJECT_ROOT
    
    cache_patterns = ['__pycache__', '*.pyc', '*.pyo']
    cleaned = 0
    
    for pattern in cache_patterns:
        if pattern.startswith('*'):
            # File pattern
            for cache_file in project_root.rglob(pattern):
                try:
                    cache_file.unlink()
                    cleaned += 1
                except:
                    pass
        else:
            # Directory pattern
            for cache_dir in project_root.rglob(pattern):
                try:
                    shutil.rmtree(cache_dir)
                    cleaned += 1
                except:
                    pass
    
    if cleaned > 0:
        print(f"✅ Cleaned {cleaned} cache items")


def signal_handler(signum, frame):
    """Handle interrupt signal."""
    print("\n\n⏹️  Server stopped by user")
    cleanup_cache()
    sys.exit(0)


# Register cleanup handlers
atexit.register(cleanup_cache)
signal.signal(signal.SIGINT, signal_handler)
# SIGTERM is not available on Windows
if hasattr(signal, 'SIGTERM'):
    signal.signal(signal.SIGTERM, signal_handler)

def browser_url(host: str, port: int) -> str:
    """브라우저로 열 주소. 모든 주소에 바인딩했으면 이 컴퓨터(localhost)로 연다."""
    if host in ('', '0.0.0.0', '::', '127.0.0.1', 'localhost'):
        host = 'localhost'
    elif ':' in host:
        host = f'[{host}]'
    return f'http://{host}:{port}'


def open_browser(url: str, delay: float = 0.0) -> None:
    """기본 브라우저로 앱을 연다. 서버가 이미 연결을 받을 수 있을 때만 부른다.

    - exe 로 실행하면 기본 동작, 개발 중에는 OPEN_BROWSER=1 일 때만
    - NO_BROWSER=1 이면 항상 끔 (서버/원격 환경)
    """
    if os.environ.get('NO_BROWSER', '').lower() in ('1', 'true', 'yes'):
        return
    frozen = getattr(sys, 'frozen', False)
    if not frozen and os.environ.get('OPEN_BROWSER', '').lower() not in ('1', 'true', 'yes'):
        return

    def _open():
        try:
            webbrowser.open(url)
        except Exception:
            # 브라우저가 없는 환경(헤드리스 등)에서는 조용히 넘어간다
            pass

    threading.Timer(delay, _open).start()


def pause_before_exit() -> None:
    """exe 를 더블클릭해 연 콘솔 창이 오류 메시지와 함께 바로 닫히지 않게 붙잡는다."""
    if not getattr(sys, 'frozen', False):
        return
    try:
        if sys.stdin and sys.stdin.isatty():
            input("엔터를 누르면 닫혀요")
    except (EOFError, OSError, KeyboardInterrupt):
        pass


app = create_app()


def wants_headless(argv: list[str]) -> bool:
    """--headless 인자 또는 HEADLESS 환경변수(도커/서비스용)."""
    if '--headless' in argv:
        return True
    return os.environ.get('HEADLESS', '').lower() in ('1', 'true', 'yes')


def run_headless(argv: list[str]) -> int:
    """헤드리스 러너에 넘긴다. --headless 자체만 빼고 그대로 전달한다."""
    from desktop.headless import main as headless_main

    return headless_main([arg for arg in argv if arg != '--headless'])


#: 이 앱이 떠 있는지 알아보는 응답 헤더. 값은 "이름/버전"
WHOAMI_HEADER = 'X-Train-Reservation-App'
APP_NAME = 'TrainReservationApp'
#: 기본 포트를 누가 쓰고 있으면 그 다음 이만큼의 포트를 차례로 시도한다 (5050 → 5051..5060)
FALLBACK_PORTS = 10


@app.after_request
def _whoami_header(resp):
    resp.headers[WHOAMI_HEADER] = f'{APP_NAME}/{__version__}'
    return resp


@app.route('/__whoami')
def whoami():
    return {'app': APP_NAME, 'version': __version__}


def bind_socket(host: str, port: int) -> socket.socket:
    """host:port 에 바인딩하고 listen 까지 한 소켓. 이미 누가 쓰고 있으면 OSError.

    Windows 의 SO_REUSEADDR 는 다른 프로그램이 쓰는 포트에도 겹쳐 바인딩되게 해서
    (요청이 어느 쪽으로 갈지 모른다) 쓰지 않고, SO_EXCLUSIVEADDRUSE 로 독점한다.
    POSIX 의 SO_REUSEADDR 는 TIME_WAIT 만 허용할 뿐 겹쳐 바인딩되지 않아 그대로 쓴다.
    """
    family = select_address_family(host, port)
    sock = socket.socket(family, socket.SOCK_STREAM)
    try:
        if os.name == 'nt':
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE, 1)
        else:
            sock.setsockopt(socket.SOL_SOCKET, socket.SO_REUSEADDR, 1)
        sock.bind(get_sockaddr(host, port, family))
        sock.listen(LISTEN_QUEUE)
    except BaseException:
        sock.close()
        raise
    return sock


def identify(host: str, port: int, timeout: float = 2.0) -> str | None:
    """host:port 에 떠 있는 게 이 앱이면 "이름/버전", 아니면 None."""
    if host in ('', '0.0.0.0', '::'):
        host = '127.0.0.1'
    if ':' in host:
        host = f'[{host}]'
    try:
        with urllib.request.urlopen(f'http://{host}:{port}/__whoami', timeout=timeout) as resp:
            value = resp.headers.get(WHOAMI_HEADER)
    except urllib.error.HTTPError as e:  # 접근 비밀번호 등으로 막혀도 헤더는 붙는다
        value = e.headers.get(WHOAMI_HEADER) if e.headers else None
    except (OSError, ValueError):
        return None
    return value or None


def is_same_app(whoami: str | None) -> bool:
    """같은 앱, 같은 큰 버전(v3)이면 True."""
    if not whoami or '/' not in whoami:
        return False
    name, version = whoami.split('/', 1)
    return name == APP_NAME and version.split('.')[0] == __version__.split('.')[0]


class PortChoice(NamedTuple):
    port: int
    sock: socket.socket | None  # None 이면 이미 떠 있는 같은 앱을 쓴다
    existing: str | None = None  # 이미 떠 있는 앱의 "이름/버전"


class NoFreePort(Exception):
    pass


def choose_port(host: str, preferred: int, fallbacks: int = FALLBACK_PORTS,
                probe=identify) -> PortChoice:
    """서버를 띄울 포트를 고르고 바인딩까지 해 둔다.

    1. preferred 가 비었으면 그대로.
    2. 이미 이 앱(같은 큰 버전)이 떠 있으면 새로 띄우지 않고 그걸 쓴다.
    3. 다른 프로그램이면 preferred+1 .. preferred+fallbacks 중 빈 포트.
    """
    try:
        return PortChoice(preferred, bind_socket(host, preferred))
    except OSError:
        pass
    existing = probe(host, preferred)
    if is_same_app(existing):
        return PortChoice(preferred, None, existing)
    for port in range(preferred + 1, preferred + fallbacks + 1):
        try:
            return PortChoice(port, bind_socket(host, port))
        except OSError:
            continue
    raise NoFreePort(f'{preferred}..{preferred + fallbacks}')


class ExclusiveWSGIServer(ThreadedWSGIServer):
    """Windows 에서 남이 쓰는 포트에 겹쳐 바인딩하지 않는 서버 (werkzeug 기본은 SO_REUSEADDR)."""
    allow_reuse_address = os.name != 'nt'


def make_app_server(host: str, sock: socket.socket) -> ExclusiveWSGIServer:
    """미리 바인딩한 소켓을 그대로 넘겨받아 요청을 받는 서버."""
    port = sock.getsockname()[1]
    server = ExclusiveWSGIServer(host, port, app, fd=sock.fileno())
    sock.close()  # 서버가 fd 를 복제해 갖고 있다
    return server


def serve(host: str, port: int, debug: bool, sock: socket.socket | None) -> None:
    if debug:
        # 개발용: reloader 가 자식 프로세스에서 다시 바인딩하므로 잡아 둔 소켓은 놓는다
        if sock is not None:
            sock.close()
        os.environ['PORT'] = str(port)  # 자식 프로세스도 같은 포트로
        if os.environ.get('WERKZEUG_RUN_MAIN') == 'true':  # 실제로 서버를 돌리는 자식에서만
            open_browser(browser_url(host, port), delay=1.0)
        app.run(host=host, port=port, debug=True, threaded=True)
        return
    server = make_app_server(host, sock)
    # 서버 소켓이 listen 중이라 이제 브라우저가 바로 붙을 수 있다
    open_browser(browser_url(host, port))
    server.serve_forever()


def run_web() -> None:
    """웹 서버로 띄운다 (기본 동작)."""
    # 기본 포트를 5050으로 변경 (macOS AirPlay가 5000 사용)
    preferred = int(os.environ.get('PORT', 5050))
    # 기본은 이 컴퓨터에서만 (127.0.0.1). 같은 와이파이의 폰에서 열려면 HOST=0.0.0.0.
    # (예전 기본 0.0.0.0 은 카페·회사 와이파이의 누구나 로그인 화면·텔레그램 설정에 닿았다)
    host = os.environ.get('HOST', '127.0.0.1')
    # 디버그 모드는 웹 디버거(원격 코드 실행)가 열리므로 FLASK_DEBUG=true 로 켤 때만
    debug = os.environ.get('FLASK_DEBUG', 'false').lower() == 'true'

    print(f"🚄 Train Reservation App v{__version__}")

    if debug and os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        # reloader 의 자식: 부모가 고른 포트(PORT)를 그대로 쓴다
        serve(host, preferred, debug, None)
        return

    try:
        choice = choose_port(host, preferred)
    except NoFreePort:
        print(f"❌ 포트 {preferred}~{preferred + FALLBACK_PORTS} 를 모두 다른 프로그램이 쓰고 있어 시작하지 못했어요.")
        print("   다른 프로그램을 끄거나 PORT 환경변수로 다른 포트를 지정해 주세요.")
        pause_before_exit()
        sys.exit(1)

    if choice.sock is None:
        url = browser_url(host, choice.port)
        print(f"이미 실행 중인 앱({choice.existing})이 있어서 그 화면을 열어요: {url}")
        open_browser(url)
        return

    if choice.port != preferred:
        print(f"⚠️  포트 {preferred} 를 다른 프로그램이 쓰고 있어서 {choice.port} 로 열어요.")
    print(f"   {browser_url(host, choice.port)}")
    print("브라우저가 자동으로 열립니다. 종료하려면 이 창에서 Ctrl+C")
    print("")

    try:
        serve(host, choice.port, debug, choice.sock)
    except KeyboardInterrupt:
        print("\n⏹️  Server stopped by user")
    except Exception as e:
        print(f"❌ 서버를 시작하지 못했어요: {e}")
        pause_before_exit()
        sys.exit(1)
    finally:
        cleanup_cache()


def main() -> None:
    argv = sys.argv[1:]
    if wants_headless(argv):
        sys.exit(run_headless(argv))
    run_web()


if __name__ == '__main__':
    main()
