# -*- coding: utf-8 -*-
"""Authentication routes with multi-provider support."""
from flask import Blueprint, request, session, redirect, url_for, render_template

from app.services import ServiceManager
from app.utils.session_helper import (
    get_current_provider,
    set_current_provider,
    is_logged_in,
    get_logged_in_providers,
    get_any_logged_in_provider,
)

bp = Blueprint("auth", __name__)


def get_service(provider: str):
    """Get service instance - wrapper for backward compatibility."""
    return ServiceManager.get_service(provider)


@bp.route("/login", methods=["GET", "POST"])
def login():
    """Handle login."""
    provider = request.args.get("provider") or request.form.get("provider", "srt")
    logged_in_providers = get_logged_in_providers()

    if request.method == "POST":
        user_id = request.form.get("user_id", "").strip()
        password = request.form.get("password", "").strip()

        if not user_id or not password:
            return render_template(
                "login.html",
                error="아이디와 비밀번호를 입력해주세요.",
                provider=provider,
                logged_in_providers=logged_in_providers,
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
                logged_in_providers=logged_in_providers,
            )

    # GET: Already logged in to this provider? Go to search
    if is_logged_in(provider):
        set_current_provider(provider)
        return redirect(url_for("search.index"))

    return render_template(
        "login.html", provider=provider, logged_in_providers=logged_in_providers
    )


@bp.route("/logout", methods=["POST"])
def logout():
    """Handle logout - supports selective or full logout."""
    provider = request.form.get("provider") or get_current_provider()
    logout_all = request.form.get("logout_all", "false") == "true"

    if logout_all:
        ServiceManager.logout_all()
        return redirect(url_for("auth.login"))
    else:
        ServiceManager.logout(provider)

        # If another provider is logged in, switch to it
        other_provider = get_any_logged_in_provider()
        if other_provider:
            set_current_provider(other_provider)
            return redirect(url_for("search.index"))

        return redirect(url_for("auth.login"))


@bp.route("/switch/<provider>")
def switch_provider(provider: str):
    """Switch between SRT and Korail - NO logout, just switch context."""
    if provider not in ["srt", "korail"]:
        return redirect(url_for("auth.login"))

    # If logged in to this provider, just switch
    if is_logged_in(provider):
        set_current_provider(provider)
        return redirect(url_for("search.index"))

    # Not logged in - go to login page for this provider
    return redirect(url_for("auth.login", provider=provider))
