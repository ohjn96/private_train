# -*- coding: utf-8 -*-
"""QA 용 외부 네트워크 차단. PYTHONPATH 에 이 폴더를 넣으면 파이썬이 시작할 때 자동으로 읽힌다.

127.x / localhost 말고는 연결·DNS 조회를 막고, 막은 곳의 호출 경로를 stderr 에
[QA-NONET] 로 남긴다 (단위 테스트가 실수로 코레일에 붙는지 잡아낸다).
"""
import socket
import sys
import traceback

_LOCAL = ('localhost', '::1', '0.0.0.0')


def _is_local(host) -> bool:
    return not isinstance(host, str) or host.startswith('127.') or host in _LOCAL


def _report(what):
    sys.stderr.write(f'\n[QA-NONET] blocked {what}\n' + ''.join(traceback.format_stack(limit=20)) + '\n')


_connect = socket.socket.connect


def _guarded_connect(self, addr):
    if isinstance(addr, tuple) and not _is_local(addr[0]):
        _report(f'connect {addr}')
        raise OSError('QA: 외부 네트워크 차단')
    return _connect(self, addr)


_getaddrinfo = socket.getaddrinfo


def _guarded_getaddrinfo(host, *a, **k):
    if not _is_local(host):
        _report(f'DNS {host}')
        raise socket.gaierror('QA: 외부 네트워크 차단')
    return _getaddrinfo(host, *a, **k)


socket.socket.connect = _guarded_connect
socket.getaddrinfo = _guarded_getaddrinfo
