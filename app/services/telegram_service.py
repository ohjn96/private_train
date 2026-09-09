# -*- coding: utf-8 -*-
"""Telegram Bot Service for train reservation notifications and remote control."""
import collections
import json
import logging
import threading
import time
from datetime import datetime
from typing import Optional, Callable

import requests

logger = logging.getLogger(__name__)


class TelegramService:
    """
    Telegram Bot integration service.
    
    Features:
    - Send reservation success notifications
    - Remote control via bot commands (/start, /stop, /status, /help)
    - Persistent polling for incoming commands
    """

    _instance: Optional['TelegramService'] = None
    _lock = threading.Lock()

    def __init__(self):
        self.bot_token: Optional[str] = None
        self.chat_id: Optional[str] = None
        self._polling_thread: Optional[threading.Thread] = None
        self._polling_active = False
        self._last_update_id = 0
        self._connected = False

        # Callbacks for remote control
        self._on_start_callback: Optional[Callable] = None
        self._on_stop_callback: Optional[Callable] = None
        self._on_status_callback: Optional[Callable[[], str]] = None
        self._on_reserve_callback: Optional[Callable] = None
        self._on_trains_callback: Optional[Callable[[], str]] = None

        # Macro state tracking
        self._macro_running = False
        self._macro_info: dict = {}
        self._macro_start_time: Optional[datetime] = None
        self._macro_attempt: int = 0
        self._last_reserve_params: Optional[dict] = None

        # Stored web session for background thread usage
        self._stored_provider: Optional[str] = None
        self._stored_credentials: Optional[dict] = None
        self._stored_card_settings: Optional[dict] = None

        # Shared log queue for web ↔ Telegram sync
        self._macro_logs: collections.deque = collections.deque(maxlen=500)
        self._log_event = threading.Event()
        self._log_counter = 0

        # Pending /reserve flow state per chat
        self._pending_reserve: Optional[dict] = None

    @classmethod
    def get_instance(cls) -> 'TelegramService':
        """Get singleton instance."""
        if cls._instance is None:
            with cls._lock:
                if cls._instance is None:
                    cls._instance = cls()
        return cls._instance

    @property
    def is_configured(self) -> bool:
        """Check if bot token and chat_id are set."""
        return bool(self.bot_token and self.chat_id)

    @property
    def is_connected(self) -> bool:
        """Check if the bot is actively connected."""
        return self._connected and self._polling_active

    # ─── Configuration ───────────────────────────────────────────

    def configure(self, bot_token: str, chat_id: str = '') -> dict:
        """
        Configure the telegram bot.
        
        Args:
            bot_token: Telegram Bot API token from @BotFather
            chat_id: Optional chat ID (can be auto-detected via /start)
            
        Returns:
            dict with success status and bot info
        """
        # Validate token by calling getMe
        try:
            resp = requests.get(
                f'https://api.telegram.org/bot{bot_token}/getMe',
                timeout=15
            )
            data = resp.json()
            if not data.get('ok'):
                return {'success': False, 'message': '유효하지 않은 봇 토큰입니다.'}

            bot_info = data['result']
            self.bot_token = bot_token
            if chat_id:
                self.chat_id = str(chat_id)

            return {
                'success': True,
                'message': f"봇 연결 성공: @{bot_info['username']}",
                'bot_name': bot_info.get('first_name', ''),
                'bot_username': bot_info.get('username', '')
            }
        except requests.RequestException as e:
            return {'success': False, 'message': f'연결 오류: {str(e)}'}

    def disconnect(self):
        """Disconnect and reset the bot."""
        self.stop_polling()
        self.bot_token = None
        self.chat_id = None
        self._connected = False

    # ─── Message Sending ─────────────────────────────────────────

    def _api_call(self, method: str, data: dict = None) -> Optional[dict]:
        """Make a Telegram Bot API call with retry."""
        if not self.bot_token:
            return None

        # getUpdates uses long polling — needs longer HTTP timeout
        if method == 'getUpdates':
            poll_timeout = (data or {}).get('timeout', 10)
            http_timeout = poll_timeout + 10  # margin above long-poll
        else:
            http_timeout = 15

        max_retries = 3
        for attempt in range(max_retries):
            try:
                resp = requests.post(
                    f'https://api.telegram.org/bot{self.bot_token}/{method}',
                    json=data,
                    timeout=http_timeout
                )
                result = resp.json()
                if result.get('ok'):
                    return result.get('result')
                else:
                    logger.warning(f"Telegram API error: {result.get('description')}")
                    return None
            except requests.Timeout:
                logger.warning(f"Telegram API timeout ({method}), retry {attempt+1}/{max_retries}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                continue
            except requests.RequestException as e:
                logger.error(f"Telegram API request failed: {e}")
                if attempt < max_retries - 1:
                    time.sleep(2)
                    continue
                return None
        return None

    def send_message(self, text: str, parse_mode: str = 'HTML') -> bool:
        """
        Send a message to the configured chat.
        
        Args:
            text: Message text (supports HTML formatting)
            parse_mode: 'HTML' or 'Markdown'
            
        Returns:
            True if sent successfully
        """
        if not self.is_configured:
            logger.warning("Telegram not configured, skipping message")
            return False

        result = self._api_call('sendMessage', {
            'chat_id': self.chat_id,
            'text': text,
            'parse_mode': parse_mode
        })
        return result is not None

    def send_reservation_success(self, train_name: str, dep_time: str,
                                  dep_station: str = '', arr_station: str = '',
                                  reservation_id: str = '') -> bool:
        """
        Send a formatted reservation success notification.
        
        Args:
            train_name: Name of the train (e.g., 'SRT 301')
            dep_time: Departure time formatted
            dep_station: Departure station name
            arr_station: Arrival station name  
            reservation_id: Reservation ID if available
        """
        timestamp = datetime.now().strftime('%Y-%m-%d %H:%M:%S')

        message = (
            "🎉 <b>예약 성공!</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"🚄 <b>열차:</b> {train_name}\n"
            f"🕐 <b>출발:</b> {dep_time}\n"
        )
        if dep_station and arr_station:
            message += f"📍 <b>구간:</b> {dep_station} → {arr_station}\n"
        if reservation_id:
            message += f"🔖 <b>예약번호:</b> {reservation_id}\n"
        message += (
            "━━━━━━━━━━━━━━━━━━━\n"
            f"⏰ {timestamp}"
        )

        return self.send_message(message)

    def send_macro_started(self, train_count: int, trains_info: str = '') -> bool:
        """Send notification that macro has started."""
        message = (
            "▶️ <b>예약 매크로 시작</b>\n"
            f"🔢 대상 열차: {train_count}개\n"
        )
        if trains_info:
            message += f"🚄 {trains_info}\n"
        message += f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        return self.send_message(message)

    def send_macro_stopped(self, reason: str = '사용자 중단') -> bool:
        """Send notification that macro has stopped."""
        message = (
            f"⏹️ <b>예약 매크로 중단</b>\n"
            f"📝 사유: {reason}\n"
            f"⏰ {datetime.now().strftime('%H:%M:%S')}"
        )
        return self.send_message(message)

    # ─── Polling for Commands ────────────────────────────────────

    def set_callbacks(self, on_start: Callable = None,
                      on_stop: Callable = None,
                      on_status: Callable[[], str] = None,
                      on_reserve: Callable = None,
                      on_trains: Callable[[], str] = None):
        """
        Set callback functions for remote commands.
        
        Args:
            on_start: Called when /start_macro received
            on_stop: Called when /stop received  
            on_status: Called when /status received, should return status string
            on_reserve: Called when /reserve received with (dep, arr, date, time, train_indices) 
            on_trains: Called when /trains received, should return trains list string
        """
        self._on_start_callback = on_start
        self._on_stop_callback = on_stop
        self._on_status_callback = on_status
        self._on_reserve_callback = on_reserve
        self._on_trains_callback = on_trains

    def set_macro_state(self, running: bool, info: dict = None):
        """Update macro running state for status reporting."""
        self._macro_running = running
        if running:
            if info:
                self._macro_info.update(info)
            if not self._macro_start_time:
                self._macro_start_time = datetime.now()
                self._macro_attempt = 0
        else:
            self._macro_info = {}
            self._macro_start_time = None
            self._macro_attempt = 0

    def update_attempt(self, attempt: int):
        """Update current macro attempt count."""
        self._macro_attempt = attempt

    def push_log(self, event_type: str, message: str, **extra):
        """Push a log event to shared buffer for web/Telegram sync."""
        self._log_counter += 1
        event = {'id': self._log_counter, 'type': event_type, 'message': message, **extra}
        self._macro_logs.append(event)
        self._log_event.set()

    def clear_logs(self):
        """Clear the log buffer."""
        self._macro_logs.clear()
        self._log_counter = 0
        self._log_event.clear()

    def store_web_session(self, provider: str, credentials: dict):
        """Store web session info for use in background threads (e.g., Telegram /reserve)."""
        self._stored_provider = provider
        self._stored_credentials = dict(credentials) if credentials else None
        logger.info(f"Web session stored for provider: {provider}")

    def store_card_settings(self, card_settings: Optional[dict]):
        """Store card auto-payment settings for use in background threads."""
        self._stored_card_settings = dict(card_settings) if card_settings else None

    def get_stored_card_settings(self) -> Optional[dict]:
        """Return card auto-payment settings captured for the current standalone service."""
        return self._stored_card_settings

    def create_standalone_service(self):
        """Create a standalone service instance using stored credentials.

        Returns:
            tuple: (service, provider) or (None, None) on failure
        """
        if not self._stored_provider or not self._stored_credentials:
            return None, None

        from app.services.srt_service import SRTService
        from app.services.korail_service import KorailService

        provider = self._stored_provider
        if provider == 'srt':
            service = SRTService()
        elif provider == 'korail':
            service = KorailService()
        else:
            return None, None

        try:
            success = service.login(
                self._stored_credentials['user_id'],
                self._stored_credentials['password']
            )
            if success:
                return service, provider
            else:
                logger.warning(f"Failed to login for standalone service: {provider}")
                return None, None
        except Exception as e:
            logger.error(f"Error creating standalone service: {e}")
            return None, None

    def start_polling(self):
        """Start polling for incoming bot commands in a background thread."""
        if self._polling_active:
            return

        if not self.bot_token:
            logger.warning("Cannot start polling: bot token not set")
            return

        self._polling_active = True
        self._connected = True
        self._polling_thread = threading.Thread(
            target=self._poll_loop,
            daemon=True,
            name='telegram-polling'
        )
        self._polling_thread.start()
        logger.info("Telegram bot polling started")

    def stop_polling(self):
        """Stop the polling loop."""
        self._polling_active = False
        self._connected = False
        if self._polling_thread and self._polling_thread.is_alive():
            self._polling_thread.join(timeout=5)
        self._polling_thread = None
        logger.info("Telegram bot polling stopped")

    def _poll_loop(self):
        """Background polling loop for incoming updates."""
        consecutive_errors = 0
        while self._polling_active:
            try:
                updates = self._get_updates()
                consecutive_errors = 0  # reset on success
                if updates:
                    for update in updates:
                        self._handle_update(update)
                        self._last_update_id = update['update_id'] + 1
            except Exception as e:
                consecutive_errors += 1
                logger.error(f"Telegram polling error: {e}")
                # Back off on repeated failures (max 30s)
                backoff = min(consecutive_errors * 3, 30)
                time.sleep(backoff)
            time.sleep(0.5)

    def _get_updates(self) -> list:
        """Fetch new updates from Telegram."""
        result = self._api_call('getUpdates', {
            'offset': self._last_update_id,
            'timeout': 10,
            'allowed_updates': ['message']
        })
        return result if isinstance(result, list) else []

    def _handle_update(self, update: dict):
        """Process an incoming update (message/command)."""
        message = update.get('message', {})
        text = message.get('text', '').strip()
        chat = message.get('chat', {})
        incoming_chat_id = str(chat.get('id', ''))
        user_name = message.get('from', {}).get('first_name', '알 수 없음')

        if not text:
            return

        # Auto-register chat_id on first /start
        if text == '/start' and not self.chat_id:
            self.chat_id = incoming_chat_id
            self.send_message(
                f"✅ <b>연결 완료!</b>\n"
                f"👋 안녕하세요, {user_name}님!\n"
                f"이제 예약 알림을 받을 수 있습니다.\n\n"
                f"📋 <b>사용 가능한 명령어:</b>\n"
                f"/reserve 출발역 도착역 날짜 시간 - 열차검색/예약\n"
                f"/trains - 마지막 검색 결과 보기\n"
                f"/stop - 매크로 중단\n"
                f"/restart - 최신 데이터로 재시작\n"
                f"/status - 상태 확인\n"
                f"/help - 도움말"
            )
            return

        # Only respond to the registered chat
        if self.chat_id and incoming_chat_id != self.chat_id:
            return

        # Check if there's a pending reserve selection (non-command text)
        if not text.startswith('/') and self._pending_reserve:
            self._handle_reserve_selection(text)
            return

        if not text.startswith('/'):
            return

        # Handle commands
        parts = text.split()
        command = parts[0].lower().split('@')[0]  # strip @botname

        if command == '/start':
            self.send_message(
                f"👋 이미 연결되어 있습니다, {user_name}님!\n"
                f"/help 를 입력하여 명령어를 확인하세요."
            )

        elif command == '/help':
            self.send_message(
                "📋 <b>명령어 목록</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "/reserve [출발] [도착] [날짜] [시간]\n"
                "  └ 열차 검색 후 예약 시작\n"
                "  └ 예: <code>/reserve 수서 부산 2026-03-01 06:00</code>\n\n"
                "/trains - 마지막 검색 열차 목록\n"
                "/stop - 예약 매크로 중단\n"
                "/restart - 최신 데이터로 매크로 재시작\n"
                "/status - 현재 상태 확인\n"
                "/chatid - 채팅 ID 확인\n"
                "/help - 이 도움말 표시"
            )

        elif command == '/stop':
            if self._on_stop_callback:
                try:
                    self._on_stop_callback()
                    self.send_message("⏹️ 예약 매크로 중단 요청을 보냈습니다.")
                except Exception as e:
                    self.send_message(f"❌ 중단 오류: {str(e)}")
            else:
                self.send_message("ℹ️ 현재 실행 중인 매크로가 없습니다.")

        elif command == '/status':
            self._send_detailed_status()

        elif command == '/restart':
            self._handle_restart_command()

        elif command == '/chatid':
            self.send_message(f"🔑 <b>Chat ID:</b> <code>{incoming_chat_id}</code>")

        elif command == '/reserve':
            self._handle_reserve_command(parts[1:])

        elif command == '/trains':
            if self._on_trains_callback:
                try:
                    trains_text = self._on_trains_callback()
                    self.send_message(trains_text)
                except Exception as e:
                    self.send_message(f"❌ 열차 목록 조회 오류: {str(e)}")
            else:
                self.send_message("ℹ️ 검색된 열차가 없습니다.\n/reserve 명령어로 먼저 검색해주세요.")

        else:
            self.send_message(
                f"❓ 알 수 없는 명령어입니다.\n/help 를 입력하여 명령어를 확인하세요."
            )

    def _handle_reserve_command(self, args: list):
        """
        Handle /reserve command.
        Usage: /reserve 출발역 도착역 날짜 시간
        Example: /reserve 수서 부산 2026-03-01 06:00
        """
        # Prevent concurrent macro execution
        if self._macro_running:
            self.send_message(
                "⚠️ 현재 매크로가 실행 중입니다.\n"
                "먼저 /stop 으로 중단 후 다시 시도해주세요."
            )
            return

        if len(args) < 4:
            self.send_message(
                "📝 <b>/reserve 사용법</b>\n"
                "━━━━━━━━━━━━━━━━━━━\n"
                "<code>/reserve 출발역 도착역 날짜 시간</code>\n\n"
                "예시:\n"
                "<code>/reserve 수서 부산 2026-03-01 06:00</code>\n"
                "<code>/reserve 서울 부산 2026-03-15 14:00</code>\n\n"
                "날짜 형식: YYYY-MM-DD\n"
                "시간 형식: HH:MM"
            )
            return

        dep = args[0]
        arr = args[1]
        date_str = args[2]
        time_str = args[3]

        # Validate date format
        try:
            parsed_date = datetime.strptime(date_str, '%Y-%m-%d')
            date_fmt = parsed_date.strftime('%Y%m%d')
        except ValueError:
            self.send_message("❌ 날짜 형식이 올바르지 않습니다.\n예: 2026-03-01")
            return

        # Validate time format
        try:
            parsed_time = datetime.strptime(time_str, '%H:%M')
            time_fmt = parsed_time.strftime('%H%M%S')
        except ValueError:
            self.send_message("❌ 시간 형식이 올바르지 않습니다.\n예: 06:00")
            return

        self.send_message(
            f"🔍 <b>열차 검색 중...</b>\n"
            f"📍 {dep} → {arr}\n"
            f"📅 {date_str} {time_str}"
        )

        # Call the reserve callback to search
        if self._on_reserve_callback:
            try:
                result = self._on_reserve_callback(
                    action='search',
                    dep=dep,
                    arr=arr,
                    date=date_fmt,
                    time=time_fmt
                )

                if not result.get('success'):
                    self.send_message(f"❌ {result.get('message', '검색 실패')}")
                    return

                trains = result.get('trains', [])
                if not trains:
                    self.send_message("😔 검색 결과가 없습니다.")
                    return

                # Build train list message
                msg = (
                    f"🚄 <b>검색 결과 ({len(trains)}건)</b>\n"
                    f"📍 {dep} → {arr} | 📅 {date_str}\n"
                    "━━━━━━━━━━━━━━━━━━━\n"
                )
                for i, t in enumerate(trains):
                    seat_status = ""
                    if t.get('general_seat_available'):
                        seat_status += "🟢일반 "
                    else:
                        seat_status += "🔴일반 "
                    if t.get('special_seat_available'):
                        seat_status += "🟢특실"
                    else:
                        seat_status += "🔴특실"

                    dep_time_fmt = f"{t['dep_time'][:2]}:{t['dep_time'][2:4]}"
                    arr_time_fmt = f"{t['arr_time'][:2]}:{t['arr_time'][2:4]}"

                    msg += (
                        f"\n<b>{i+1}.</b> {t['train_name']} "
                        f"{dep_time_fmt}→{arr_time_fmt} "
                        f"{seat_status}"
                    )

                msg += (
                    "\n\n━━━━━━━━━━━━━━━━━━━\n"
                    "📌 예약할 열차 번호를 입력하세요\n"
                    "예: <code>1,3,5</code> 또는 <code>2</code>\n"
                    "취소: <code>/cancel</code>"
                )
                self.send_message(msg)

                # Store pending state
                self._pending_reserve = {
                    'dep': dep,
                    'arr': arr,
                    'date': date_fmt,
                    'time': time_fmt,
                    'trains': trains
                }

            except Exception as e:
                self.send_message(f"❌ 검색 오류: {str(e)}")
        else:
            self.send_message(
                "⚠️ 웹에서 먼저 로그인 후 사용해주세요.\n"
                "현재 로그인된 세션이 없습니다."
            )

    def _handle_reserve_selection(self, text: str):
        """Handle train selection after /reserve search results."""
        if text.lower() in ['/cancel', 'cancel', '취소']:
            self._pending_reserve = None
            self.send_message("🚫 예약이 취소되었습니다.")
            return

        pending = self._pending_reserve
        trains = pending.get('trains', [])

        # Parse indices
        try:
            raw_indices = [s.strip() for s in text.replace(' ', ',').split(',') if s.strip()]
            selected_indices = []
            for s in raw_indices:
                idx = int(s) - 1  # 1-based to 0-based
                if 0 <= idx < len(trains):
                    selected_indices.append(idx)
                else:
                    self.send_message(f"⚠️ 번호 {s}은(는) 범위를 벗어납니다 (1~{len(trains)})")
                    return
        except ValueError:
            self.send_message("⚠️ 숫자를 입력해주세요.\n예: <code>1,3,5</code>\n취소: <code>/cancel</code>")
            return

        if not selected_indices:
            self.send_message("⚠️ 최소 1개의 열차를 선택해주세요.\n취소: <code>/cancel</code>")
            return

        selected_trains = [trains[i] for i in selected_indices]

        # Prevent concurrent macro execution (race condition between search and selection)
        if self._macro_running:
            self.send_message(
                "⚠️ 현재 매크로가 실행 중입니다.\n"
                "먼저 /stop 으로 중단 후 다시 시도해주세요."
            )
            self._pending_reserve = None
            return

        train_names = ', '.join(
            f"{t['train_name']}({t['dep_time'][:2]}:{t['dep_time'][2:4]})"
            for t in selected_trains
        )

        self.send_message(
            f"▶️ <b>예약 매크로를 시작합니다!</b>\n"
            f"🚄 대상: {train_names}\n"
            f"📍 {pending['dep']} → {pending['arr']}\n\n"
            f"중단하려면 /stop 입력"
        )

        # Start the reservation via callback
        if self._on_reserve_callback:
            try:
                result = self._on_reserve_callback(
                    action='start',
                    dep=pending['dep'],
                    arr=pending['arr'],
                    date=pending['date'],
                    time=pending['time'],
                    train_indices=selected_indices,
                    trains=trains
                )
                if not result.get('success'):
                    self.send_message(f"❌ {result.get('message', '예약 시작 실패')}")
            except Exception as e:
                self.send_message(f"❌ 예약 시작 오류: {str(e)}")

        self._pending_reserve = None

    # ─── Status & Restart ────────────────────────────────────────

    def _send_detailed_status(self):
        """Send detailed status message via Telegram."""
        running = "실행 중 ▶️" if self._macro_running else "대기 중 ⏸️"
        info = self._macro_info

        msg = (
            "📊 <b>현재 상태</b>\n"
            "━━━━━━━━━━━━━━━━━━━\n"
            f"상태: {running}\n"
        )

        # Provider info
        if self._stored_provider:
            provider_name = 'SRT' if self._stored_provider == 'srt' else 'KTX(코레일)'
            msg += f"🚄 서비스: {provider_name}\n"

        # Connection info
        msg += f"📡 텔레그램: {'연결됨 ✅' if self.is_connected else '미연결 ❌'}\n"

        if self._macro_running:
            msg += "\n"
            # Route info
            if info.get('dep') and info.get('arr'):
                msg += f"📍 <b>구간:</b> {info['dep']} → {info['arr']}\n"
            if info.get('date'):
                date_str = info['date']
                if len(date_str) == 8:
                    date_str = f"{date_str[:4]}-{date_str[4:6]}-{date_str[6:8]}"
                msg += f"📅 <b>날짜:</b> {date_str}\n"

            # Selected trains
            if info.get('trains'):
                msg += f"🎯 <b>대상:</b> {info['trains']}\n"

            # Attempt count
            if self._macro_attempt > 0:
                msg += f"🔄 <b>시도:</b> {self._macro_attempt}회\n"

            # Elapsed time
            if self._macro_start_time:
                elapsed = datetime.now() - self._macro_start_time
                total_seconds = int(elapsed.total_seconds())
                hours = total_seconds // 3600
                minutes = (total_seconds % 3600) // 60
                seconds = total_seconds % 60
                if hours > 0:
                    msg += f"⏱️ <b>경과:</b> {hours}시간 {minutes}분 {seconds}초\n"
                else:
                    msg += f"⏱️ <b>경과:</b> {minutes}분 {seconds}초\n"
        else:
            msg += "\nℹ️ 실행 중인 매크로가 없습니다.\n"
            msg += "/reserve 로 새 예약을 시작하세요.\n"

        msg += f"\n⏰ {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
        self.send_message(msg)

    def _handle_restart_command(self):
        """Handle /restart command - restart the last macro with latest data."""
        # Check if we have stored reserve params to restart with
        params = self._last_reserve_params
        if not params:
            self.send_message(
                "⚠️ 재시작할 데이터가 없습니다.\n"
                "이전에 실행된 매크로가 없습니다.\n"
                "/reserve 명령어로 새로 시작해주세요."
            )
            return

        # Stop current macro if running
        if self._macro_running:
            if self._on_stop_callback:
                self._on_stop_callback()
            self.send_message("⏹️ 현재 매크로를 중단합니다...")
            time.sleep(2)

        self.send_message("🔄 <b>매크로 재시작 중...</b>\n최신 데이터로 다시 시작합니다.")

        # Re-search with same parameters
        dep = params['dep']
        arr = params['arr']
        date = params['date']
        time_str = params['time']
        old_train_indices = params.get('train_indices', [])
        old_trains = params.get('trains', [])

        # Get selected train numbers for matching after re-search
        selected_numbers = set()
        for idx in old_train_indices:
            if idx < len(old_trains):
                selected_numbers.add(old_trains[idx]['train_number'])

        if self._on_reserve_callback:
            try:
                # Re-search
                result = self._on_reserve_callback(
                    action='search',
                    dep=dep,
                    arr=arr,
                    date=date,
                    time=time_str
                )

                if not result.get('success'):
                    self.send_message(f"❌ 재검색 실패: {result.get('message', '')}")
                    return

                new_trains = result.get('trains', [])
                if not new_trains:
                    self.send_message("😔 재검색 결과가 없습니다.")
                    return

                # Match previously selected trains in new results
                new_indices = []
                for i, t in enumerate(new_trains):
                    if t['train_number'] in selected_numbers:
                        new_indices.append(i)

                if not new_indices:
                    self.send_message(
                        "⚠️ 이전에 선택한 열차를 새 검색 결과에서 찾을 수 없습니다.\n"
                        "/reserve 명령어로 새로 시작해주세요."
                    )
                    return

                matched_names = ', '.join(
                    f"{new_trains[i]['train_name']}({new_trains[i]['dep_time'][:2]}:{new_trains[i]['dep_time'][2:4]})"
                    for i in new_indices
                )
                self.send_message(
                    f"✅ <b>열차 매칭 완료</b>\n"
                    f"🚄 대상: {matched_names}\n"
                    f"▶️ 매크로를 다시 시작합니다..."
                )

                # Start macro with new data
                start_result = self._on_reserve_callback(
                    action='start',
                    dep=dep,
                    arr=arr,
                    date=date,
                    time=time_str,
                    train_indices=new_indices,
                    trains=new_trains
                )

                if not start_result.get('success'):
                    self.send_message(f"❌ {start_result.get('message', '재시작 실패')}")

            except Exception as e:
                self.send_message(f"❌ 재시작 오류: {str(e)}")
        else:
            self.send_message(
                "⚠️ 콜백이 설정되지 않았습니다.\n"
                "웹에서 먼저 로그인 후 사용해주세요."
            )

    # ─── API Status ──────────────────────────────────────────────

    def get_status(self) -> dict:
        """Get current telegram service status."""
        return {
            'configured': self.is_configured,
            'connected': self.is_connected,
            'bot_token_set': bool(self.bot_token),
            'chat_id': self.chat_id or '',
            'polling': self._polling_active,
            'macro_running': self._macro_running,
            'provider': self._stored_provider or '',
            'macro_attempt': self._macro_attempt,
            'macro_info': self._macro_info,
            'macro_start_time': self._macro_start_time.isoformat() if self._macro_start_time else None,
            'has_logs': bool(self._macro_logs),
        }
