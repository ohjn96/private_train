# -*- coding: utf-8 -*-
"""Flask 게이트 — 라이선스가 없으면 앱의 모든 기능을 막는다."""
from __future__ import annotations

from flask import Flask, jsonify, redirect, request, url_for

from app import licensing

# 라이선스가 없어도 열려 있어야 하는 엔드포인트
_OPEN_ENDPOINTS = frozenset({
    'static',
    'license.page',
    'license.activate',
    'license.status',
    'license.deactivate',
    'license.check',
})


def _wants_json() -> bool:
    if request.path.startswith('/api/'):
        return True
    if request.headers.get('X-Requested-With') == 'XMLHttpRequest':
        return True
    accept = request.accept_mimetypes
    return accept['application/json'] >= accept['text/html'] and accept['application/json'] > 0


def register(app: Flask) -> None:
    """앱의 모든 요청 앞에 라이선스 검사를 건다."""

    @app.before_request
    def _require_license():
        if request.endpoint in _OPEN_ENDPOINTS:
            return None

        status = licensing.current_status()
        if status.valid:
            return None

        if _wants_json():
            return jsonify({
                'success': False,
                'error': status.message,
                'license_required': True,
                'code': status.code,
                'machine_id': licensing.machine_id(),
            }), 403

        return redirect(url_for('license.page'))

    @app.context_processor
    def _inject_license():
        status = licensing.current_status()
        return {
            'license_status': status,
            'license_days_left': status.days_left,
            'license_expiring_soon': status.expiring_soon,
        }
