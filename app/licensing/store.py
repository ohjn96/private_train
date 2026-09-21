# -*- coding: utf-8 -*-
"""라이선스 파일과 상태 저장.

- license.key : 발급받은 토큰 원문
- state.json  : 마지막으로 관측한 시각(시계 되돌리기 감지), 철회 목록 캐시
- 홈 디렉터리의 숨김 파일 : state.json 을 지워도 남는 보조 기록

상태 파일은 지워질 수 있다. 지워져도 라이선스가 생기는 건 아니고, 시계 되돌리기
감지 기준만 초기화된다.
"""
from __future__ import annotations

import json
import time
from pathlib import Path

from app.licensing import config


def license_path() -> Path:
    return config.app_data_dir() / 'license.key'


def state_path() -> Path:
    return config.app_data_dir() / 'state.json'


# --------------------------------------------------------------------------- 라이선스 본문

def read_license() -> str | None:
    try:
        text = license_path().read_text(encoding='utf-8').strip()
    except OSError:
        return None
    return text or None


def write_license(token: str) -> None:
    path = license_path()
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(token.strip() + '\n', encoding='utf-8')
    try:
        path.chmod(0o600)
    except OSError:
        pass   # Windows 등에서는 무시


def clear_license() -> None:
    try:
        license_path().unlink()
    except OSError:
        pass


# --------------------------------------------------------------------------- 상태

def _read_json(path: Path) -> dict:
    try:
        data = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def read_state() -> dict:
    return _read_json(state_path())


def write_state(state: dict) -> None:
    path = state_path()
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(state, ensure_ascii=False), encoding='utf-8')
    except OSError:
        pass   # 상태를 못 써도 앱은 돌아가야 한다


def last_seen() -> int:
    """지금까지 관측한 가장 늦은 시각. 두 곳 중 큰 값을 쓴다."""
    primary = read_state().get('last_seen', 0)
    shadow = _read_json(config.shadow_state_path()).get('last_seen', 0)
    values = [v for v in (primary, shadow) if isinstance(v, int)]
    return max(values) if values else 0


def touch_last_seen(now: int | None = None) -> None:
    """현재 시각을 두 곳에 기록 (뒤로 가지는 않게)."""
    now = int(time.time()) if now is None else now
    if now <= last_seen():
        return

    state = read_state()
    state['last_seen'] = now
    write_state(state)

    shadow = config.shadow_state_path()
    try:
        shadow.parent.mkdir(parents=True, exist_ok=True)
        shadow.write_text(json.dumps({'last_seen': now}), encoding='utf-8')
    except OSError:
        pass
