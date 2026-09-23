# -*- coding: utf-8 -*-
"""Telegram bot API routes."""
from flask import Blueprint, current_app, request, jsonify

from webui.services.telegram_service import (
    TelegramService, load_saved_settings, save_settings, clear_saved_settings
)
from webui.utils.session_helper import (
    get_current_provider, is_logged_in, get_credentials,
    get_any_logged_in_provider, mask_user
)

bp = Blueprint('telegram', __name__, url_prefix='/api/telegram')

#: 서버 모드에서 막는 엔드포인트. 봇은 서버 전체에 하나라 아무나 바꾸거나 끊으면 안 된다.
#: (서버 모드 알림은 웹 푸시로 한다. status 는 매크로 상태 확인용이라 열어 둔다.)
_ADMIN_ONLY = {'telegram.configure', 'telegram.disconnect', 'telegram.test_message'}


@bp.before_request
def block_bot_settings_in_server_mode():
    if current_app.config.get('SERVER_MODE') and request.endpoint in _ADMIN_ONLY:
        return jsonify({'success': False,
                        'message': '서버 모드에서는 텔레그램 대신 웹 푸시 알림을 씁니다.'}), 403
    return None


@bp.route('/configure', methods=['POST'])
def configure():
    """Configure the Telegram bot with token and optional chat_id."""
    data = request.get_json() or {}
    bot_token = data.get('bot_token', '').strip()
    chat_id = data.get('chat_id', '').strip()

    # 토큰 없이 부르면 서버에 저장해 둔 설정으로 다시 붙는다 (페이지 로드 때 자동 연결)
    if not bot_token:
        saved = load_saved_settings()
        bot_token = saved['token']
        chat_id = chat_id or saved['chat_id']
    if not bot_token:
        return jsonify({'success': False, 'message': '봇 토큰을 입력해주세요.'})

    tg = TelegramService.get_instance()
    result = tg.configure(bot_token, chat_id)

    if result['success']:
        save_settings(bot_token, tg.chat_id or '')

        # Store web session credentials if user is logged in
        try:
            provider = get_current_provider() if is_logged_in() else get_any_logged_in_provider()
            if provider:
                credentials = get_credentials(provider)
                if credentials:
                    tg.store_web_session(provider, credentials)
        except Exception:
            pass

        # Set up Telegram callbacks
        from webui.routes.reservation import _setup_telegram_callbacks
        _setup_telegram_callbacks()

        # Start polling for commands
        tg.start_polling()

    return jsonify(result)


@bp.route('/disconnect', methods=['POST'])
def disconnect():
    """Disconnect the Telegram bot."""
    tg = TelegramService.get_instance()
    tg.disconnect()
    clear_saved_settings()
    return jsonify({'success': True, 'message': '텔레그램 봇 연결이 해제되었습니다.'})


@bp.route('/status', methods=['GET'])
def status():
    """Get current Telegram bot status."""
    tg = TelegramService.get_instance()
    # /start 로 채팅 ID 가 나중에 잡히면 다음 재시작 때도 쓰도록 같이 저장한다
    if tg.bot_token and tg.chat_id:
        saved = load_saved_settings()
        if saved['token'] == tg.bot_token and saved['chat_id'] != tg.chat_id:
            save_settings(tg.bot_token, tg.chat_id)
    status = tg.get_status()

    # 남의 매크로 상태와 로그는 가리고, 누가 쓰는 중인지만 알려준다
    from webui.routes.reservation import owns_macro
    if not owns_macro(tg):
        if status['macro_running']:
            status['busy'] = {
                'user': mask_user(tg.macro_owner),
                'since': status['macro_start_time'],
            }
        status.update(macro_running=False, macro_info={}, macro_attempt=0,
                      macro_start_time=None, has_logs=False)
    return jsonify(status)


@bp.route('/test', methods=['POST'])
def test_message():
    """Send a test message to verify the connection."""
    tg = TelegramService.get_instance()

    if not tg.is_configured:
        return jsonify({
            'success': False,
            'message': '텔레그램이 설정되지 않았습니다. 봇 토큰과 Chat ID를 먼저 설정해주세요.'
        })

    sent = tg.send_message(
        "🔔 <b>테스트 메시지</b>\n"
        "━━━━━━━━━━━━━━━━━━━\n"
        "열차 예약 알림이 연결되었습니다!\n"
        "예약 성공 시 이곳으로 알림이 전송됩니다."
    )

    if sent:
        return jsonify({'success': True, 'message': '테스트 메시지가 전송되었습니다.'})
    else:
        return jsonify({'success': False, 'message': '메시지 전송에 실패했습니다. 설정을 확인해주세요.'})
