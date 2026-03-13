# -*- coding: utf-8 -*-
"""Service instance manager with session-based storage."""
from flask import g
from typing import Optional

from app.services.srt_service import SRTService
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
    Manages service instances per-request.

    Service instances are stored in Flask g object (request scope).
    Authentication state is stored in session for persistence across requests.
    """

    @staticmethod
    def _get_service_key(provider: str) -> str:
        return f"_service_{provider}"

    @classmethod
    def get_service(cls, provider: str) -> Optional[BaseTrainService]:
        """
        Get or create service instance for the current request.
        If user is logged in, restores the session using stored credentials.
        """
        key = cls._get_service_key(provider)

        # Check if already created in this request
        if hasattr(g, key):
            return getattr(g, key)

        # Create new instance
        if provider == "srt":
            service = SRTService()
        elif provider == "korail":
            service = KorailService()
        else:
            return None

        # Try to restore login from session credentials
        if is_logged_in(provider):
            credentials = get_credentials(provider)
            if credentials:
                try:
                    success = service.login(
                        credentials["user_id"], credentials["password"]
                    )
                    if not success:
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

        # Logout from service if exists in current request
        if hasattr(g, key):
            service = getattr(g, key)
            if service:
                try:
                    service.logout()
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
        for provider in ["srt", "korail"]:
            key = cls._get_service_key(provider)
            if hasattr(g, key):
                service = getattr(g, key)
                if service:
                    try:
                        service.logout()
                    except Exception:
                        pass
                delattr(g, key)

        clear_all_session()

    @classmethod
    def is_logged_in(cls, provider: str) -> bool:
        """Check if logged in to a provider."""
        return is_logged_in(provider)
