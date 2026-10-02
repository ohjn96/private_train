# -*- coding: utf-8 -*-
"""Telegram bot API routes."""
from flask import Blueprint, current_app, request, jsonify

from webui.services.telegram_service import (
    TelegramService, load_saved_settings, save_settings, clear_saved_settings
)
from webui.utils.session_helper import (
    get_current_provider, is_logged_in, get_credentials,
    get_any_logged_in_provider, mask_user, current_user_id
)

bp = Blueprint('telegram', __name__, url_prefix='/api/telegram')

#: 서버 모드에서 막는 엔드포인트. 봇은 서버 전체에 하나라 아무나 바꾸거나 끊으면 안 된다.
#: (서버 모드 알림은 웹 푸시로 한다. status 는 매크로 상태 확인용이라 열어 둔다.)
_ADMIN_ONLY = {'telegram.configure', 'telegram.disconnect', 'telegram.test_message'}


#: 로그인해야 부를 수 있는 엔드포인트. PC 앱이 LAN 에 열려 있어도 옆 사람이
#: 봇을 바꿔 끼워 알림·원격 조종을 가로채지 못하게 한다.
_LOGIN_REQUIRED = _ADMIN_ONLY | {'telegram.pairing'}


@bp.before_request
def block_bot_settings_in_server_mode():
    if current_app.config.get('SERVER_MODE') and request.endpoint in _ADMIN_ONLY:
        return jsonify({'success': False,
                        'message': '서버 모드에서는 텔레그램 대신 웹 푸시 알림을 씁니다.'}), 403
    if request.endpoint in _LOGIN_REQUIRED and not is_logged_in():
        return jsonify({'success': False, 'message': '로그인이 필요합니다.'}), 401
    if request.endpoint in _LOGIN_REQUIRED and not _is_bot_owner(TelegramService.get_instance()):
        return jsonify({'success': False,
                        'message': '다른 사용자가 연결한 텔레그램 봇입니다. 그 사용자만 바꾸거나 끊을 수 있습니다.'}), 403
    return None


def _bot_owner(tg: TelegramService) -> str:
    """지금 연결된(없으면 저장돼 있는) 봇을 연결한 코레일 ID. 모르면 ''."""
    if tg.bot_token:
        return tg.owner or ''
    saved = load_saved_settings()
    return saved.get('owner', '') if saved.get('token') else ''


def _is_bot_owner(tg: TelegramService) -> bool:
    """주인이 없는 봇(환경변수·예전 설정 파일)은 로그인한 누구나 다룰 수 있다."""
    owner = _bot_owner(tg)
    return not owner or owner == current_user_id()


def _pairing_payload(tg: TelegramService) -> dict | None:
    """채팅이 아직 없으면 /start 에 붙일 일회용 코드를 알려준다 (없으면 새로 만든다)."""
    if not tg.bot_token or tg.chat_id:
        return None
    info = tg.pairing_info() or tg.new_pairing_code()
    return {**info, 'command': f"/start {info['code']}"}


@bp.route('/configure', methods=['POST'])
def configure():
    """Configure the Telegram bot with token and optional chat_id."""
    data = request.get_json(silent=True)
    if data is None:
        data = {}
    if not isinstance(data, dict):
        return jsonify({'success': False, 'message': '요청 형식이 올바르지 않습니다.'}), 400
    bot_token = data.get('bot_token') or ''
    chat_id = data.get('chat_id') or ''
    if isinstance(chat_id, int) and not isinstance(chat_id, bool):
        chat_id = str(chat_id)  # 채팅 ID 는 숫자로 보내도 받아 준다
    if not isinstance(bot_token, str) or not isinstance(chat_id, str):
        return jsonify({'success': False, 'message': '봇 토큰과 채팅 ID 는 문자열로 보내주세요.'}), 400
    bot_token, chat_id = bot_token.strip(), chat_id.strip()

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
        tg.owner = current_user_id()
        save_settings(bot_token, tg.chat_id or '', tg.owner)

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

        pairing = _pairing_payload(tg)
        if pairing:
            result['pairing'] = pairing

    return jsonify(result)


@bp.route('/pairing', methods=['GET'])
def pairing():
    """채팅 등록용 일회용 코드. 봇에게 `/start <code>` 를 보내면 그 채팅이 등록된다.

    응답: {success, paired, code?, expires_in?, command?}
    - paired=True 면 이미 채팅이 등록돼 있어 코드가 없다.
    - 코드는 10분 동안 한 번만 쓸 수 있다. 유효한 코드가 있으면 같은 걸 돌려준다.
    """
    tg = TelegramService.get_instance()
    if not tg.bot_token:
        return jsonify({'success': False, 'paired': False,
                        'message': '먼저 봇 토큰으로 연결해주세요.'})
    if tg.chat_id:
        return jsonify({'success': True, 'paired': True})
    return jsonify({'success': True, 'paired': False, **_pairing_payload(tg)})


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
            save_settings(tg.bot_token, tg.chat_id, tg.owner)
    status = tg.get_status()
    # 로그인 안 한 사람(같은 LAN 의 다른 기기 등)에게 채팅 ID 는 알려주지 않는다
    logged_in = is_logged_in()
    if not logged_in:
        status.update(chat_id='', provider='')

    # 남의 매크로 상태와 로그는 가리고, 누가 쓰는 중인지만 알려준다.
    # 로그인 안 한 사람에게는 누가·언제부터인지도 알려주지 않는다.
    from webui.routes.reservation import owns_macro
    if not logged_in or not owns_macro(tg):
        if status['macro_running']:
            status['busy'] = ({'user': mask_user(tg.macro_owner), 'since': status['macro_start_time']}
                              if logged_in else {'user': mask_user(None), 'since': None})
        status.update(macro_running=False, macro_info={}, macro_attempt=0,
                      macro_start_time=None, has_logs=False, last_result=None, resumed_at=None)
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
