# -*- coding: utf-8 -*-
"""라이선스 등록 화면과 처리."""
from urllib.parse import quote

from flask import Blueprint, jsonify, redirect, render_template, request, url_for

from app import licensing
from app.licensing import config
from app.utils.session_helper import PROVIDER

bp = Blueprint('license', __name__, url_prefix='/license')


def _request_url(machine_id: str) -> str:
    """머신 ID 가 미리 채워진 GitHub Issue 폼 주소."""
    return config.REQUEST_FORM_URL.format(machine_id=machine_id)


def _mailto_link(machine_id: str) -> str:
    subject = quote(f'[열차예약] 라이선스 요청 - {machine_id}')
    body = quote(
        '아래 내용을 그대로 보내주세요.\n\n'
        f'머신 ID: {machine_id}\n'
        '이름/용도: \n'
        '희망 사용 기간: \n'
    )
    return f'mailto:{config.OWNER_EMAIL}?subject={subject}&body={body}'


@bp.route('', methods=['GET'])
def page():
    """라이선스 등록 화면. 이미 유효하면 앱으로 돌려보낸다."""
    status = licensing.current_status(refresh=True)
    if status.valid and request.args.get('manage') != '1':
        return redirect(url_for('search.index'))

    mid = licensing.machine_id()
    return render_template(
        'license.html',
        provider=PROVIDER,
        machine_id=mid,
        status=status,
        owner_email=config.OWNER_EMAIL,
        issue_url=config.OWNER_ISSUE_URL,
        request_url=_request_url(mid),
        mailto=_mailto_link(mid),
    )


@bp.route('/check', methods=['POST'])
def check():
    """승인된 키가 올라왔는지 확인하고, 있으면 바로 등록한다.

    라이선스 화면이 이 엔드포인트를 주기적으로 두드린다. 사용자는 승인만 기다리면 된다.
    """
    status = licensing.try_auto_activate()
    return jsonify({
        'valid': status.valid,
        'code': status.code,
        'message': status.message,
        'days_left': status.days_left,
    })


@bp.route('/activate', methods=['POST'])
def activate():
    """붙여넣은 키를 검증하고 저장."""
    token = request.form.get('license_key', '')
    mid = licensing.machine_id()

    try:
        licensing.activate(token)
    except licensing.LicenseError as exc:
        return render_template(
            'license.html',
            provider=PROVIDER,
            machine_id=mid,
            status=licensing.current_status(refresh=True),
            error=exc.message,
            submitted_key=token,
            owner_email=config.OWNER_EMAIL,
            issue_url=config.OWNER_ISSUE_URL,
            request_url=_request_url(mid),
            mailto=_mailto_link(mid),
        ), 400

    return redirect(url_for('search.index'))


@bp.route('/deactivate', methods=['POST'])
def deactivate():
    licensing.deactivate()
    return redirect(url_for('license.page'))


@bp.route('/status', methods=['GET'])
def status():
    current = licensing.current_status(refresh=True)
    payload = {
        'valid': current.valid,
        'code': current.code,
        'message': current.message,
        'machine_id': licensing.machine_id(),
    }
    if current.license:
        payload['license'] = current.license.to_public_dict()
    return jsonify(payload)
