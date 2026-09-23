# -*- coding: utf-8 -*-
"""외부에 열어 둘 때 필요한 보안 동작."""
import base64
import json
import os
import unittest
import zlib
from unittest import mock

from app.services import ServiceManager
from app.services.korail_service import KorailService
from app.utils import session_helper


def decode_session_cookie(client) -> dict:
    """Flask 세션 쿠키는 서명만 돼 있어 누구나 이렇게 풀어 볼 수 있다."""
    cookie = client.get_cookie('session')
    payload = cookie.value
    compressed = payload.startswith('.')
    body = payload.lstrip('.').split('.')[0]
    raw = base64.urlsafe_b64decode(body + '=' * (-len(body) % 4))
    if compressed:
        raw = zlib.decompress(raw)
    return json.loads(raw)


class SecretsStayOffTheCookieTest(unittest.TestCase):
    def setUp(self):
        from app import create_app

        def fake_login(svc, user_id, password):
            svc._user_id, svc._password = user_id, password
            svc._client = type('C', (), {'logined': True})()
            return True

        self.patches = [
            mock.patch.object(KorailService, 'login', fake_login),
            mock.patch.object(KorailService, 'is_logged_in',
                              lambda svc: svc._client is not None),
        ]
        [p.start() for p in self.patches]
        ServiceManager._services.clear()
        session_helper._vault.clear()
        self.client = create_app().test_client()

    def tearDown(self):
        [p.stop() for p in self.patches]
        ServiceManager._services.clear()
        session_helper._vault.clear()

    def login(self):
        return self.client.post('/login', data={'user_id': 'me', 'password': 's3cret-pw'})

    def test_password_is_not_in_cookie(self):
        self.login()
        cookie = json.dumps(decode_session_cookie(self.client))
        self.assertNotIn('s3cret-pw', cookie)
        self.assertEqual(session_helper._vault[decode_session_cookie(self.client)['sid']]
                         ['credentials']['korail']['password'], 's3cret-pw')

    def test_card_is_not_in_cookie(self):
        self.login()
        self.client.post('/api/card/save', json={
            'card_number': '1234567812345678', 'card_password': '12',
            'validation_number': '900101', 'card_expire': '2912'})
        cookie = decode_session_cookie(self.client)
        self.assertNotIn('1234567812345678', json.dumps(cookie))
        self.assertEqual(session_helper._vault[cookie['sid']]['cards']['korail']['card_number'],
                         '1234567812345678')

    def test_empty_vault_means_logged_out(self):
        """재시작으로 금고가 비면 쿠키가 남아 있어도 로그인 화면으로 보낸다."""
        self.login()
        session_helper._vault.clear()
        resp = self.client.get('/')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/login', resp.headers['Location'])

    def test_forged_auth_cookie_does_not_reuse_cached_service(self):
        """다른 사람이 로그인해 둔 인스턴스를 자격증명 없는 세션이 가져가면 안 된다."""
        self.login()
        other = ServiceManager._services[('korail', 'me')]
        intruder = self.client.application.test_client()
        with intruder.session_transaction() as sess:
            sess['auth'] = {'korail': {'logged_in': True, 'user_id': 'me'}}
            sess['current_provider'] = 'korail'
        with self.client.application.test_request_context():
            from flask import session
            session.update({'auth': {'korail': {'logged_in': True, 'user_id': 'me'}}})
            self.assertIsNot(ServiceManager.get_service('korail'), other)
        self.assertEqual(intruder.get('/').status_code, 302)

    def test_logout_empties_the_vault(self):
        self.login()
        self.client.post('/logout', data={'logout_all': 'true'})
        self.assertEqual(session_helper._vault, {})


class SecretKeyTest(unittest.TestCase):
    def test_env_key_wins(self):
        from app import _load_secret_key
        with mock.patch.dict(os.environ, {'FLASK_SECRET_KEY': 'from-env'}):
            self.assertEqual(_load_secret_key(), 'from-env')

    def test_generated_key_is_random_and_private(self):
        import tempfile
        from pathlib import Path
        import app as app_module
        with tempfile.TemporaryDirectory() as d, \
                mock.patch.dict(os.environ, {}, clear=False), \
                mock.patch.object(app_module, 'SECRET_KEY_PATH', Path(d) / 'secret_key'):
            os.environ.pop('FLASK_SECRET_KEY', None)
            first = app_module._load_secret_key()
            self.assertNotEqual(first, 'train_reservation_secret_key_2024')
            self.assertGreaterEqual(len(first), 32)
            self.assertEqual(app_module._load_secret_key(), first, '재시작 뒤에도 같은 키')
            self.assertEqual(os.stat(Path(d) / 'secret_key').st_mode & 0o777, 0o600)


class AccessGateTest(unittest.TestCase):
    def make_client(self, password):
        from app import create_app
        with mock.patch.dict(os.environ, {'APP_PASSWORD': password}):
            return create_app().test_client()

    def test_no_password_means_no_gate(self):
        resp = self.make_client('').get('/login')
        self.assertEqual(resp.status_code, 200)

    def test_pages_redirect_to_gate(self):
        resp = self.make_client('letmein').get('/login')
        self.assertEqual(resp.status_code, 302)
        self.assertIn('/gate', resp.headers['Location'])

    def test_api_gets_401(self):
        resp = self.make_client('letmein').get('/api/telegram/status')
        self.assertEqual(resp.status_code, 401)

    def test_right_password_opens_the_gate(self):
        client = self.make_client('letmein')
        resp = client.post('/gate?next=/login', data={'password': 'letmein'})
        self.assertEqual(resp.headers['Location'], '/login')
        self.assertEqual(client.get('/login').status_code, 200)

    @mock.patch('app.time.sleep')
    def test_wrong_password_stays_shut(self, _sleep):
        client = self.make_client('letmein')
        resp = client.post('/gate', data={'password': 'nope'})
        self.assertEqual(resp.status_code, 200)
        self.assertIn('비밀번호가 틀렸습니다'.encode(), resp.data)
        self.assertEqual(client.get('/login').status_code, 302)

    def test_next_cannot_leave_the_site(self):
        client = self.make_client('letmein')
        resp = client.post('/gate?next=//evil.example', data={'password': 'letmein'})
        self.assertEqual(resp.headers['Location'], '/')


if __name__ == '__main__':
    unittest.main(verbosity=2)
