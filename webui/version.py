# -*- coding: utf-8 -*-
"""앱 버전 (루트 VERSION 파일이 단일 출처)."""
import os
import sys
from pathlib import Path

FALLBACK = "0.0.0"


def _version_path() -> Path:
    if getattr(sys, 'frozen', False):
        # PyInstaller 번들 안에서는 임시 해제 폴더 루트에 들어있음
        return Path(sys._MEIPASS) / 'VERSION'
    return Path(__file__).resolve().parent.parent / 'VERSION'


def get_version() -> str:
    # 안드로이드 앱처럼 VERSION 파일을 같이 싣지 않는 곳에선 바깥에서 넘겨준다
    if os.environ.get('TRAIN_APP_VERSION'):
        return os.environ['TRAIN_APP_VERSION']
    try:
        return _version_path().read_text(encoding='utf-8').strip() or FALLBACK
    except OSError:
        return FALLBACK


__version__ = get_version()
