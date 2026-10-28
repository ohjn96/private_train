# -*- coding: utf-8 -*-
"""정책·철회 목록·발급 키를 받아올 때 쓰는 HTTP 열기.

PyInstaller 로 묶은 exe(특히 macOS)는 OpenSSL 이 시스템 인증서를 못 찾아서
urllib 의 HTTPS 가 CERTIFICATE_VERIFY_FAILED 로 실패한다. 그러면 원격 정책을
한 번도 못 받은 것으로 보고 잠겨버리므로, requests 와 같은 certifi 묶음을 쓴다.
"""
from __future__ import annotations

import ssl
import urllib.request


def _context() -> ssl.SSLContext:
    try:
        import certifi
        return ssl.create_default_context(cafile=certifi.where())
    except (ImportError, OSError):
        return ssl.create_default_context()


_CONTEXT = _context()


def urlopen(request: urllib.request.Request, timeout: float):
    return urllib.request.urlopen(request, timeout=timeout, context=_CONTEXT)
