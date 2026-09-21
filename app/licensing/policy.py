# -*- coding: utf-8 -*-
"""원격 정책 — 라이선스 검사를 켜고 끄는 스위치.

앱은 서명된 정책 파일 하나를 보고 자기가 어떻게 행동할지 정한다.

    open      누구나 쓸 수 있다 (기본값). 라이선스 검사를 하지 않는다.
    licensed  라이선스가 있어야 쓸 수 있다.
    blocked   전면 차단. 긴급 정지용.

정책 파일이 없거나 못 받아오면 **open** 이다. 지금 쓰고 있는 사람을 갑자기
막지 않기 위한 선택이다.

한 번 licensed/blocked 를 본 앱은 그 상태를 기억한다. 인터넷을 끊어서 스위치를
피할 수 없게 하기 위해서다. 되돌리려면 더 큰 seq 를 가진 새 정책을 발행해야 하고,
낮은 seq 는 거부하므로 옛날 정책을 다시 들이밀 수도 없다.

한계: 한 번도 정책을 받아본 적 없는 새 설치는 open 으로 시작한다. 처음부터
잠그고 싶으면 정책을 켠 뒤에 빌드해 배포하면 된다.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from app.licensing import config, store
from app.licensing.token import LicenseError, parse

POLICY_ID = 'policy'

OPEN = 'open'
LICENSED = 'licensed'
BLOCKED = 'blocked'
VALID_MODES = (OPEN, LICENSED, BLOCKED)

DEFAULT_MODE = OPEN

_refreshing = threading.Lock()


class Policy:
    __slots__ = ('mode', 'seq', 'message', 'source')

    def __init__(self, mode: str = DEFAULT_MODE, seq: int = 0, message: str = '',
                 source: str = 'builtin'):
        self.mode = mode if mode in VALID_MODES else DEFAULT_MODE
        self.seq = seq
        self.message = message
        # 'remote'  방금 받아왔다
        # 'cache'   전에 받아둔 것을 쓰는 중
        # 'builtin' 한 번도 못 받아서 빌드 시점 값으로 돌아갔다
        self.source = source

    @property
    def requires_license(self) -> bool:
        return self.mode == LICENSED

    @property
    def blocks_everything(self) -> bool:
        return self.mode == BLOCKED

    @property
    def unverified(self) -> bool:
        """원격 정책을 한 번도 확인하지 못한 상태인가."""
        return self.source == 'builtin'

    def as_dict(self) -> dict:
        return {'mode': self.mode, 'seq': self.seq, 'message': self.message}

    def __repr__(self) -> str:
        return f'Policy(mode={self.mode!r}, seq={self.seq}, source={self.source!r})'


def built_in() -> Policy:
    """원격 정책을 못 받았을 때 돌아갈 자리 — 빌드 시점에 구워둔 값."""
    from app.licensing.built_in_policy import (
        BUILT_IN_MESSAGE, BUILT_IN_MODE, BUILT_IN_SEQ,
    )
    return Policy(mode=BUILT_IN_MODE, seq=BUILT_IN_SEQ,
                  message=BUILT_IN_MESSAGE, source='builtin')


def _fetch(url: str) -> str | None:
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'TrainReservation'})
        with urllib.request.urlopen(request, timeout=config.POLICY_TIMEOUT) as response:
            body = response.read(64 * 1024).decode('utf-8')
    except (urllib.error.URLError, OSError, UnicodeDecodeError, ValueError):
        return None

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    token = data.get('token') if isinstance(data, dict) else None
    return token if isinstance(token, str) else None


def _parse_policy(token: str, public_key) -> Policy:
    """서명된 정책 토큰을 Policy 로. 서명이 안 맞으면 LicenseError."""
    license_obj = parse(token, public_key)
    if license_obj.license_id != POLICY_ID:
        raise LicenseError('정책 파일이 아닙니다.', 'not_a_policy')

    data = license_obj.data
    mode = str(data.get('mode', DEFAULT_MODE))
    if mode not in VALID_MODES:
        raise LicenseError(f'알 수 없는 정책 모드: {mode}', 'bad_policy')

    try:
        seq = int(data.get('seq', 0))
    except (TypeError, ValueError) as exc:
        raise LicenseError('정책 seq 가 올바르지 않습니다.', 'bad_policy') from exc

    return Policy(mode=mode, seq=seq, message=str(data.get('message', '')))


def _stored() -> Policy:
    """캐시된 정책. 캐시가 없으면 빌드 시점 값."""
    cached = store.read_state().get('policy')
    if not isinstance(cached, dict):
        return built_in()
    return Policy(
        mode=str(cached.get('mode', DEFAULT_MODE)),
        seq=int(cached.get('seq', 0)) if isinstance(cached.get('seq'), int) else 0,
        message=str(cached.get('message', '')),
        source='cache',
    )


def _save(policy: Policy) -> None:
    state = store.read_state()
    entry = policy.as_dict()
    entry['fetched_at'] = int(time.time())
    state['policy'] = entry
    store.write_state(state)


def _refresh(public_key) -> Policy | None:
    """정책을 새로 받아 저장한다. 실패하거나 무시해야 할 정책이면 None."""
    token = _fetch(config.POLICY_URL)
    if token is None:
        return None
    try:
        fresh = _parse_policy(token, public_key)
    except LicenseError:
        return None   # 서명이 안 맞는 정책은 없는 셈 친다

    # 되돌리기(replay) 방지: 지금까지 본 것(또는 빌드 시점 값)보다 낮은 seq 는 받지 않는다
    if fresh.seq < _stored().seq:
        return None

    _save(fresh)
    fresh.source = 'remote'
    return fresh


def _refresh_in_background(public_key) -> None:
    if not _refreshing.acquire(blocking=False):
        return

    def run():
        try:
            _refresh(public_key)
        finally:
            _refreshing.release()

    threading.Thread(target=run, daemon=True).start()


def current(public_key, *, force: bool = False) -> Policy:
    """지금 적용할 정책.

    캐시가 있으면 즉시 돌려주고 낡았으면 뒤에서 갱신한다. force 면 기다린다.
    """
    cached = store.read_state().get('policy')
    cached = cached if isinstance(cached, dict) else {}
    fetched_at = cached.get('fetched_at', 0)
    fresh = (isinstance(fetched_at, int)
             and int(time.time()) - fetched_at < config.POLICY_TTL)

    if force:
        return _refresh(public_key) or _stored()

    if cached and fresh:
        return _stored()

    if cached:
        _refresh_in_background(public_key)
        return _stored()

    # 한 번도 받아본 적 없으면 한 번은 기다려본다.
    # 그래도 실패하면 빌드 시점 값으로 간다 — 네트워크를 막아서 검사를 피하지 못하게.
    return _refresh(public_key) or built_in()
