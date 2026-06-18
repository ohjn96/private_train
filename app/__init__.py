# -*- coding: utf-8 -*-
"""Flask Application Factory"""
import os
import secrets
from flask import Flask


def create_app(config_name: str = 'default') -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')

    secret_key = os.environ.get("FLASK_SECRET_KEY")
    if not secret_key:
        secret_key = secrets.token_hex(32)
        # 앱 컨텍스트 없이 logger를 쓸 수 없으므로 print 사용
        print(
            "[WARNING] FLASK_SECRET_KEY 미설정 — 임시 랜덤 키를 생성했습니다. "
            "재시작 시 모든 세션이 무효화됩니다. .env에 FLASK_SECRET_KEY를 설정하세요."
        )
    app.secret_key = secret_key

    # Register blueprints
    from app.routes import auth, search, reservation
    app.register_blueprint(auth.bp)
    app.register_blueprint(search.bp)
    app.register_blueprint(reservation.bp)

    # .env에 자격증명이 있으면 로그인 페이지에 기본값으로 채워주기 위해 앱 설정에 저장
    app.config['DEFAULT_SRT_ID'] = os.environ.get('SRT_ID', '')
    app.config['DEFAULT_SRT_PW'] = os.environ.get('SRT_PW', '')
    app.config['DEFAULT_KORAIL_ID'] = os.environ.get('KORAIL_ID', '')
    app.config['DEFAULT_KORAIL_PW'] = os.environ.get('KORAIL_PW', '')

    return app
