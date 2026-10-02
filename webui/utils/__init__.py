# -*- coding: utf-8 -*-
"""Utility modules for the application."""
from webui.utils.session_helper import (
    get_current_provider,
    set_current_provider,
    get_auth_state,
    set_auth_state,
    clear_auth_state,
    is_logged_in,
    get_logged_in_providers,
    get_search_state,
    set_search_trains,
    set_selected_indices,
)

__all__ = [
    'get_current_provider',
    'set_current_provider',
    'get_auth_state',
    'set_auth_state',
    'clear_auth_state',
    'is_logged_in',
    'get_logged_in_providers',
    'get_search_state',
    'set_search_trains',
    'set_selected_indices',
]
