# -*- coding: utf-8 -*-
"""iPhone 앱 연결부 (mobile/ios/src/trainreservation): 작업 파일, 토큰, bridge, 서버 소켓 재개, 앱 배선.

toga·rubicon 은 리눅스에 없으므로 가짜 모듈로 대신한다. 실제 iOS 동작은 CI(ios.yml)의
시뮬레이터 스모크 테스트가 확인한다.
"""
import asyncio
import json
import os
import stat
import sys
import tempfile
import threading
import types
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from unittest import mock

ROOT = Path(__file__).resolve().parent.parent
IOS_SRC = ROOT / 'mobile' / 'ios' / 'src'
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(IOS_SRC))

from trainreservation.ios_bridge import (  # noqa: E402
    TOKEN_COOKIE, IOSBridge, JobStore, ServerHost, load_or_create_token,
)


class FakeNative:
    def __init__(self):
        self.calls = []

    def set_idle_timer_disabled(self, disabled):
        self.calls.append(('idle', disabled))

    def notify(self, kind, title, body):
        self.calls.append(('notify', kind, title, body))


def run_now(fn, *args):
    fn(*args)


class JobStoreTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.dir = Path(self.tmp.name)
        self.addCleanup(self.tmp.cleanup)

    def test_save_load_clear(self):
        store = JobStore(self.dir / 'job.json')
        self.assertIsNone(store.load())
        job = json.dumps({'user_id': 'u', 'password': '비밀', 'trains': []}, ensure_ascii=False)
        store.save(job)
        self.assertEqual(store.load(), job)
        self.assertEqual(stat.S_IMODE(os.stat(self.dir / 'job.json').st_mode), 0o600)
        store.clear()
        self.assertIsNone(store.load())
        store.clear()  # 없는 걸 지워도 괜찮다

    def test_overwrite_is_atomic_and_leaves_no_temp_files(self):
        store = JobStore(self.dir / 'job.json')
        store.save('{"n": 1}')
        store.save('{"n": 2}')
        self.assertEqual(json.loads(store.load()), {'n': 2})
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ['job.json'])

    def test_failed_write_keeps_previous_job(self):
        store = JobStore(self.dir / 'job.json')
        store.save('{"n": 1}')
        with mock.patch('os.replace', side_effect=OSError('disk full')):
            with self.assertRaises(OSError):
                store.save('{"n": 2}')
        self.assertEqual(json.loads(store.load()), {'n': 1})
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ['job.json'])

    def test_corrupt_file_is_dropped(self):
        path = self.dir / 'job.json'
        for bad in ('{"n": 1', '[1, 2]', ''):
            path.write_text(bad, encoding='utf-8')
            self.assertIsNone(JobStore(path).load())
            self.assertFalse(path.exists())

    def test_protection_hooks_called_and_failures_ignored(self):
        protected, excluded = [], []

        def protect(p):
            protected.append(Path(p).name)
            raise RuntimeError('no NSFileManager here')

        store = JobStore(self.dir / 'job.json', protect=protect, exclude_from_backup=excluded.append)
        store.save('{"a": 1}')
        self.assertEqual(json.loads(store.load()), {'a': 1})
        self.assertEqual(len(protected), 1)
        self.assertTrue(protected[0].endswith('.tmp'))  # 내용을 쓰기 전 임시 파일에 건다
        self.assertEqual(excluded, [self.dir / 'job.json'])

    def test_concurrent_saves(self):
        store = JobStore(self.dir / 'job.json')
        threads = [threading.Thread(target=store.save, args=(json.dumps({'n': i}),)) for i in range(20)]
        for t in threads:
            t.start()
        for t in threads:
            t.join()
        self.assertIn(json.loads(store.load())['n'], range(20))
        self.assertEqual(sorted(p.name for p in self.dir.iterdir()), ['job.json'])


class TokenTest(unittest.TestCase):
    def test_created_once_and_reused(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'app_token'
            first = load_or_create_token(path)
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(load_or_create_token(path), first)
            self.assertEqual(stat.S_IMODE(os.stat(path).st_mode), 0o600)

    def test_short_or_empty_token_is_replaced(self):
        with tempfile.TemporaryDirectory() as d:
            path = Path(d) / 'app_token'
            path.write_text('short', encoding='utf-8')
            token = load_or_create_token(path)
            self.assertNotEqual(token, 'short')
            self.assertEqual(path.read_text(encoding='utf-8'), token)


class IOSBridgeTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.store = JobStore(Path(self.tmp.name) / 'job.json')
        self.native = FakeNative()
        self.states = []
        self.bridge = IOSBridge(self.store, self.native, run_now,
                                on_state=lambda r, s: self.states.append((r, s)))

    def test_macro_state_toggles_idle_timer(self):
        self.bridge.onMacroState(True, 'KTX 101')
        self.assertTrue(self.bridge.macro_running)
        self.bridge.onMacroState(False, '')
        self.assertEqual(self.native.calls, [('idle', True), ('idle', False)])
        self.assertEqual(self.states, [(True, 'KTX 101'), (False, '')])

    def test_ui_work_is_scheduled_not_run_inline(self):
        queued = []
        bridge = IOSBridge(self.store, self.native, lambda fn, *a: queued.append((fn, a)))
        bridge.onMacroState(True, 'x')
        bridge.notifyEvent('reserved', '🎉 예약 성공', 'KTX')
        self.assertEqual(self.native.calls, [])  # 메인 스레드에서 돌 때까지 UIKit 을 안 만진다
        for fn, a in queued:
            fn(*a)
        self.assertEqual(self.native.calls, [('idle', True), ('notify', 'reserved', '🎉 예약 성공', 'KTX')])

    def test_native_failures_do_not_propagate(self):
        class Broken:
            def set_idle_timer_disabled(self, v):
                raise RuntimeError('boom')

            def notify(self, *a):
                raise RuntimeError('boom')

        bridge = IOSBridge(self.store, Broken(), run_now)
        with self.assertLogs('trainreservation.ios_bridge', 'ERROR'):
            bridge.onMacroState(True, 'x')
            bridge.notifyEvent('reserved', 't', 'b')

        def closed_loop(fn, *a):
            raise RuntimeError('Event loop is closed')

        bridge = IOSBridge(self.store, self.native, closed_loop)
        with self.assertLogs('trainreservation.ios_bridge', 'ERROR'):
            bridge.notifyEvent('reserved', 't', 'b')

    def test_save_and_clear_job(self):
        self.bridge.saveJob('{"user_id": "u"}')
        self.assertEqual(self.store.load(), '{"user_id": "u"}')
        self.bridge.clearJob()
        self.assertIsNone(self.store.load())

    def test_save_failure_is_logged_not_raised(self):
        with mock.patch.object(self.store, 'save', side_effect=OSError('read-only')):
            with self.assertLogs('trainreservation.ios_bridge', 'ERROR'):
                self.bridge.saveJob('{}')

    def test_server_ready_forwards_port(self):
        ports = []
        bridge = IOSBridge(self.store, self.native, run_now, on_server_ready=ports.append)
        bridge.onServerReady(54321)
        self.assertEqual(ports, [54321])
        IOSBridge(self.store, self.native, run_now).onServerReady(1)  # 콜백이 없어도 괜찮다


class IOSSupervisorTest(unittest.TestCase):
    """공통 런타임(mobile_runtime)이 부르는 방식 그대로: 매크로 시작 때 파일 저장, 정상 종료 때 삭제."""

    def setUp(self):
        sys.path.insert(0, str(ROOT / 'mobile' / 'shared'))
        self.addCleanup(sys.path.remove, str(ROOT / 'mobile' / 'shared'))
        import mobile_runtime
        import webui.routes.reservation as reservation
        from webui.services.telegram_service import TelegramService
        self.runtime, self.reservation = mobile_runtime, reservation
        self.addCleanup(setattr, reservation, 'run_reservation_loop', reservation.run_reservation_loop)
        self.addCleanup(mobile_runtime.set_bridge, mobile_runtime._platform['bridge'])
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.path = Path(self.tmp.name) / 'macro_job.json'
        self.bridge = IOSBridge(JobStore(self.path), FakeNative(), run_now)
        mobile_runtime.set_bridge(self.bridge)
        mobile_runtime._crash_times.clear()
        mobile_runtime._supervise_macros()
        self.tg = TelegramService.get_instance()
        self.tg.set_macro_state(False)

    def run_supervised(self, reason, seen):
        from core.base_service import SeatOption

        def run_reservation(*args, **kwargs):
            seen.append(json.loads(self.path.read_text(encoding='utf-8')))
            return reason

        service = mock.Mock()
        service.credentials = {'user_id': 'u', 'password': 'p'}
        with mock.patch.object(self.reservation, 'run_reservation', side_effect=run_reservation):
            self.tg.try_start_macro(owner='u')
            self.reservation.run_reservation_loop(service, 'korail', [{'dep_date': '29991231'}],
                                                  SeatOption.GENERAL_FIRST, None, owner='u')

    def test_job_file_exists_while_running_and_is_removed_after(self):
        from core.reservation import END_SUCCESS, END_STOPPED
        for reason in (END_SUCCESS, END_STOPPED):
            seen = []
            self.run_supervised(reason, seen)
            self.assertEqual(seen[0]['password'], 'p')
            self.assertEqual(seen[0]['trains'], [{'dep_date': '29991231'}])
            self.assertFalse(self.path.exists(), reason)

    def test_crash_keeps_job_for_restart(self):
        with mock.patch.object(self.runtime.threading, 'Timer'):
            self.run_supervised('crash', [])
        self.assertEqual(json.loads(JobStore(self.path).load())['user_id'], 'u')


class ServerHostTest(unittest.TestCase):
    """가짜 런타임이 werkzeug make_server 로 띄운 서버를 ServerHost 가 잡고, 소켓을 다시 여는지."""

    def setUp(self):
        import werkzeug.serving as serving
        self.serving = serving
        self.original_make_server = serving.make_server
        self.addCleanup(setattr, serving, 'make_server', self.original_make_server)
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)

    def make_runtime(self, token):
        serving = self.serving
        runtime = types.SimpleNamespace(bridge=None, resumed=[])

        def app(environ, start_response):
            ok = f'{TOKEN_COOKIE}={token}' in environ.get('HTTP_COOKIE', '')
            start_response('200 OK' if ok else '403 FORBIDDEN', [('Content-Type', 'text/plain')])
            return [b'ok' if ok else b'forbidden']

        def start(files_dir, port, tok, version, debug=False):
            runtime.started = (files_dir, port, tok, version, debug)
            from werkzeug.serving import make_server  # mobile_runtime 과 같은 방식
            server = make_server('127.0.0.1', int(port), app, threaded=True)
            runtime.server = server
            server.serve_forever()

        runtime.set_bridge = lambda b: setattr(runtime, 'bridge', b)
        runtime.resume_job = runtime.resumed.append
        runtime.start = start
        self.assertIs(serving.make_server, self.original_make_server)
        return runtime

    def test_start_health_and_reopen(self):
        token = 't' * 40
        runtime = self.make_runtime(token)
        host = ServerHost(runtime, self.tmp.name, 0, token, '1.2.3')  # 0 = 아무 빈 포트
        bridge = object()
        host.start(bridge, '{"user_id": "u"}')
        self.assertTrue(host.wait_until_up(10))
        self.assertIs(runtime.bridge, bridge)
        self.assertEqual(runtime.resumed, ['{"user_id": "u"}'])
        self.assertEqual(runtime.started, (self.tmp.name, 0, token, '1.2.3', False))
        first_port = host.port
        self.assertNotEqual(first_port, 0)  # 실제 포트를 읽었다
        self.assertTrue(host.auth_url.startswith(f'http://127.0.0.1:{first_port}/__auth?t='))

        # 토큰 없는 요청은 막힌다 (CI 스모크 테스트가 기대하는 403 과 같은 원리)
        with self.assertRaises(urllib.error.HTTPError) as ctx:
            urllib.request.urlopen(f'{host.base_url}/login', timeout=5)
        self.assertEqual(ctx.exception.code, 403)

        # iOS 가 듣는 소켓을 회수한 상황: 소켓을 닫아 버린다
        runtime.server.socket.close()
        self.assertFalse(host.is_up(timeout=1))
        self.assertTrue(host.reopen_listener())
        self.assertTrue(host.wait_until_up(10))
        host._server.shutdown()

    def test_no_job_means_no_resume(self):
        token = 'x' * 40
        runtime = self.make_runtime(token)
        host = ServerHost(runtime, self.tmp.name, 0, token, '1')
        self.assertFalse(host.reopen_listener())  # 아직 서버가 없다
        host.start(object(), None)
        self.assertTrue(host.wait_until_up(10))
        self.assertEqual(runtime.resumed, [])
        host._server.shutdown()

    def test_is_up_false_when_nothing_listens(self):
        host = ServerHost(types.SimpleNamespace(), self.tmp.name, 1, 'x' * 40, '1')
        self.assertFalse(host.is_up(timeout=0.5))
        self.assertFalse(host.wait_until_up(timeout=0.3, interval=0.1))


class AppWiringTest(unittest.TestCase):
    """app.py 가 toga/rubicon 없이도 뜻대로 배선되는지 (가짜 toga 로)."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        data = Path(self.tmp.name)

        class Widget:
            def __init__(self, *args, **kwargs):
                self.args, self.kwargs = args, kwargs
                self.text = args[0] if args else None
                self.url = kwargs.get('url')

        class MainWindow(Widget):
            def show(self):
                self.shown = True

        class App:
            def __init__(self, formal_name, app_id, app_name=None):
                self.formal_name, self.app_id = formal_name, app_id
                self.paths = types.SimpleNamespace(data=data)
                self.version = '9.9.9'
                self.scheduled = []
                self.loop = types.SimpleNamespace(
                    call_soon_threadsafe=lambda fn, *a: self.scheduled.append((fn, a)))

        fake_toga = types.ModuleType('toga')
        fake_toga.App = App
        fake_toga.MainWindow = MainWindow
        fake_toga.Label = fake_toga.Box = fake_toga.WebView = Widget
        fake_style = types.ModuleType('toga.style')
        fake_style.Pack = lambda **kw: kw
        self.native = types.ModuleType('trainreservation.ios_native')
        self.native.calls = []
        self.native.protect_file = lambda p: self.native.calls.append(('protect', Path(p).name))
        self.native.exclude_from_backup = lambda p: None
        self.native.setup_notifications = lambda: self.native.calls.append(('setup',))
        self.native.notify = lambda *a: self.native.calls.append(('notify',) + a)
        self.native.NativeAPI = types.SimpleNamespace(set_idle_timer_disabled=lambda v: None,
                                                      notify=self.native.notify)
        self.runtime = types.ModuleType('mobile_runtime')
        patcher = mock.patch.dict(sys.modules, {
            'toga': fake_toga, 'toga.style': fake_style,
            'trainreservation.ios_native': self.native, 'mobile_runtime': self.runtime,
        })
        patcher.start()
        self.addCleanup(patcher.stop)
        sys.modules.pop('trainreservation.app', None)
        self.addCleanup(sys.modules.pop, 'trainreservation.app', None)
        import trainreservation
        self.native_attr = mock.patch.object(trainreservation, 'ios_native', self.native, create=True)
        self.native_attr.start()
        self.addCleanup(self.native_attr.stop)
        import importlib
        self.app_module = importlib.import_module('trainreservation.app')  # 테스트마다 새 가짜 toga 로
        self.addCleanup(lambda: trainreservation.__dict__.pop('app', None))
        self.data = data

    def make_app(self):
        app = self.app_module.main()
        self.assertEqual(app.formal_name, '열차예약')
        self.assertEqual(app.app_id, 'com.ohjn96.trainreservation')
        return app

    def test_startup_without_saved_job(self):
        app = self.make_app()
        with mock.patch.object(ServerHost, 'start') as start:
            app.startup()
        start.assert_called_once_with(app.bridge, None)
        self.assertEqual(app.host.port, self.app_module.PORT)
        self.assertEqual(app.host.version, '9.9.9')
        self.assertIn(('setup',), self.native.calls)
        self.assertTrue((self.data / 'app_token').exists())
        self.assertTrue(app.main_window.shown)

        # 서버가 뜨면 WebView 가 /__auth?t=토큰 을 연다
        with mock.patch.object(ServerHost, 'wait_until_up', return_value=True):
            asyncio.run(app.on_running())
        self.assertIs(app.main_window.content, app.webview)
        self.assertEqual(app.webview.url, app.host.auth_url)

    def test_startup_resumes_saved_job(self):
        (self.data / 'macro_job.json').write_text('{"user_id": "u"}', encoding='utf-8')
        app = self.make_app()
        with mock.patch.object(ServerHost, 'start') as start:
            app.startup()
        start.assert_called_once_with(app.bridge, '{"user_id": "u"}')
        self.assertIn('이어서', app.status.text)

    def test_server_never_up_shows_message(self):
        app = self.make_app()
        with mock.patch.object(ServerHost, 'start'):
            app.startup()
        with mock.patch.object(ServerHost, 'wait_until_up', return_value=False):
            asyncio.run(app.on_running())
        self.assertIsNone(app.webview)
        self.assertIn('시작하지 못했어요', app.status.text)

    def test_macro_state_goes_through_main_loop(self):
        app = self.make_app()
        with mock.patch.object(ServerHost, 'start'):
            app.startup()
        app.bridge.onMacroState(True, 'KTX 101')
        for fn, a in app.scheduled:
            fn(*a)
        self.assertIn('실행 중', app.main_window.title)

    def test_background_notice_only_while_macro_runs(self):
        app = self.make_app()
        with mock.patch.object(ServerHost, 'start'):
            app.startup()
        app._on_background(app.main_window)
        self.assertFalse([c for c in self.native.calls if c[0] == 'notify'])
        app.bridge.macro_running = True
        app._on_background(app.main_window)
        self.assertEqual([c[1] for c in self.native.calls if c[0] == 'notify'], ['background'])

    def test_foreground_reopens_dead_listener_and_reloads(self):
        app = self.make_app()
        with mock.patch.object(ServerHost, 'start'):
            app.startup()
        app._on_foreground(app.main_window)  # WebView 가 뜨기 전엔 아무것도 안 한다
        app.webview = types.SimpleNamespace(url=None)
        done = threading.Event()
        with mock.patch.object(ServerHost, 'is_up', return_value=False), \
                mock.patch.object(ServerHost, 'reopen_listener', return_value=True), \
                mock.patch.object(ServerHost, 'wait_until_up', side_effect=lambda **kw: done.set() or True):
            app._on_foreground(app.main_window)
            self.assertTrue(done.wait(5))
            for _ in range(50):
                if app.scheduled:
                    break
                threading.Event().wait(0.02)
        for fn, a in app.scheduled:
            fn(*a)
        self.assertEqual(app.webview.url, app.host.auth_url)


if __name__ == '__main__':
    unittest.main()
