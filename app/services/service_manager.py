# -*- coding: utf-8 -*-
"""Service instance manager with session-based storage."""
import threading
from flask import g
from typing import Optional

from app.services.korail_service import KorailService
from app.services.base_service import BaseTrainService
from app.utils.session_helper import (
    set_auth_state,
    clear_auth_state,
    get_credentials,
    set_credentials,
    is_logged_in,
    clear_all_session,
)


class ServiceManager:
    """
    Manages service instances.

    로그인된 서비스 인스턴스는 프로세스 전역으로 재사용한다. 예전에는 요청마다
    새로 만들어 매 페이지 로드가 코레일 로그인 API 호출 한 번씩을 유발했고,
    그만큼 화면이 느려지고 호출 간격 예산도 축났다.

    Authentication state is stored in session for persistence across requests.
    """

    #: (provider, 코레일 ID) -> 로그인된 서비스 인스턴스.
    #: 사람마다 따로 둬야 여러 명이 동시에 로그인해도 서로 밀어내지 않는다.
    _services: dict[tuple[str, str], BaseTrainService] = {}
    _cache_lock = threading.Lock()

    @staticmethod
    def _get_service_key(provider: str) -> str:
        return f"_service_{provider}"

    @classmethod
    def get_service(cls, provider: str) -> Optional[BaseTrainService]:
        """
        Get a service instance for the current request.

        이미 로그인된 인스턴스가 있으면 그대로 쓴다. 없거나 세션이 끊겼을 때만
        저장된 자격증명으로 다시 로그인한다.
        """
        key = cls._get_service_key(provider)

        # Check if already created in this request
        if hasattr(g, key):
            return getattr(g, key)

        if provider != "korail":
            return None

        credentials = get_credentials(provider) if is_logged_in(provider) else None

        with cls._cache_lock:
            # 자격증명이 없는 세션에는 캐시된(남의) 로그인 인스턴스를 절대 넘기지 않는다
            cache_key = (provider, credentials["user_id"]) if credentials else None
            service = cls._services.get(cache_key) if cache_key else None

            if service is not None:
                if service.is_logged_in():
                    setattr(g, key, service)
                    return service
                # 세션이 끊긴 인스턴스는 버린다
                cls._services.pop(cache_key, None)

            service = KorailService()

            # Try to restore login from session credentials
            if credentials:
                try:
                    if service.login(credentials["user_id"], credentials["password"]):
                        cls._services[cache_key] = service
                    else:
                        # Login failed - clear stale session
                        cls._clear_auth(provider)
                except Exception:
                    # Login error - clear stale session
                    cls._clear_auth(provider)

        setattr(g, key, service)
        return service

    @classmethod
    def login(cls, provider: str, user_id: str, password: str) -> bool | str:
        """Login to a provider and store credentials in session.
        Returns True on success, or error message string on failure."""
        if provider != "korail":
            return False
        # 늘 새 인스턴스로 로그인한다. 지금 세션의 인스턴스를 재사용하면, 다른 계정으로
        # 바꿔 로그인할 때 앞 사람의 캐시된 인스턴스가 뒷사람 계정으로 바뀌어 버린다.
        service = KorailService()
        if service.login(user_id, password):
            with cls._cache_lock:
                cls._services[(provider, user_id)] = service
            setattr(g, cls._get_service_key(provider), service)
            # Store auth state
            set_auth_state(provider, user_id)
            # Store credentials for session restoration
            set_credentials(provider, user_id, password)
            return True
        if getattr(service, "last_error", None):
            return service.last_error
        return False

    @classmethod
    def logout(cls, provider: str) -> None:
        """Logout from a specific provider only."""
        key = cls._get_service_key(provider)
        credentials = get_credentials(provider)

        with cls._cache_lock:
            service = (
                cls._services.pop((provider, credentials["user_id"]), None)
                if credentials else None
            )
        if service:
            try:
                service.logout()
            except Exception:
                pass  # Ignore logout errors

        # Logout from service if exists in current request
        if hasattr(g, key):
            request_service = getattr(g, key)
            if request_service is not None and request_service is not service:
                try:
                    request_service.logout()
                except Exception:
                    pass  # Ignore logout errors
            delattr(g, key)

        # Clear session state for this provider only
        cls._clear_auth(provider)

    @classmethod
    def _clear_auth(cls, provider: str) -> None:
        """Clear authentication state for a provider."""
        clear_auth_state(provider)

    @classmethod
    def logout_all(cls) -> None:
        """Logout from all providers and clear entire session."""
        for provider in ["korail"]:
            cls.logout(provider)

        clear_all_session()

    @classmethod
    def is_logged_in(cls, provider: str) -> bool:
        """Check if logged in to a provider."""
        return is_logged_in(provider)
