# -*- coding: utf-8 -*-
"""Flask Application Factory"""
import os
from flask import Flask


def create_app(config_name: str = 'default') -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')

    app.secret_key = os.environ.get("FLASK_SECRET_KEY", "train_reservation_secret_key_2024")

    # 모든 템플릿에서 버전을 쓸 수 있게 (단일 출처는 루트 VERSION 파일)
    from app.version import get_version

    @app.context_processor
    def inject_version():
        return {'app_version': get_version()}

    # Register blueprints
    from app.routes import auth, search, reservation, telegram
    app.register_blueprint(auth.bp)
    app.register_blueprint(search.bp)
    app.register_blueprint(reservation.bp)
    app.register_blueprint(telegram.bp)

    return app
