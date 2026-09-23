# -*- coding: utf-8 -*-
"""Session management utilities (코레일 단일 서비스).

Flask 기본 세션은 쿠키에 서명만 하고 암호화는 하지 않는다. 쿠키를 base64 로 풀면
내용이 그대로 보이므로, 코레일 비밀번호와 카드 정보는 쿠키에 넣지 않고 서버 메모리
(`_vault`)에 둔다. 쿠키에는 그 금고를 찾는 무작위 세션 ID 만 남는다.
프로세스 메모리라 서버를 재시작하면 비워지고, 그때는 다시 로그인하면 된다.
"""
import secrets
import threading
from flask import session
from typing import Optional, Dict, Any, List

#: 유일한 서비스 제공자. SRT 열차도 코레일 API로 함께 조회된다.
PROVIDER = 'korail'

#: 세션 ID -> {'credentials': {...}, 'cards': {...}}. 쿠키에 두면 안 되는 값만 여기 둔다.
_vault: Dict[str, Dict[str, Dict[str, Any]]] = {}
_vault_lock = threading.Lock()


def _vault_entry(create: bool = False) -> Optional[Dict[str, Dict[str, Any]]]:
    """현재 세션의 금고 칸. create=False 면 없을 때 None."""
    sid = session.get('sid')
    if not sid:
        if not create:
            return None
        sid = session['sid'] = secrets.token_urlsafe(32)
        session.modified = True
    with _vault_lock:
        entry = _vault.get(sid)
        if entry is None and create:
            entry = _vault[sid] = {'credentials': {}, 'cards': {}}
        return entry


def _vault_drop(kind: str, provider: str) -> None:
    entry = _vault_entry()
    if entry:
        entry[kind].pop(provider, None)


def _init_session_structure() -> None:
    """Initialize session structure if not exists."""
    if 'auth' not in session:
        session['auth'] = {}
    if 'search_state' not in session:
        session['search_state'] = {}


def get_current_provider() -> str:
    """Get the currently active provider."""
    return session.get('current_provider', PROVIDER)


def set_current_provider(provider: str) -> None:
    """Set the currently active provider."""
    if provider != PROVIDER:
        raise ValueError(f"Invalid provider: {provider}")
    session['current_provider'] = provider
    session.modified = True


def get_auth_state(provider: str) -> Dict[str, Any]:
    """Get authentication state for a provider."""
    _init_session_structure()
    return session['auth'].get(provider, {'logged_in': False})


def set_auth_state(provider: str, user_id: str) -> None:
    """Set authentication state for a provider."""
    _init_session_structure()
    session['auth'][provider] = {
        'logged_in': True,
        'user_id': user_id
    }
    session.modified = True


def clear_auth_state(provider: str) -> None:
    """Clear authentication state for a provider."""
    _init_session_structure()
    if provider in session['auth']:
        session['auth'][provider] = {'logged_in': False}
    if provider in session['search_state']:
        del session['search_state'][provider]
    _vault_drop('credentials', provider)
    _vault_drop('cards', provider)
    session.modified = True


def is_logged_in(provider: str = None) -> bool:
    """Check if logged in to a specific provider or current provider."""
    if provider is None:
        provider = get_current_provider()
    # 쿠키의 로그인 표시만으론 부족하다. 서버 금고에 자격증명이 있어야 로그인 상태다
    # (재시작으로 금고가 비었거나, 쿠키만 흉내 낸 요청을 걸러낸다).
    return bool(get_auth_state(provider).get('logged_in') and get_credentials(provider))


def current_user_id() -> Optional[str]:
    """지금 요청을 보낸 사람의 코레일 ID. 로그인 안 했거나 요청 밖이면 None."""
    try:
        if is_logged_in(PROVIDER):
            return get_auth_state(PROVIDER).get('user_id')
    except RuntimeError:
        pass  # 요청 컨텍스트 밖 (헤드리스, 텔레그램 스레드)
    return None


def mask_user(user_id: Optional[str]) -> str:
    """다른 사람에게 보여줄 때 쓰는 가린 ID (010-1234-5678 → 010***)."""
    return f"{user_id[:3]}***" if user_id else '다른 사용자'


def get_logged_in_providers() -> List[str]:
    """Get list of all logged-in providers."""
    return [p for p in [PROVIDER] if is_logged_in(p)]


def get_any_logged_in_provider() -> Optional[str]:
    """Return any provider that is logged in, or None."""
    for p in [PROVIDER]:
        if is_logged_in(p):
            return p
    return None


def get_search_state(provider: str = None) -> Dict[str, Any]:
    """Get search state for a provider."""
    if provider is None:
        provider = get_current_provider()
    _init_session_structure()
    if provider not in session['search_state']:
        session['search_state'][provider] = {
            'trains': [],
            'selected_indices': [],
            'seat_option': 'GENERAL_FIRST',
            'form_data': {}
        }
        session.modified = True
    return session['search_state'][provider]


def set_search_trains(provider: str, trains: List[Dict]) -> None:
    """Store search results for a provider."""
    state = get_search_state(provider)
    state['trains'] = trains
    session.modified = True


def set_selected_indices(
    provider: str, indices: List[int], seat_option: str,
    passenger_count: int = 1, sequential: bool = False
) -> None:
    """Store selected train indices and reservation options for a provider."""
    state = get_search_state(provider)
    state['selected_indices'] = indices
    state['seat_option'] = seat_option
    state['passenger_count'] = passenger_count
    state['sequential'] = sequential
    session.modified = True


def get_credentials(provider: str) -> Optional[Dict[str, str]]:
    """Get stored credentials for a provider (for session restoration)."""
    entry = _vault_entry()
    return entry['credentials'].get(provider) if entry else None


def set_credentials(provider: str, user_id: str, password: str) -> None:
    """Store credentials for a provider (for session restoration)."""
    _vault_entry(create=True)['credentials'][provider] = {
        'user_id': user_id,
        'password': password
    }


def get_card_settings(provider: str) -> Optional[Dict[str, Any]]:
    """Get stored card auto-payment settings for a provider."""
    entry = _vault_entry()
    return entry['cards'].get(provider) if entry else None


def set_card_settings(
    provider: str,
    card_number: str,
    card_password: str,
    validation_number: str,
    card_expire: str,
    installment: int = 0,
    card_type: str = 'J',
    auto_pay: bool = True,
) -> None:
    """Store card auto-payment settings for a provider."""
    _vault_entry(create=True)['cards'][provider] = {
        'card_number': card_number,
        'card_password': card_password,
        'validation_number': validation_number,
        'card_expire': card_expire,
        'installment': installment,
        'card_type': card_type,
        'auto_pay': auto_pay,
    }


def clear_card_settings(provider: str) -> None:
    """Remove stored card settings for a provider."""
    _vault_drop('cards', provider)


def clear_all_session() -> None:
    """Clear all session data."""
    sid = session.get('sid')
    if sid:
        with _vault_lock:
            _vault.pop(sid, None)
    session.clear()
