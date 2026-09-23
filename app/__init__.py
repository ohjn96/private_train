# -*- coding: utf-8 -*-
"""Flask Application Factory"""
import hmac
import logging
import os
import secrets
import time
from pathlib import Path

from flask import Flask, redirect, render_template, request, session, url_for

logger = logging.getLogger(__name__)

#: FLASK_SECRET_KEY 를 안 줬을 때 무작위로 만든 키를 두는 곳 (텔레그램 설정과 같은 폴더)
SECRET_KEY_PATH = Path.home() / '.train_reservation' / 'secret_key'

#: 접근 비밀번호 없이도 열려야 하는 엔드포인트
_GATE_EXEMPT = {'gate', 'static'}


def _load_secret_key() -> str:
    """세션 서명 키. 코드에 박아 두면 누구나 세션 쿠키를 위조할 수 있다.

    환경변수가 있으면 그걸 쓰고, 없으면 처음 한 번 무작위로 만들어 파일(600)에 남긴다.
    파일에 남기는 건 재시작 뒤에도 기존 쿠키가 유효하도록 하기 위해서다.
    """
    key = os.environ.get('FLASK_SECRET_KEY')
    if key:
        return key
    try:
        return SECRET_KEY_PATH.read_text(encoding='utf-8').strip() or _write_secret_key()
    except OSError:
        return _write_secret_key()


def _write_secret_key() -> str:
    key = secrets.token_urlsafe(48)
    try:
        SECRET_KEY_PATH.parent.mkdir(parents=True, exist_ok=True)
        fd = os.open(SECRET_KEY_PATH, os.O_WRONLY | os.O_CREAT | os.O_TRUNC, 0o600)
        with os.fdopen(fd, 'w', encoding='utf-8') as f:
            f.write(key)
    except OSError as e:
        # 저장을 못 해도 이번 실행은 이 키로 돈다. 재시작하면 다시 로그인하면 된다.
        logger.warning(f"세션 키 저장 실패: {e}")
    return key


def create_app(config_name: str = 'default') -> Flask:
    """Create and configure the Flask application."""
    app = Flask(__name__,
                template_folder='templates',
                static_folder='static')

    app.secret_key = _load_secret_key()
    # 자바스크립트에서 세션 쿠키를 못 읽게, 다른 사이트에서 온 POST 에는 안 실리게
    app.config.update(SESSION_COOKIE_HTTPONLY=True, SESSION_COOKIE_SAMESITE='Lax')

    # 모든 템플릿에서 버전을 쓸 수 있게 (단일 출처는 루트 VERSION 파일)
    from app.version import get_version

    @app.context_processor
    def inject_version():
        return {'app_version': get_version()}

    _install_access_gate(app)

    # Register blueprints
    from app.routes import auth, search, reservation, telegram
    app.register_blueprint(auth.bp)
    app.register_blueprint(search.bp)
    app.register_blueprint(reservation.bp)
    app.register_blueprint(telegram.bp)

    return app


def _install_access_gate(app: Flask) -> None:
    """APP_PASSWORD 가 설정돼 있으면 모든 페이지 앞에 접근 비밀번호를 세운다.

    서버를 외부(Tailscale 등)에 열었을 때 코레일 로그인 화면까지 아무나 오지 못하게
    막는 1차 관문이다. 로컬에서 혼자 쓸 땐 비워 두면 예전과 똑같이 동작한다.
    """
    password = os.environ.get('APP_PASSWORD', '')
    if not password:
        return

    @app.before_request
    def require_gate():
        if request.endpoint in _GATE_EXEMPT or session.get('gate_ok'):
            return None
        if request.path.startswith('/api/'):
            return {'success': False, 'message': '접근 비밀번호가 필요합니다.'}, 401
        return redirect(url_for('gate', next=request.full_path))

    @app.route('/gate', methods=['GET', 'POST'])
    def gate():
        error = None
        if request.method == 'POST':
            given = request.form.get('password', '')
            if hmac.compare_digest(given.encode(), password.encode()):
                session['gate_ok'] = True
                target = request.args.get('next') or '/'
                # 외부 주소로 튕기지 않도록 같은 사이트 경로만 허용
                if not target.startswith('/') or target.startswith('//'):
                    target = '/'
                return redirect(target)
            time.sleep(1)  # 무차별 대입을 느리게
            error = '비밀번호가 틀렸습니다.'
        return render_template('gate.html', error=error)
