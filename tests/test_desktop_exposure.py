# -*- coding: utf-8 -*-
"""PC 앱이 LAN·다른 사이트에 노출될 때의 방어: 텔레그램 API 로그인, Host 허용 목록,
기본 바인딩/디버그, 텔레그램 채팅 페어링 코드."""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from webui.services.telegram_service import TelegramService
from webui.utils import session_helper
from test_server import sign_in


def make_app():
    from webui import create_app
    return create_app(server_mode=False)


class TelegramApiNeedsLoginTest(unittest.TestCase):
    def setUp(self):
        session_helper._vault.clear()
        self.app = make_app()
        self.tg = TelegramService.get_instance()
        self.saved = (self.tg.bot_token, self.tg.chat_id)

    def tearDown(self):
        self.tg.bot_token, self.tg.chat_id = self.saved
        self.tg._pairing = None
        session_helper._vault.clear()

    def test_anonymous_cannot_change_bot(self):
        client = self.app.test_client()
        with mock.patch.object(TelegramService, 'configure') as configure, \
                mock.patch.object(TelegramService, 'disconnect') as disconnect:
            for path in ('/api/telegram/configure', '/api/telegram/disconnect', '/api/telegram/test'):
                resp = client.post(path, json={'bot_token': '1:x', 'chat_id': '9'})
                self.assertEqual(resp.status_code, 401, path)
            configure.assert_not_called()
            disconnect.assert_not_called()
        self.assertEqual(client.get('/api/telegram/pairing').status_code, 401)

    def test_status_stays_open_but_hides_chat_id(self):
        self.tg.bot_token, self.tg.chat_id = '1:x', '424242'
        anon = self.app.test_client().get('/api/telegram/status')
        self.assertEqual(anon.status_code, 200)
        self.assertEqual(anon.get_json()['chat_id'], '')
        client = self.app.test_client()
        sign_in(client, 'me')
        self.assertEqual(client.get('/api/telegram/status').get_json()['chat_id'], '424242')

    def test_logged_in_user_can_disconnect(self):
        client = self.app.test_client()
        sign_in(client, 'me')
        with mock.patch.object(TelegramService, 'disconnect'), \
                mock.patch('webui.routes.telegram.clear_saved_settings'):
            self.assertTrue(client.post('/api/telegram/disconnect').get_json()['success'])

    def test_pairing_endpoint(self):
        client = self.app.test_client()
        sign_in(client, 'me')
        self.tg.bot_token, self.tg.chat_id = None, None
        self.assertFalse(client.get('/api/telegram/pairing').get_json()['success'])
        self.tg.bot_token = '1:x'
        first = client.get('/api/telegram/pairing').get_json()
        self.assertTrue(first['success'])
        self.assertFalse(first['paired'])
        self.assertGreaterEqual(len(first['code']), 6)
        self.assertEqual(first['command'], f"/start {first['code']}")
        self.assertLessEqual(first['expires_in'], 600)
        # 유효한 동안은 같은 코드
        self.assertEqual(client.get('/api/telegram/pairing').get_json()['code'], first['code'])
        self.tg.chat_id = '5'
        self.assertEqual(client.get('/api/telegram/pairing').get_json(),
                         {'success': True, 'paired': True})


def update(text, chat_id='777'):
    return {'update_id': 1, 'message': {'text': text, 'chat': {'id': chat_id},
                                        'from': {'first_name': 'x'}}}


class PairingCodeTest(unittest.TestCase):
    def setUp(self):
        self.tg = TelegramService()
        self.tg.bot_token = '1:x'
        self.sent = []
        self.tg._api_call = lambda method, data=None: self.sent.append((method, data)) or {}
        self.tg.send_message = lambda text, parse_mode='HTML': self.sent.append(('me', text)) or True

    def test_bare_start_does_not_register(self):
        self.tg.new_pairing_code()
        self.tg._handle_update(update('/start'))
        self.assertIsNone(self.tg.chat_id)
        self.assertEqual(self.sent[0][0], 'sendMessage')
        self.assertEqual(self.sent[0][1]['chat_id'], '777')

    def test_without_any_code_nobody_can_claim(self):
        self.tg._handle_update(update('/start ABCDEFGH'))
        self.assertIsNone(self.tg.chat_id)

    def test_wrong_code_does_not_register(self):
        self.tg.new_pairing_code()
        self.tg._handle_update(update('/start WRONG123'))
        self.assertIsNone(self.tg.chat_id)

    def test_right_code_registers_once(self):
        code = self.tg.new_pairing_code()['code']
        self.tg._handle_update(update(f'/start {code.lower()}'))
        self.assertEqual(self.tg.chat_id, '777')
        self.assertIsNone(self.tg.pairing_info(), '한 번 쓰면 사라진다')
        # 같은 코드로 다른 채팅이 뺏어 갈 수 없다
        self.tg.chat_id = None
        self.tg._handle_update(update(f'/start {code}', chat_id='666'))
        self.assertIsNone(self.tg.chat_id)

    def test_code_expires(self):
        with mock.patch('webui.services.telegram_service.time.monotonic', return_value=1000.0):
            code = self.tg.new_pairing_code()['code']
        with mock.patch('webui.services.telegram_service.time.monotonic', return_value=1601.0):
            self.tg._handle_update(update(f'/start {code}'))
            self.assertIsNone(self.tg.pairing_info())
        self.assertIsNone(self.tg.chat_id)

    def test_start_with_botname_suffix(self):
        code = self.tg.new_pairing_code()['code']
        self.tg._handle_update(update(f'/start@my_bot {code}'))
        self.assertEqual(self.tg.chat_id, '777')

    def test_explicit_chat_id_still_works(self):
        with mock.patch('webui.services.telegram_service.requests.get') as get:
            get.return_value.json.return_value = {'ok': True, 'result': {'username': 'b'}}
            self.assertTrue(self.tg.configure('1:x', '4242')['success'])
        self.assertEqual(self.tg.chat_id, '4242')

    def test_code_is_random_and_long(self):
        codes = {self.tg.new_pairing_code()['code'] for _ in range(20)}
        self.assertEqual(len(codes), 20)
        self.assertTrue(all(len(c) >= 6 for c in codes))


class HostAllowlistTest(unittest.TestCase):
    def get(self, host, env=None):
        env = env or {}
        with mock.patch.dict(os.environ, env):
            for key in ('ALLOWED_HOSTS', 'HOST'):
                if key not in env:
                    os.environ.pop(key, None)
            return make_app().test_client().get('/login', headers={'Host': host})

    def test_local_names_pass(self):
        for host in ('localhost', 'localhost:5050', '127.0.0.1:17650', '[::1]:5050',
                     '192.168.0.10:5050', '100.101.102.103'):
            self.assertNotEqual(self.get(host).status_code, 400, host)

    def test_rebinding_domain_is_refused(self):
        for host in ('evil.example', 'evil.example:5050', '127.0.0.1.evil.example', ''):
            self.assertEqual(self.get(host).status_code, 400, host)

    def test_allowed_hosts_env(self):
        env = {'ALLOWED_HOSTS': 'box.tail1234.ts.net, .home.arpa'}
        self.assertNotEqual(self.get('box.tail1234.ts.net', env).status_code, 400)
        self.assertNotEqual(self.get('pc.home.arpa:5050', env).status_code, 400)
        self.assertEqual(self.get('other.ts.net', env).status_code, 400)

    def test_bind_host_is_allowed(self):
        self.assertNotEqual(self.get('mypc.lan:5050', {'HOST': 'mypc.lan'}).status_code, 400)

    def test_star_disables_check(self):
        self.assertNotEqual(self.get('anything.example', {'ALLOWED_HOSTS': '*'}).status_code, 400)


class DesktopDefaultsTest(unittest.TestCase):
    def run_web(self, env):
        import desktop.main as desktop_main
        # 실제 포트(5050)는 건드리지 않는다: 포트 고르기와 서버 실행은 가짜로
        choice = desktop_main.PortChoice(5050, mock.Mock())
        with mock.patch.dict(os.environ, env, clear=False), \
                mock.patch.object(desktop_main, 'choose_port', return_value=choice), \
                mock.patch.object(desktop_main, 'serve') as serve, \
                mock.patch.object(desktop_main, 'cleanup_cache'), \
                mock.patch.object(desktop_main, 'open_browser'):
            for key in ('HOST', 'FLASK_DEBUG', 'WERKZEUG_RUN_MAIN'):
                if key not in env:
                    os.environ.pop(key, None)
            desktop_main.run_web()
        host, _port, debug, _sock = serve.call_args.args
        return {'host': host, 'debug': debug}

    def test_defaults_are_local_and_no_debug(self):
        kwargs = self.run_web({})
        self.assertEqual(kwargs['host'], '127.0.0.1')
        self.assertFalse(kwargs['debug'])

    def test_opt_in(self):
        kwargs = self.run_web({'HOST': '0.0.0.0', 'FLASK_DEBUG': 'true'})
        self.assertEqual(kwargs['host'], '0.0.0.0')
        self.assertTrue(kwargs['debug'])


class StartReservationCsrfTest(unittest.TestCase):
    """예약 시작은 POST + 같은 사이트에서만. 다른 사이트의 <img>·폼으로는 못 켠다."""

    def setUp(self):
        import webui.routes.reservation as reservation
        self.reservation = reservation
        session_helper._vault.clear()
        self.tg = TelegramService.get_instance()
        self.tg.set_macro_state(False)
        self.started = []
        self.patch = mock.patch.object(reservation, 'run_reservation_loop',
                                       lambda *a, **k: (self.started.append(1),
                                                        self.tg.set_macro_state(False)))
        self.patch.start()
        from webui.services import ServiceManager
        from webui.services.korail_service import KorailService

        def fake_login(svc, user_id, password):
            svc._user_id, svc._password = user_id, password
            svc._client = type('C', (), {'logined': True})()
            return True

        self.login_patches = [
            mock.patch.object(KorailService, 'login', fake_login),
            mock.patch.object(KorailService, 'is_logged_in', lambda svc: svc._client is not None),
        ]
        [p.start() for p in self.login_patches]
        ServiceManager._services.clear()
        self.client = make_app().test_client()
        sign_in(self.client, 'me')

    def tearDown(self):
        self.patch.stop()
        [p.stop() for p in self.login_patches]
        from webui.services import ServiceManager
        ServiceManager._services.clear()
        self.reservation.STOP_MACRO = True
        self.tg.set_macro_state(False)
        self.tg._macro_owner = None
        session_helper._vault.clear()

    def test_get_is_not_allowed(self):
        self.assertEqual(self.client.get('/start_reservation').status_code, 405)

    def test_cross_site_is_refused(self):
        for headers in ({'Sec-Fetch-Site': 'cross-site'}, {'Sec-Fetch-Site': 'same-site'},
                        {'Origin': 'http://evil.example'}):
            resp = self.client.post('/start_reservation', headers=headers)
            self.assertEqual(resp.status_code, 403, headers)
        self.assertEqual(self.started, [])

    def test_same_origin_is_accepted(self):
        for headers in ({'Sec-Fetch-Site': 'same-origin'},
                        {'Origin': 'http://localhost'}, {}):
            resp = self.client.post('/start_reservation', headers=headers)
            self.assertTrue(resp.get_json()['success'], (headers, resp.get_json()))
            for _ in range(100):
                if not self.tg._macro_running:
                    break
                threading.Event().wait(0.01)


class SecurityHeadersTest(unittest.TestCase):
    def test_every_response_has_headers(self):
        client = make_app().test_client()
        for path in ('/login', '/manifest.webmanifest', '/sw.js', '/api/telegram/status', '/nope'):
            resp = client.get(path)
            self.assertEqual(resp.headers.get('Referrer-Policy'), 'same-origin', path)
            self.assertEqual(resp.headers.get('X-Content-Type-Options'), 'nosniff', path)
            csp = resp.headers.get('Content-Security-Policy', '')
            self.assertIn("connect-src 'self'", csp, path)
            self.assertIn("frame-ancestors 'none'", csp, path)
            self.assertNotIn('unsafe-eval', csp)


if __name__ == '__main__':
    unittest.main(verbosity=2)
