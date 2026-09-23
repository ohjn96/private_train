# -*- coding: utf-8 -*-
"""Authentication routes (코레일 단일 서비스)."""
from flask import Blueprint, request, session, redirect, url_for, render_template

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
