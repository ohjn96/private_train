# -*- coding: utf-8 -*-
"""웹 보안 점검에서 나온 문제들: 앱 전체 CSRF 검사, 로그의 봇 토큰, 텔레그램 봇 가로채기,
오류 문구 노출, 잘못된 JSON 타입으로 500, 코레일 로그인 무차별 대입."""
import io
import logging
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui.routes.reservation as reservation
from webui.services import ServiceManager
from webui.services import telegram_service
from webui.services.telegram_service import TelegramService, redact_token
from webui.utils import session_helper
from test_server import sign_in

TOKEN = '123456789:AAHdqTcvCH1vGWJxfSeofSAs0K5PALDsaw'


def make_app():
    from webui import create_app
    return create_app(server_mode=False)


class CsrfEverywhereTest(unittest.TestCase):
    """POST 등 바뀌는 요청은 어느 경로든 다른 사이트에서 오면 403."""

    PATHS = ['/login', '/logout', '/stop_macro', '/start_reservation', '/reserve_select', '/',
             '/api/search_more', '/api/card/save', '/api/card/clear',
             '/api/telegram/configure', '/api/telegram/disconnect', '/api/telegram/test']

    def setUp(self):
        session_helper._vault.clear()
        self.client = make_app().test_client()
        sign_in(self.client, 'me')
        self.stop = reservation.STOP_MACRO

    def tearDown(self):
        reservation.STOP_MACRO = self.stop
        session_helper._vault.clear()

    def post(self, path, **headers):
        return self.client.post(path, data={'user_id': 'x', 'password': 'y'},
                                headers=headers)

    def test_cross_site_post_is_rejected_everywhere(self):
        attacks = [
            {'Sec-Fetch-Site': 'cross-site', 'Origin': 'https://evil.example'},
            {'Sec-Fetch-Site': 'same-site'},
            {'Origin': 'https://evil.example'},        # Sec-Fetch-Site 없는 옛 브라우저
            {'Origin': 'null'},                        # 샌드박스 iframe
            {'Referer': 'https://evil.example/x'},     # Origin 도 없을 때
        ]
        with mock.patch.object(ServiceManager, 'logout_all') as logout_all, \
                mock.patch.object(ServiceManager, 'logout') as logout, \
                mock.patch.object(ServiceManager, 'login') as login:
            for headers in attacks:
                for path in self.PATHS:
                    resp = self.post(path, **headers)
                    self.assertEqual(resp.status_code, 403, (path, headers))
            logout.assert_not_called()
            logout_all.assert_not_called()
            login.assert_not_called()
        self.assertEqual(reservation.STOP_MACRO, self.stop)
        self.assertEqual(self.post('/api/card/clear', **attacks[0]).get_json()['success'], False)

    def test_same_origin_and_webview_requests_pass(self):
        ok_headers = [
            {'Sec-Fetch-Site': 'same-origin', 'Origin': 'http://localhost'},  # Android WebView (실제로는 http://127.0.0.1:<port>)
            {'Sec-Fetch-Site': 'none'},                                            # 주소창 입력
            {'Origin': 'http://localhost'},     # Sec-Fetch-Site 없는 옛 WKWebView
            {'Referer': 'http://localhost/'},
            {},                                       # 브라우저 밖 (스크립트·네이티브 호출)
        ]
        for headers in ok_headers:
            resp = self.post('/api/card/clear', **headers)
            self.assertEqual(resp.status_code, 200, headers)
            self.assertTrue(resp.get_json()['success'])
        resp = self.post('/stop_macro', Origin='http://localhost')
        self.assertEqual(resp.status_code, 200)

    def test_get_is_not_checked(self):
        resp = self.client.get('/api/telegram/status', headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(resp.status_code, 200)

    def test_referrer_policy_keeps_same_origin_origin(self):
        # 'no-referrer' 면 같은 사이트 POST 의 Origin 이 'null' 이 되어 옛 WKWebView 가 막힌다
        resp = self.client.get('/login')
        self.assertEqual(resp.headers['Referrer-Policy'], 'same-origin')


class TokenRedactionTest(unittest.TestCase):
    def test_redact_helper(self):
        url = f'https://api.telegram.org/bot{TOKEN}/getUpdates'
        self.assertEqual(redact_token(url), 'https://api.telegram.org/bot<숨김>/getUpdates')
        self.assertEqual(redact_token('평범한 문구'), '평범한 문구')

    def capture(self, logger_name):
        stream = io.StringIO()
        handler = logging.StreamHandler(stream)
        handler.setFormatter(logging.Formatter('%(message)s'))
        log = logging.getLogger(logger_name)
        log.addHandler(handler)
        old = log.level
        log.setLevel(logging.DEBUG)
        self.addCleanup(lambda: (log.removeHandler(handler), log.setLevel(old)))
        return stream

    def test_request_errors_do_not_log_token(self):
        import requests
        stream = self.capture(telegram_service.__name__)
        err = requests.ConnectionError(
            f"HTTPSConnectionPool(host='api.telegram.org', port=443): Max retries exceeded "
            f"with url: /bot{TOKEN}/getUpdates")
        tg = TelegramService()
        tg.bot_token = TOKEN
        with mock.patch.object(telegram_service.requests, 'post', side_effect=err), \
                mock.patch.object(telegram_service.time, 'sleep'):
            self.assertIsNone(tg._api_call('getMe'))
        with mock.patch.object(telegram_service.requests, 'get', side_effect=err):
            result = tg.configure(TOKEN)
        self.assertNotIn(TOKEN, result['message'])
        # 트레이스백까지 찍는 logger.exception 도
        try:
            raise err
        except requests.ConnectionError:
            logging.getLogger(telegram_service.__name__).exception('실패: %s', err)
        out = stream.getvalue()
        self.assertIn('bot<숨김>', out)
        self.assertNotIn(TOKEN, out)
        self.assertNotIn(TOKEN.split(':')[1], out)

    def test_urllib3_debug_log_is_redacted(self):
        stream = self.capture('urllib3.connectionpool')
        logging.getLogger('urllib3.connectionpool').debug(
            '%s://%s:%s "%s %s %s" %s', 'https', 'api.telegram.org', 443, 'POST',
            f'/bot{TOKEN}/getUpdates', 'HTTP/1.1', 200)
        self.assertNotIn(TOKEN, stream.getvalue())
        self.assertIn('/bot<숨김>/getUpdates', stream.getvalue())


class TelegramOwnerTest(unittest.TestCase):
    """HOST 를 LAN 에 열고 APP_PASSWORD 가 없을 때 옆 사람이 봇을 가로채지 못하게."""

    def setUp(self):
        session_helper._vault.clear()
        self.tmp = tempfile.TemporaryDirectory()
        self.tg = TelegramService.get_instance()
        self.saved = (self.tg.bot_token, self.tg.chat_id, self.tg.owner, self.tg._macro_running,
                      self.tg._macro_owner, self.tg._macro_start_time)
        self.tg.bot_token = self.tg.chat_id = self.tg.owner = None

        def fake_configure(tg, token, chat_id=''):
            tg.bot_token = token
            if chat_id:
                tg.chat_id = chat_id
            return {'success': True, 'message': 'ok'}

        self.patches = [
            mock.patch.object(telegram_service, 'SETTINGS_PATH', Path(self.tmp.name) / 'tg.json'),
            mock.patch.object(TelegramService, 'configure', fake_configure),
            mock.patch.object(TelegramService, 'start_polling'),
            mock.patch.object(TelegramService, 'stop_polling'),
            mock.patch.object(TelegramService, 'send_message', return_value=True),
            mock.patch('webui.routes.reservation._setup_telegram_callbacks'),
        ]
        [p.start() for p in self.patches]
        app = make_app()
        self.alice, self.bob, self.anon = app.test_client(), app.test_client(), app.test_client()
        sign_in(self.alice, 'alice')
        sign_in(self.bob, 'bob')

    def tearDown(self):
        [p.stop() for p in self.patches]
        (self.tg.bot_token, self.tg.chat_id, self.tg.owner, self.tg._macro_running,
         self.tg._macro_owner, self.tg._macro_start_time) = self.saved
        self.tg._pairing = None
        session_helper._vault.clear()
        self.tmp.cleanup()

    def test_only_owner_can_manage_bot(self):
        resp = self.alice.post('/api/telegram/configure', json={'bot_token': TOKEN})
        self.assertTrue(resp.get_json()['success'])
        self.assertEqual(self.tg.owner, 'alice')
        self.assertEqual(telegram_service.load_saved_settings()['owner'], 'alice')

        self.assertEqual(self.bob.post('/api/telegram/configure',
                                       json={'bot_token': '9:evil'}).status_code, 403)
        self.assertEqual(self.bob.post('/api/telegram/configure', json={}).status_code, 403)
        self.assertEqual(self.bob.get('/api/telegram/pairing').status_code, 403)
        self.assertEqual(self.bob.post('/api/telegram/test').status_code, 403)
        self.assertEqual(self.bob.post('/api/telegram/disconnect').status_code, 403)
        self.assertEqual(self.tg.bot_token, TOKEN)

        self.assertEqual(self.alice.get('/api/telegram/pairing').status_code, 200)
        self.assertTrue(self.alice.post('/api/telegram/disconnect').get_json()['success'])
        # 주인이 끊은 뒤에는 누구든 새로 연결할 수 있다
        self.assertTrue(self.bob.post('/api/telegram/configure',
                                      json={'bot_token': '9:new'}).get_json()['success'])
        self.assertEqual(self.tg.owner, 'bob')

    def test_saved_owner_survives_restart(self):
        telegram_service.save_settings(TOKEN, '', 'alice')
        self.assertEqual(self.bob.post('/api/telegram/configure', json={}).status_code, 403)
        self.assertIsNone(self.tg.bot_token)
        self.assertTrue(self.alice.post('/api/telegram/configure', json={}).get_json()['success'])
        self.assertEqual(self.tg.bot_token, TOKEN)

    def test_bot_without_owner_stays_usable(self):
        # 환경변수·예전 설정 파일로 붙은 봇은 주인을 모른다 → 로그인한 사람이면 다룰 수 있다
        self.tg.bot_token = TOKEN
        self.assertEqual(self.bob.get('/api/telegram/pairing').status_code, 200)

    def test_status_hides_busy_user_from_anonymous(self):
        from datetime import datetime
        self.tg._macro_running, self.tg._macro_owner = True, 'alice123'
        self.tg._macro_start_time = datetime(2026, 9, 23, 8, 0)
        anon = self.anon.get('/api/telegram/status').get_json()
        self.assertNotIn('ali', str(anon['busy']))
        self.assertIsNone(anon['busy']['since'])
        self.assertFalse(anon['macro_running'])
        self.assertEqual(anon['macro_info'], {})
        bob = self.bob.get('/api/telegram/status').get_json()
        self.assertEqual(bob['busy']['user'], 'ali***')
        # 주인이 기록되지 않은 매크로라도 로그인 안 한 사람에게는 자세히 보여주지 않는다
        self.tg._macro_owner = None
        self.assertFalse(self.anon.get('/api/telegram/status').get_json()['macro_running'])
        self.assertTrue(self.bob.get('/api/telegram/status').get_json()['macro_running'])


class BadInputTest(unittest.TestCase):
    def setUp(self):
        session_helper._vault.clear()
        self.client = make_app().test_client()
        sign_in(self.client, 'me')

    def tearDown(self):
        session_helper._vault.clear()

    def test_card_save_wrong_types_is_400(self):
        for body in ([1], 'x', {'card_number': 1}, {'card_number': '1234', 'card_password': ['12']}):
            resp = self.client.post('/api/card/save', json=body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertFalse(resp.get_json()['success'])
            self.assertTrue(resp.get_json()['message'])

    def test_telegram_configure_wrong_types_is_400(self):
        for body in ({'bot_token': 123}, {'bot_token': TOKEN, 'chat_id': [1]}, [1]):
            resp = self.client.post('/api/telegram/configure', json=body)
            self.assertEqual(resp.status_code, 400, body)
            self.assertFalse(resp.get_json()['success'])

    def test_search_more_hides_exception_text(self):
        service = mock.Mock()
        service.search.side_effect = RuntimeError('/home/secret/path 코레일 원문')
        with mock.patch.object(ServiceManager, 'get_service', return_value=service), \
                self.assertLogs('webui.routes.search', 'ERROR'):
            resp = self.client.post('/api/search_more', data={
                'dep': '서울', 'arr': '부산', 'date': '2026-10-03', 'last_time': '08:00:00'})
        self.assertEqual(resp.status_code, 500)
        self.assertNotIn('secret', resp.get_data(as_text=True))
        self.assertIn('열차', resp.get_json()['error'])


class LoginThrottleTest(unittest.TestCase):
    def setUp(self):
        session_helper._vault.clear()
        self.client = make_app().test_client()

    def tearDown(self):
        session_helper._vault.clear()

    def attempt(self, ip='10.0.0.5'):
        return self.client.post('/login', data={'user_id': 'victim', 'password': 'guess'},
                                environ_base={'REMOTE_ADDR': ip})

    def test_eleventh_attempt_in_a_minute_is_429(self):
        with mock.patch.object(ServiceManager, 'login', return_value=False) as login:
            for _ in range(10):
                self.assertEqual(self.attempt().status_code, 200)
            resp = self.attempt()
            self.assertEqual(resp.status_code, 429)
            self.assertIn('너무 많습니다', resp.get_data(as_text=True))
            self.assertEqual(login.call_count, 10)
            # 다른 IP 는 따로 센다
            self.assertEqual(self.attempt('10.0.0.6').status_code, 200)

    def test_window_expires(self):
        clock = [1000.0]
        with mock.patch.object(ServiceManager, 'login', return_value=False), \
                mock.patch('webui.time.monotonic', side_effect=lambda: clock[0]):
            for _ in range(10):
                self.attempt()
            self.assertEqual(self.attempt().status_code, 429)
            clock[0] += 61
            self.assertEqual(self.attempt().status_code, 200)


if __name__ == '__main__':
    unittest.main(verbosity=2)
