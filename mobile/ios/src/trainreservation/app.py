# -*- coding: utf-8 -*-
"""iPhone 앱 입구 (BeeWare Toga).

안드로이드와 같은 구조: 공통 런타임(mobile_runtime)이 공통 웹 화면(webui/)을 폰 안
127.0.0.1 의 무작위 포트에 띄우고, 이 앱은 그걸 WebView 로 보여준다. 코레일 호출은 이 폰에서 나간다.

토큰은 그 포트의 서버가 /__hello 로 우리 서버임을 증명한 뒤에만 보내고(ServerHost.verify),
WebView 는 확인한 http://127.0.0.1:<포트> 만 연다. 바깥 링크는 사파리로 넘긴다.

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

from .ios_bridge import IOSBridge, JobStore, ServerHost, is_external_web_url, is_own_url, load_or_create_token

logger = logging.getLogger(__name__)

#: 앱 안 서버 포트. 0 = 운영체제가 빈 포트를 고른다 (고정 포트는 다른 앱이 먼저 차지하고
#: 우리 행세를 할 수 있다). 실제 포트는 ServerHost 가 받아 /__hello 로 확인한다.
PORT = 0
#: 확인을 마친 포트를 적어 두는 파일 (앱 데이터 폴더 안). CI 스모크 테스트가 읽는다. 비밀 아님.
PORT_FILE = 'server_port'
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
        self.host = ServerHost(mobile_runtime, data_dir, PORT, token, str(self.version or '0'),
                               port_file=data_dir / PORT_FILE)
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
        self.webview = toga.WebView(url=self.host.auth_url, style=Pack(flex=1),
                                    on_navigation_starting=self._on_navigate)
        _drop_navigation_cleanup(self.webview, self._on_navigate)
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
        """소켓을 다시 열었다 (포트가 바뀌었을 수 있다). 다시 확인된 주소로 새로 연다."""
        if self.webview is not None and self.host.verified_port:
            self.webview.url = self.host.auth_url

    def _on_navigate(self, widget, url, **kwargs):
        """WebView 안에서는 확인한 우리 서버만. 바깥 http(s) 링크는 사파리로, 나머지는 막는다."""
        if url == 'about:blank' or is_own_url(url, self.host.verified_port):
            return True
        if is_external_web_url(url):
            _try(self.native.open_external, url)
        else:
            logger.warning('blocked navigation: %s', url.split('?', 1)[0])
        return False


def _drop_navigation_cleanup(webview, handler):
    """toga 0.5.6 은 on_navigation_starting 이 True 를 돌려주면 cleanup 에서 `webview.url = url` 로
    그 주소를 다시 연다. 그러면 로그인 같은 POST 폼 제출이 GET 으로 바뀌어 깨진다.
    허용/차단 판단만 쓰도록 cleanup 없는 handler 로 바꿔 끼운다 (toga 버전을 올리면 다시 확인).
    """
    from toga.handlers import wrapped_handler
    webview._on_navigation_starting = wrapped_handler(webview, handler)


def _try(fn, *args):
    try:
        fn(*args)
    except Exception:  # noqa: BLE001 - 보호·알림 같은 부가 기능 실패로 앱을 멈추지 않는다
        logger.warning('%s failed', getattr(fn, '__name__', fn), exc_info=True)


def main():
    return TrainReservationApp(DISPLAY_NAME, 'com.ohjn96.trainreservation', app_name='trainreservation')
