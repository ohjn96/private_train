# -*- coding: utf-8 -*-
"""PC 앱 기동: 포트 고르기, 이미 떠 있는 앱 알아보기, 브라우저는 서버가 뜬 뒤에.

실제 소켓을 127.0.0.1 의 임의 포트에 연다 (5050 같은 고정 포트는 건드리지 않는다).
"""
import os
import socket
import sys
import threading
import unittest
import urllib.request
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import desktop.main as desktop_main
from desktop.main import (
    APP_NAME, NoFreePort, PortChoice, bind_socket, choose_port, identify, is_same_app,
)
from webui.version import __version__

HOST = '127.0.0.1'


def free_block(size: int) -> int:
    """연속으로 비어 있는 size 개 포트의 시작 번호."""
    for _ in range(50):
        with socket.socket() as s:
            s.bind((HOST, 0))
            start = s.getsockname()[1]
        if start + size >= 65535:
            continue
        ok = True
        for port in range(start, start + size):
            try:
                with socket.socket() as s:
                    s.bind((HOST, port))
            except OSError:
                ok = False
                break
        if ok:
            return start
    raise unittest.SkipTest('연속된 빈 포트를 찾지 못함')


def occupy(port: int) -> socket.socket:
    """다른 프로그램 흉내: SO_REUSEADDR 없이 바인딩 + listen."""
    s = socket.socket()
    s.bind((HOST, port))
    s.listen(1)
    return s


class ChoosePortTest(unittest.TestCase):
    def setUp(self):
        self.base = free_block(4)
        self.held = []

    def tearDown(self):
        for s in self.held:
            s.close()

    def hold(self, sock):
        self.held.append(sock)
        return sock

    def test_free_preferred_port_is_bound(self):
        probe = mock.Mock()
        choice = choose_port(HOST, self.base, fallbacks=3, probe=probe)
        self.hold(choice.sock)
        self.assertEqual(choice.port, self.base)
        self.assertEqual(choice.sock.getsockname()[1], self.base)
        probe.assert_not_called()
        # 잡아 둔 소켓이 listen 중이라 곧바로 접속된다 (브라우저를 열어도 안전)
        with socket.create_connection((HOST, self.base), timeout=2):
            pass

    def test_taken_by_other_program_moves_to_next_free(self):
        self.hold(occupy(self.base))
        self.hold(occupy(self.base + 1))
        choice = choose_port(HOST, self.base, fallbacks=3, probe=lambda h, p: None)
        self.hold(choice.sock)
        self.assertEqual(choice.port, self.base + 2)

    def test_same_app_already_running_is_reused(self):
        self.hold(occupy(self.base))
        running = f'{APP_NAME}/{__version__}'
        choice = choose_port(HOST, self.base, fallbacks=3, probe=lambda h, p: running)
        self.assertEqual(choice, PortChoice(self.base, None, running))

    def test_older_major_version_is_not_reused(self):
        self.hold(occupy(self.base))
        choice = choose_port(HOST, self.base, fallbacks=3, probe=lambda h, p: f'{APP_NAME}/2.3.3')
        self.hold(choice.sock)
        self.assertEqual(choice.port, self.base + 1)

    def test_all_taken_raises(self):
        for port in range(self.base, self.base + 3):
            self.hold(occupy(port))
        with self.assertRaises(NoFreePort):
            choose_port(HOST, self.base, fallbacks=2, probe=lambda h, p: None)

    def test_bound_socket_is_exclusive(self):
        self.hold(bind_socket(HOST, self.base))
        with self.assertRaises(OSError):
            bind_socket(HOST, self.base).close()

    @unittest.skipUnless(os.name == 'nt', 'Windows 전용')
    def test_windows_uses_exclusive_addr(self):
        sock = self.hold(bind_socket(HOST, self.base))
        self.assertTrue(sock.getsockopt(socket.SOL_SOCKET, socket.SO_EXCLUSIVEADDRUSE))
        self.assertFalse(desktop_main.ExclusiveWSGIServer.allow_reuse_address)


class IsSameAppTest(unittest.TestCase):
    def test_values(self):
        major = __version__.split('.')[0]
        self.assertTrue(is_same_app(f'{APP_NAME}/{major}.9.9'))
        self.assertFalse(is_same_app(f'{APP_NAME}/0.1'))
        self.assertFalse(is_same_app(f'Other/{__version__}'))
        self.assertFalse(is_same_app(None))
        self.assertFalse(is_same_app('garbage'))


class ServerIdentityTest(unittest.TestCase):
    """실제 서버를 미리 잡은 소켓으로 띄워 /__whoami 와 헤더로 알아본다."""

    def setUp(self):
        self.sock = bind_socket(HOST, 0)
        self.port = self.sock.getsockname()[1]
        self.server = desktop_main.make_app_server(HOST, self.sock)
        self.thread = threading.Thread(target=self.server.serve_forever, daemon=True)
        self.thread.start()

    def tearDown(self):
        self.server.shutdown()
        self.thread.join(5)

    def test_identify_running_app(self):
        whoami = identify(HOST, self.port)
        self.assertEqual(whoami, f'{APP_NAME}/{__version__}')
        self.assertTrue(is_same_app(whoami))

    def test_header_on_every_response(self):
        with urllib.request.urlopen(f'http://{HOST}:{self.port}/__whoami', timeout=5) as resp:
            self.assertEqual(resp.headers[desktop_main.WHOAMI_HEADER], f'{APP_NAME}/{__version__}')

    def test_identify_nothing_listening(self):
        port = free_block(1)
        self.assertIsNone(identify(HOST, port, timeout=1))

    def test_identify_other_program(self):
        other = occupy(free_block(1))
        port = other.getsockname()[1]

        def answer():
            conn, _ = other.accept()
            conn.recv(1024)
            conn.sendall(b'HTTP/1.0 200 OK\r\nContent-Length: 2\r\n\r\nhi')
            conn.close()
        t = threading.Thread(target=answer, daemon=True)
        t.start()
        try:
            self.assertIsNone(identify(HOST, port, timeout=2))
        finally:
            t.join(3)
            other.close()


class RunWebTest(unittest.TestCase):
    """브라우저는 서버 소켓이 listen 한 뒤에만, 포트를 못 잡으면 안 연다."""

    def run_web(self, choice=None, error=None):
        env = {'NO_BROWSER': '', 'FLASK_DEBUG': 'false', 'HOST': HOST, 'PORT': '6000'}
        choose = mock.Mock(return_value=choice, side_effect=error)
        with mock.patch.dict(os.environ, env), \
                mock.patch.object(desktop_main, 'choose_port', choose), \
                mock.patch.object(desktop_main, 'serve') as serve, \
                mock.patch.object(desktop_main, 'open_browser') as open_browser, \
                mock.patch.object(desktop_main, 'cleanup_cache'), \
                mock.patch.object(desktop_main, 'pause_before_exit') as pause, \
                mock.patch('builtins.print'):
            try:
                desktop_main.run_web()
                code = 0
            except SystemExit as e:
                code = e.code
        return code, serve, open_browser, pause

    def test_existing_instance_only_opens_browser(self):
        code, serve, open_browser, _ = self.run_web(PortChoice(6000, None, f'{APP_NAME}/{__version__}'))
        self.assertEqual(code, 0)
        serve.assert_not_called()
        open_browser.assert_called_once_with('http://localhost:6000')

    def test_no_port_fails_without_browser(self):
        code, serve, open_browser, pause = self.run_web(error=NoFreePort('x'))
        self.assertEqual(code, 1)
        serve.assert_not_called()
        open_browser.assert_not_called()
        pause.assert_called_once()

    def test_fallback_port_is_served(self):
        sock = mock.Mock()
        code, serve, open_browser, _ = self.run_web(PortChoice(6003, sock))
        self.assertEqual(code, 0)
        serve.assert_called_once_with(HOST, 6003, False, sock)
        open_browser.assert_not_called()  # serve 가 서버를 만든 뒤에 연다

    def test_serve_opens_browser_after_server_is_listening(self):
        events = []
        server = mock.Mock()
        server.serve_forever.side_effect = lambda: events.append('serve')
        with mock.patch.object(desktop_main, 'make_app_server',
                               side_effect=lambda h, s: (events.append('bind'), server)[1]), \
                mock.patch.object(desktop_main, 'open_browser',
                                  side_effect=lambda url, **k: events.append(url)):
            desktop_main.serve(HOST, 6001, False, mock.Mock())
        self.assertEqual(events, ['bind', 'http://localhost:6001', 'serve'])


class PauseBeforeExitTest(unittest.TestCase):
    def test_only_when_frozen_and_tty(self):
        stdin = mock.Mock(isatty=mock.Mock(return_value=True))
        with mock.patch('builtins.input') as ask, mock.patch.object(sys, 'stdin', stdin):
            desktop_main.pause_before_exit()
            ask.assert_not_called()
            with mock.patch.object(sys, 'frozen', True, create=True):
                desktop_main.pause_before_exit()
            ask.assert_called_once_with('엔터를 누르면 닫혀요')


class LoginKeepsUserIdTest(unittest.TestCase):
    def test_failed_login_keeps_id(self):
        from webui import create_app
        from webui.services.service_manager import ServiceManager
        app = create_app(server_mode=False)
        with mock.patch.object(ServiceManager, 'login', return_value='틀렸어요'):
            resp = app.test_client().post('/login', data={'user_id': 'me@example.com', 'password': 'x'},
                                          headers={'Origin': 'http://localhost'})
        body = resp.get_data(as_text=True)
        self.assertIn('value="me@example.com"', body)


class BuildConfigTest(unittest.TestCase):
    def setUp(self):
        sys.path.insert(0, os.path.join(os.path.dirname(os.path.dirname(os.path.abspath(__file__))),
                                        'desktop', 'build'))
        import build
        self.build = build

    def test_tailwind_source_is_not_bundled(self):
        data = self.build.static_data()
        self.assertTrue(data)
        self.assertFalse(any('input.css' in d for d in data))
        self.assertTrue(any(d.endswith('webui/static/vendor') for d in data))

    def test_dead_ktx_target_is_gone(self):
        self.assertEqual(list(self.build.BUILD_CONFIG), ['unified'])


if __name__ == '__main__':
    unittest.main()
