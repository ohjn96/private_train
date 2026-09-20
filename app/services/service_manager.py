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

    #: provider -> 로그인된 서비스 인스턴스
    _services: dict[str, BaseTrainService] = {}
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
            service = cls._services.get(provider)

            if service is not None:
                same_user = (
                    credentials is None
                    or getattr(service, "_user_id", None) == credentials["user_id"]
                )
                if same_user and service.is_logged_in():
                    setattr(g, key, service)
                    return service
                # 계정이 바뀌었거나 세션이 끊긴 인스턴스는 버린다
                cls._services.pop(provider, None)

            service = KorailService()

            # Try to restore login from session credentials
            if credentials:
                try:
                    if service.login(credentials["user_id"], credentials["password"]):
                        cls._services[provider] = service
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
        service = cls.get_service(provider)
        if service and service.login(user_id, password):
            with cls._cache_lock:
                cls._services[provider] = service
            # Store auth state
            set_auth_state(provider, user_id)
            # Store credentials for session restoration
            set_credentials(provider, user_id, password)
            return True
        if service and hasattr(service, "last_error") and service.last_error:
            return service.last_error
        return False

    @classmethod
    def logout(cls, provider: str) -> None:
        """Logout from a specific provider only."""
        key = cls._get_service_key(provider)

        with cls._cache_lock:
            service = cls._services.pop(provider, None)
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
