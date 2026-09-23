# -*- coding: utf-8 -*-
"""iPhone 앱 입구 (BeeWare Toga).

안드로이드와 같은 구조: 공통 런타임(mobile_runtime)이 공통 웹 화면(webui/)을 폰 안
127.0.0.1(PORT) 에 띄우고, 이 앱은 그걸 WebView 로 보여준다. 코레일 호출은 이 폰에서 나간다.

iPhone 의 한계: 앱이 화면에 떠 있는 동안에만 돈다 (iOS 는 뒤로 간 앱을 몇 초 안에 멈춘다).
그래서 매크로가 도는 동안엔 화면 자동 잠금을 끈다 (IOSBridge.onMacroState).
앱을 뒤로 보내면 "매크로가 멈춰요" 알림을 띄워 다시 열도록 안내한다.
"""
import asyncio
import logging
import threading
from pathlib import Path

import toga
from toga.style import Pack

from .ios_bridge import IOSBridge, JobStore, ServerHost, load_or_create_token

logger = logging.getLogger(__name__)

#: 앱 안 서버 포트 (안드로이드 ServerService.PORT 와 같다). 여기 한 곳에만 둔다.
#: 런타임이 무작위 포트(0)를 지원하게 되면 0 으로 바꾸면 된다: ServerHost 가 실제 포트를 읽는다.
PORT = 17650
DISPLAY_NAME = '열차예약'


class TrainReservationApp(toga.App):
    def startup(self):
        import mobile_runtime

        from . import ios_native as native

        self.native = native
        data_dir = Path(self.paths.data)
        data_dir.mkdir(parents=True, exist_ok=True)
        # 폴더 전체엔 NSFileProtectionComplete 를 걸지 않는다: 폰을 잠근 직후 몇 초 동안
        # 서버가 설정 파일을 쓰다 실패할 수 있다. 폴더는 iOS 기본값(첫 잠금 해제 후 읽기 가능),
        # 비밀번호·카드가 담긴 작업 파일과 토큰만 Complete 로 둔다.
        store = JobStore(data_dir / 'macro_job.json',
                         protect=native.protect_file,
                         exclude_from_backup=native.exclude_from_backup)
        token = load_or_create_token(data_dir / 'app_token', protect=native.protect_file)
        self.host = ServerHost(mobile_runtime, data_dir, PORT, token, str(self.version or '0'))
        self.bridge = IOSBridge(store, native.NativeAPI, self._call_on_main,
                                on_state=self._show_state, on_server_ready=self.host.set_port)
        self.webview = None
        self._checking = threading.Lock()

        self.status = toga.Label('열차 예약을 준비하는 중…', style=Pack(margin=24))
        self.main_window = toga.MainWindow(title=DISPLAY_NAME)
        self.main_window.content = toga.Box(children=[self.status])
        self.main_window.on_gain_focus = self._on_foreground
        self.main_window.on_hide = self._on_background
        self.main_window.show()

        native.setup_notifications()
        job = store.load()
        self.host.start(self.bridge, job)
        if job:
            self.status.text = '하던 예약 매크로를 이어서 돌립니다…'

    async def on_running(self):
        up = await asyncio.get_running_loop().run_in_executor(None, self.host.wait_until_up)
        if not up:
            self.status.text = '앱 안의 서버를 시작하지 못했어요. 앱을 완전히 닫았다가 다시 열어 주세요.'
            return
        self.webview = toga.WebView(url=self.host.auth_url, style=Pack(flex=1))
        self.main_window.content = self.webview

    # ── 메인 스레드

    def _call_on_main(self, fn, *args):
        self.loop.call_soon_threadsafe(fn, *args)

    def _show_state(self, running, summary):
        self.main_window.title = f'{DISPLAY_NAME} · 매크로 실행 중' if running else DISPLAY_NAME

    def _on_background(self, window, **kwargs):
        """앱이 뒤로 갔다. iOS 가 곧 앱을 멈추므로 매크로도 멈춘다고 알린다."""
        if self.bridge.macro_running:
            _try(self.native.notify, 'background',
                 '⏸ 예약 매크로가 멈춰요',
                 '아이폰은 앱이 화면에 떠 있을 때만 매크로가 돌아요. 앱을 다시 열어 주세요.')

    def _on_foreground(self, window, **kwargs):
        """다시 앞으로 왔다. 멈춰 있는 동안 iOS 가 서버 소켓을 회수했으면 다시 연다."""
        if self.webview is None or not self._checking.acquire(blocking=False):
            return  # 아직 첫 화면 준비 중이거나 이미 확인 중

        def check():
            try:
                if self.host.is_up(timeout=2) or self.host.is_up(timeout=3):
                    return
                if self.host.reopen_listener() and self.host.wait_until_up(timeout=10):
                    self._call_on_main(self._reload)
            except Exception:  # noqa: BLE001
                logger.exception('foreground check failed')
            finally:
                self._checking.release()

        threading.Thread(target=check, daemon=True, name='foreground-check').start()

    def _reload(self):
        if self.webview is not None:
            self.webview.url = self.host.auth_url


def _try(fn, *args):
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - 보호·알림 같은 부가 기능 실패로 앱을 멈추지 않는다
        logger.warning('%s failed', getattr(fn, '__name__', fn), exc_info=True)


def main():
    return TrainReservationApp(DISPLAY_NAME, 'com.ohjn96.trainreservation', app_name='trainreservation')
