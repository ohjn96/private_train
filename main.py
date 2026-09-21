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

app = create_app()

if __name__ == '__main__':
    # 기본 포트를 5050으로 변경 (macOS AirPlay가 5000 사용)
    port = int(os.environ.get('PORT', 5050))
    # exe 로 실행할 때는 reloader 가 프로세스를 두 번 띄우므로 debug 기본 off
    debug_default = 'false' if getattr(sys, 'frozen', False) else 'true'
    debug = os.environ.get('FLASK_DEBUG', debug_default).lower() == 'true'

    print(f"🚄 Train Reservation App v{__version__}")
    print(f"   http://localhost:{port}")
    print("Press Ctrl+C to quit")
    print("")

    try:
        app.run(host='0.0.0.0', port=port, debug=debug, threaded=True)
    except KeyboardInterrupt:
        print("\n⏹️  Server stopped by user")
    finally:
        cleanup_cache()
