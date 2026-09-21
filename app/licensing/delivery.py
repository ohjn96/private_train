# -*- coding: utf-8 -*-
"""발급된 라이선스 자동 수령.

발급자가 GitHub Issue 에서 승인하면 Actions 가 licenses/<머신ID>.key 를 커밋한다.
앱은 자기 머신 ID 에 해당하는 파일만 받아보면 되므로, 사용자가 키를 복붙할 필요가 없다.

받아온 값은 그냥 문자열일 뿐이고, 실제 신뢰는 서명 검증(app.licensing.activate)이 한다.
따라서 이 경로가 가짜 응답을 주더라도 서명이 맞지 않으면 통과하지 못한다.
"""
from __future__ import annotations

import urllib.error
import urllib.request

from app.licensing import config

MAX_BYTES = 8 * 1024


def fetch_issued_token(machine_id: str) -> str | None:
    """이 PC 앞으로 발급된 키를 받아온다. 없거나 실패하면 None."""
    url = config.DELIVERY_URL.format(machine_id=machine_id)
    try:
        request = urllib.request.Request(url, headers={'User-Agent': 'TrainReservation'})
        with urllib.request.urlopen(request, timeout=config.DELIVERY_TIMEOUT) as response:
            body = response.read(MAX_BYTES).decode('utf-8')
    except (urllib.error.URLError, OSError, UnicodeDecodeError, ValueError):
        return None

    token = body.strip()
    return token if token.startswith('TRAIN1.') else None
