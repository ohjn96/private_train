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
import atexit
import shutil
import threading
import webbrowser
from pathlib import Path

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

def open_browser(port: int) -> None:
    """서버가 뜬 직후 기본 브라우저로 앱을 연다.

    - exe 로 실행하면 기본 동작, 개발 중에는 OPEN_BROWSER=1 일 때만
    - NO_BROWSER=1 이면 항상 끔 (서버/원격 환경)
    - reloader 가 켜져 있으면 자식 프로세스에서만 열어 중복 방지
    """
    if os.environ.get('NO_BROWSER', '').lower() in ('1', 'true', 'yes'):
        return
    frozen = getattr(sys, 'frozen', False)
    if not frozen and os.environ.get('OPEN_BROWSER', '').lower() not in ('1', 'true', 'yes'):
        return

    def _open():
        try:
            webbrowser.open(f'http://localhost:{port}')
        except Exception:
            # 브라우저가 없는 환경(헤드리스 등)에서는 조용히 넘어간다
            pass

    threading.Timer(1.0, _open).start()


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


def run_web() -> None:
    """웹 서버로 띄운다 (기본 동작)."""
    # 기본 포트를 5050으로 변경 (macOS AirPlay가 5000 사용)
    port = int(os.environ.get('PORT', 5050))
    # 서버에선 127.0.0.1 이나 Tailscale 주소로 좁힐 수 있게. 기본은 예전처럼 전체.
    host = os.environ.get('HOST', '0.0.0.0')
    # exe 로 실행할 때는 reloader 가 프로세스를 두 번 띄우므로 debug 기본 off
    debug_default = 'false' if getattr(sys, 'frozen', False) else 'true'
    debug = os.environ.get('FLASK_DEBUG', debug_default).lower() == 'true'

    print(f"🚄 Train Reservation App v{__version__}")
    print(f"   http://localhost:{port}")
    print("브라우저가 자동으로 열립니다. 종료하려면 이 창에서 Ctrl+C")
    print("")

    # debug 모드에서는 reloader 가 프로세스를 두 번 띄우므로 실제 실행되는 쪽에서만 연다
    if not debug or os.environ.get('WERKZEUG_RUN_MAIN') == 'true':
        open_browser(port)

    try:
        app.run(host=host, port=port, debug=debug, threaded=True)
    except KeyboardInterrupt:
        print("\n⏹️  Server stopped by user")
    finally:
        cleanup_cache()


def main() -> None:
    argv = sys.argv[1:]
    if wants_headless(argv):
        sys.exit(run_headless(argv))
    run_web()


if __name__ == '__main__':
    main()
