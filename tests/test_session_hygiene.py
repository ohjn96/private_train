# -*- coding: utf-8 -*-
"""서버 메모리에 남는 비밀번호·카드: 오래 안 쓰면 지우고, 로그아웃하면 지우고, 섞지 않는다."""
import os
import sys
import threading
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import webui.routes.reservation as reservation
from webui.services.telegram_service import TelegramService
from webui.utils import session_helper
from test_server import ServerTestCase, sign_in


class VaultExpiryTest(ServerTestCase):
    def test_idle_entry_expires_in_server_mode(self):
        client = self.client_for('alice')
        with mock.patch('webui.utils.session_helper.time.monotonic', return_value=1000.0):
            self.assertEqual(client.get('/api/card/status').status_code, 200)
        self.assertIn('sid-alice', session_helper._vault)
        later = 1000.0 + session_helper.VAULT_IDLE_TTL + 1
        with mock.patch('webui.utils.session_helper.time.monotonic', return_value=later):
            resp = client.get('/')
        self.assertEqual(resp.status_code, 302, '다시 로그인해야 한다')
        self.assertNotIn('sid-alice', session_helper._vault)

    def test_active_entry_is_kept(self):
        client = self.client_for('alice')
        t = 1000.0
        for _ in range(3):
            t += session_helper.VAULT_IDLE_TTL / 2
            with mock.patch('webui.utils.session_helper.time.monotonic', return_value=t):
                client.get('/api/card/status')
        self.assertIn('sid-alice', session_helper._vault)

    def test_sweep_drops_other_idle_entries(self):
        session_helper._vault['sid-gone'] = {'credentials': {}, 'cards': {}}
        session_helper._vault_touched['sid-gone'] = 0.0
        session_helper._last_sweep['at'] = 0.0
        client = self.client_for('alice')
        with mock.patch('webui.utils.session_helper.time.monotonic',
                        return_value=session_helper.VAULT_IDLE_TTL + 100):
            client.get('/api/card/status')
        self.assertNotIn('sid-gone', session_helper._vault)

    def test_session_cookie_has_lifetime_in_server_mode(self):
        self.assertEqual(self.app.permanent_session_lifetime.total_seconds(),
                         session_helper.VAULT_IDLE_TTL)
        resp = self.app.test_client().post('/login', data={'user_id': 'alice', 'password': 'pw'})
        cookie = [c for c in resp.headers.getlist('Set-Cookie') if c.startswith('session=')][0]
        self.assertIn('Expires=', cookie)


class DesktopVaultDoesNotExpireTest(unittest.TestCase):
    def test_desktop_keeps_login(self):
        from webui import create_app
        session_helper._vault.clear()
        client = create_app(server_mode=False).test_client()
        sign_in(client, 'me')
        with mock.patch('webui.utils.session_helper.time.monotonic', return_value=1e9):
            client.get('/api/telegram/status')
        self.assertIn('sid-me', session_helper._vault)
        session_helper._vault.clear()


class ServerModeKeepsSecretsOutOfTelegramTest(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.release = threading.Event()

        def fake_loop(*args, **kwargs):
            self.release.wait(5)
            self.tg.set_macro_state(False)

        self.loop_patch = mock.patch.object(reservation, 'run_reservation_loop', fake_loop)
        self.loop_patch.start()
        self.tg.clear_web_session()

    def tearDown(self):
        self.release.set()
        self.loop_patch.stop()
        self.tg.clear_web_session()
        super().tearDown()

    def test_start_does_not_copy_credentials(self):
        alice = self.client_for('alice')
        session_helper._vault['sid-alice']['cards']['korail'] = {'card_number': '1234'}
        self.assertTrue(alice.post('/start_reservation').get_json()['success'])
        self.assertIsNone(self.tg._stored_credentials)
        self.assertIsNone(self.tg._stored_card_settings)

    def test_busy_request_does_not_touch_globals(self):
        alice, bob = self.client_for('alice'), self.client_for('bob')
        alice.post('/start_reservation')
        with mock.patch.object(reservation, '_setup_telegram_callbacks') as setup:
            self.assertFalse(bob.post('/start_reservation').get_json()['success'])
            setup.assert_not_called()


class LogoutClearsTelegramCopyTest(unittest.TestCase):
    def setUp(self):
        from webui import create_app
        from webui.services import ServiceManager
        session_helper._vault.clear()
        ServiceManager._services.clear()
        self.client = create_app(server_mode=False).test_client()
        sign_in(self.client, 'me')
        self.tg = TelegramService.get_instance()
        self.events = []
        self.listener = lambda event, uid: self.events.append((event, uid))
        session_helper.add_session_listener(self.listener)

    def tearDown(self):
        session_helper._session_listeners.remove(self.listener)
        self.tg.clear_web_session()
        session_helper._vault.clear()

    def test_logout(self):
        self.tg.store_web_session('korail', {'user_id': 'me', 'password': 'pw'})
        self.tg.store_card_settings({'card_number': '1'})
        self.client.post('/logout')
        self.assertIsNone(self.tg._stored_credentials)
        self.assertIsNone(self.tg._stored_card_settings)
        self.assertIn(('logout', 'me'), self.events)

    def test_someone_elses_logout_keeps_mine(self):
        self.tg.store_web_session('korail', {'user_id': 'other', 'password': 'pw'})
        self.client.post('/logout')
        self.assertEqual(self.tg._stored_credentials['user_id'], 'other')

    def test_card_clear(self):
        self.tg.store_web_session('korail', {'user_id': 'me', 'password': 'pw'})
        self.tg.store_card_settings({'card_number': '1'})
        self.assertTrue(self.client.post('/api/card/clear').get_json()['success'])
        self.assertIsNone(self.tg._stored_card_settings)
        self.assertEqual(self.tg._stored_credentials['user_id'], 'me')
        self.assertIn(('card_cleared', 'me'), self.events)


if __name__ == '__main__':
    unittest.main(verbosity=2)
