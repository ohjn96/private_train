# -*- coding: utf-8 -*-
"""Telegram bot API routes."""
from flask import Blueprint, request, jsonify

from app.services.telegram_service import (
    TelegramService, load_saved_settings, save_settings, clear_saved_settings
)
from app.utils.session_helper import (
    get_current_provider, is_logged_in, get_credentials,
    get_any_logged_in_provider
)

bp = Blueprint('telegram', __name__, url_prefix='/api/telegram')


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
        from app.routes.reservation import _setup_telegram_callbacks
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
    return jsonify(tg.get_status())


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
