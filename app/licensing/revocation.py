# -*- coding: utf-8 -*-
"""철회 목록 — 이미 발급한 라이선스를 무효화하는 장치.

발급자가 서명한 정적 JSON 을 저장소에 커밋해두고, 앱이 하루에 한 번쯤 받아온다.
목록 자체도 서명돼 있어서, 중간에서 가짜 목록을 끼워넣어 멀쩡한 사용자를
막아버릴 수는 없다.

네트워크가 안 되면 **막지 않는다**(fail-open). 오프라인 사용을 보장하기 위한
선택이고, 그 대가로 인터넷을 끊으면 철회를 늦출 수 있다는 한계가 있다.
"""
from __future__ import annotations

import json
import threading
import time
import urllib.error
import urllib.request

from app.licensing import config, store
from app.licensing.token import LicenseError, parse

# 철회 목록도 라이선스 토큰과 같은 형식으로 서명한다.
# payload:  {"v":1,"lid":"revocation-list","mid":"*","iat":...,"exp":...,
#            "note":"<철회된 id 를 콤마로 이은 목록>"}
REVOCATION_LIST_ID = 'revocation-list'


def _parse_list(token: str, public_key) -> set[str]:
    """서명된 철회 목록 토큰에서 철회된 식별자 집합을 꺼낸다."""
    license_obj = parse(token, public_key)
    if license_obj.license_id != REVOCATION_LIST_ID:
        raise LicenseError('철회 목록이 아닙니다.', 'not_a_list')
    return {item.strip() for item in license_obj.note.split(',') if item.strip()}


def _fetch(url: str) -> str | None:
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'TrainReservation'})
        with urllib.request.urlopen(request, timeout=config.REVOCATION_TIMEOUT) as response:
            body = response.read(64 * 1024).decode('utf-8')
    except (urllib.error.URLError, OSError, UnicodeDecodeError, ValueError):
        return None

    try:
        data = json.loads(body)
    except json.JSONDecodeError:
        return None
    token = data.get('token') if isinstance(data, dict) else None
    return token if isinstance(token, str) else None


_refreshing = threading.Lock()


def _cached_ids(cache: dict) -> set[str] | None:
    ids = cache.get('ids')
    return {str(item) for item in ids} if isinstance(ids, list) else None


def _refresh(public_key) -> set[str] | None:
    """목록을 새로 받아 캐시에 쓴다. 실패하면 None."""
    token = _fetch(config.REVOCATION_URL)
    if token is None:
        return None
    try:
        ids = _parse_list(token, public_key)
    except LicenseError:
        return None   # 서명이 안 맞는 목록은 통째로 무시한다

    state = store.read_state()
    state['revocation'] = {'fetched_at': int(time.time()), 'ids': sorted(ids)}
    store.write_state(state)
    return ids


def _refresh_in_background(public_key) -> None:
    """캐시가 오래됐을 때, 요청을 붙잡지 않고 뒤에서 갱신한다."""
    if not _refreshing.acquire(blocking=False):
        return   # 이미 누가 받아오는 중

    def run():
        try:
            _refresh(public_key)
        finally:
            _refreshing.release()

    threading.Thread(target=run, daemon=True).start()


def revoked_ids(public_key, *, force: bool = False) -> set[str]:
    """철회된 식별자 집합.

    캐시가 있으면 항상 그것을 즉시 돌려주고, 오래됐으면 뒤에서 갱신한다.
    캐시가 아예 없을 때만(=설치 직후) 네트워크를 기다린다.
    """
    cache = store.read_state().get('revocation')
    cache = cache if isinstance(cache, dict) else {}
    cached = _cached_ids(cache)

    fetched_at = cache.get('fetched_at', 0)
    fresh = (isinstance(fetched_at, int)
             and int(time.time()) - fetched_at < config.REVOCATION_TTL)

    if cached is not None and fresh and not force:
        return cached

    if cached is not None and not force:
        _refresh_in_background(public_key)
        return cached

    return _refresh(public_key) or cached or set()
