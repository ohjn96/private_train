#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
KTX/SRT Train Reservation System - Unified Entry Point
"""
import os
import sys
import signal
import atexit
import shutil
import threading
import webbrowser
from pathlib import Path

# Add project root to path
sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))

from app import create_app
from app.version import __version__


def cleanup_cache():
    """Clean up Python cache files on exit."""
    # PyInstaller 로 패키징된 exe 에서는 임시 압축 해제 폴더라 정리할 캐시가 없음
    if getattr(sys, 'frozen', False):
        return

    print("\n🧹 Cleaning up Python cache...")
    project_root = Path(__file__).parent
    
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

if __name__ == '__main__':
    # 기본 포트를 5050으로 변경 (macOS AirPlay가 5000 사용)
    port = int(os.environ.get('PORT', 5050))
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
        app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)
    except KeyboardInterrupt:
        print("\n⏹️  Server stopped by user")
    finally:
        cleanup_cache()
