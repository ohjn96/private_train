# -*- coding: utf-8 -*-
"""Authentication routes (코레일 단일 서비스)."""
from flask import Blueprint, current_app, request, session, redirect, url_for, render_template

from webui.services import ServiceManager
from webui.utils.session_helper import (
    PROVIDER,
    get_current_provider,
    set_current_provider,
    is_logged_in,
)

bp = Blueprint("auth", __name__)


def get_service(provider: str):
    """Get service instance - wrapper for backward compatibility."""
    return ServiceManager.get_service(provider)


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Handle login."""
    provider = PROVIDER

    if request.method == "POST":
        # IP 마다 1분에 10번까지 (성공·실패 모두 센다). 남의 코레일 계정 비밀번호를
        # 여기서 대입해 보지 못하게. 성공으로 초기화하면 내 계정으로 끼워 넣어 우회할 수 있다.
        throttle = current_app.extensions.get("login_throttle")
        ip = request.remote_addr or "?"
        if throttle is not None:
            if throttle.blocked(ip):
                return render_template(
                    "login.html",
                    error="로그인 시도가 너무 많습니다. 1분 뒤에 다시 시도해주세요.",
                    provider=provider,
                ), 429
            throttle.fail(ip)

        user_id = request.form.get("user_id", "").strip()
        password = request.form.get("password", "").strip()

        if not user_id or not password:
            return render_template(
                "login.html",
                error="아이디와 비밀번호를 입력해주세요.",
                provider=provider,
            )

        result = ServiceManager.login(provider, user_id, password)
        if result is True:
            set_current_provider(provider)
            return redirect(url_for("search.index"))
        else:
            error_msg = (
                result
                if isinstance(result, str)
                else "로그인에 실패했습니다. 아이디와 비밀번호를 확인해주세요."
            )
            return render_template(
                "login.html",
                error=error_msg,
                provider=provider,
            )

    # GET: Already logged in to this provider? Go to search
    if is_logged_in(provider):
        set_current_provider(provider)
        return redirect(url_for("search.index"))

    return render_template("login.html", provider=provider)


@bp.route("/logout", methods=["POST"])
def logout():
    """Handle logout."""
    if request.form.get("logout_all", "false") == "true":
        ServiceManager.logout_all()
    else:
        ServiceManager.logout(get_current_provider())

    return redirect(url_for("auth.login"))
