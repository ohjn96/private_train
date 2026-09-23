# -*- coding: utf-8 -*-
"""서버 모드: 여러 명이 접속해도 매크로는 한 명만, 남의 것은 못 보고 못 건드린다. 웹 푸시."""
import json
import os
import sys
import tempfile
import threading
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from pywebpush import WebPushException

import webui.routes.reservation as reservation
from webui.services import ServiceManager
from webui.services.korail_service import KorailService
from webui.services.telegram_service import TelegramService
from webui.utils import session_helper
from core.base_service import SeatOption
from server import create_server_app
from server.push import SubscriptionStore, WebPusher, load_or_create_vapid
from test_reservation_flow import FakeTrain, FastRateLimit, as_selected, make_service

TRAIN = {
    'index': 0, 'train_name': 'KTX', 'train_number': '101', 'dep_date': '20261003',
    'dep_time': '080000', 'arr_time': '104000', 'dep_station': '서울', 'arr_station': '부산',
    'dep_time_formatted': '08:00', 'arr_time_formatted': '10:40',
}


def fake_pusher(tmp: Path) -> WebPusher:
    return WebPusher(
        load_or_create_vapid(tmp / 'vapid.pem'),
        SubscriptionStore(tmp / 'subs.json'),
        'mailto:test@example.com',
    )


def sign_in(client, user_id):
    sid = f'sid-{user_id}'
    session_helper._vault[sid] = {
        'credentials': {'korail': {'user_id': user_id, 'password': 'pw'}}, 'cards': {}}
    with client.session_transaction() as sess:
        sess['sid'] = sid
        sess['auth'] = {'korail': {'logged_in': True, 'user_id': user_id}}
        sess['current_provider'] = 'korail'
        sess['search_state'] = {'korail': {
            'trains': [TRAIN], 'selected_indices': [0],
            'seat_option': 'GENERAL_FIRST', 'form_data': {}}}


class ServerTestCase(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.pusher = fake_pusher(Path(self.tmp.name))

        def fake_login(svc, user_id, password):
            svc._user_id, svc._password = user_id, password
            svc._client = type('C', (), {'logined': True})()
            return True

        self.patches = [
            mock.patch.object(KorailService, 'login', fake_login),
            mock.patch.object(KorailService, 'is_logged_in', lambda svc: svc._client is not None),
        ]
        [p.start() for p in self.patches]
        ServiceManager._services.clear()
        session_helper._vault.clear()
        self.listeners = list(reservation._macro_listeners)
        self.app = create_server_app(self.pusher)
        self.tg = TelegramService.get_instance()
        self.tg.set_macro_state(False)
        self.tg._macro_owner = None

    def tearDown(self):
        reservation.STOP_MACRO = True
        [p.stop() for p in self.patches]
        reservation._macro_listeners[:] = self.listeners
        ServiceManager._services.clear()
        session_helper._vault.clear()
        self.tg.set_macro_state(False)
        self.tg._macro_owner = None
        self.tmp.cleanup()

    def client_for(self, user_id):
        client = self.app.test_client()
        sign_in(client, user_id)
        return client


class OneMacroAtATimeTest(ServerTestCase):
    def setUp(self):
        super().setUp()
        self.release = threading.Event()

        def fake_loop(*args, **kwargs):
            self.release.wait(5)
            self.tg.set_macro_state(False)

        self.loop_patch = mock.patch.object(reservation, 'run_reservation_loop', fake_loop)
        self.loop_patch.start()
        self.alice = self.client_for('010-1111-1111')
        self.bob = self.client_for('010-2222-2222')

    def tearDown(self):
        self.release.set()
        self.loop_patch.stop()
        super().tearDown()

    def test_second_user_is_told_who_is_busy(self):
        self.assertTrue(self.alice.get('/start_reservation').get_json()['success'])
        self.assertEqual(self.tg.macro_owner, '010-1111-1111')

        resp = self.bob.get('/start_reservation').get_json()
        self.assertFalse(resp['success'])
        self.assertIn('010***', resp['message'])
        self.assertNotIn('1111', resp['message'])

    def test_status_hides_other_users_macro(self):
        self.alice.get('/start_reservation')
        mine = self.alice.get('/api/telegram/status').get_json()
        theirs = self.bob.get('/api/telegram/status').get_json()
        self.assertTrue(mine['macro_running'])
        self.assertNotIn('busy', mine)
        self.assertFalse(theirs['macro_running'])
        self.assertFalse(theirs['has_logs'])
        self.assertEqual(theirs['busy']['user'], '010***')

    def test_cannot_stop_someone_elses_macro(self):
        self.alice.get('/start_reservation')
        reservation.STOP_MACRO = False
        self.assertEqual(self.bob.post('/stop_macro').status_code, 403)
        self.assertFalse(reservation.STOP_MACRO)
        self.assertEqual(self.alice.post('/stop_macro').status_code, 200)
        self.assertTrue(reservation.STOP_MACRO)

    def test_cannot_read_someone_elses_logs(self):
        self.alice.get('/start_reservation')
        self.tg.push_log('log', '앨리스의 비밀 로그')
        body = self.bob.get('/macro_stream').get_data(as_text=True)
        self.assertNotIn('앨리스', body)
        self.assertIn('stream_end', body)

    def test_next_user_can_start_after_first_finishes(self):
        self.alice.get('/start_reservation')
        self.release.set()
        for _ in range(50):
            if not self.tg._macro_running:
                break
            threading.Event().wait(0.05)
        self.assertTrue(self.bob.get('/start_reservation').get_json()['success'])
        self.assertEqual(self.tg.macro_owner, '010-2222-2222')


class ServerModeTest(ServerTestCase):
    def test_bot_settings_are_locked(self):
        client = self.client_for('me')
        for path in ('/api/telegram/configure', '/api/telegram/disconnect', '/api/telegram/test'):
            self.assertEqual(client.post(path, json={}).status_code, 403, path)
        self.assertEqual(client.get('/api/telegram/status').status_code, 200)

    def test_page_shows_push_card_instead_of_telegram(self):
        html = self.client_for('me').get('/').get_data(as_text=True)
        self.assertIn('data-testid="push-card"', html)
        self.assertNotIn('id="tgBotToken"', html)

    def test_each_user_keeps_own_login(self):
        """두 번째 사람이 로그인해도 첫 사람의 코레일 세션을 밀어내지 않는다."""
        a, b = self.app.test_client(), self.app.test_client()
        a.post('/login', data={'user_id': 'alice', 'password': 'pw'})
        b.post('/login', data={'user_id': 'bob', 'password': 'pw'})
        self.assertEqual(set(ServiceManager._services), {('korail', 'alice'), ('korail', 'bob')})
        self.assertEqual(ServiceManager._services[('korail', 'alice')]._user_id, 'alice')


class DesktopModeUnchangedTest(unittest.TestCase):
    def test_desktop_still_configures_telegram(self):
        from webui import create_app
        client = create_app(server_mode=False).test_client()
        sign_in(client, 'me')
        # 실제 홈 폴더에 저장된 봇 토큰으로 붙지 않게 막는다 (실행 중인 봇과 폴링이 충돌)
        with mock.patch('webui.routes.telegram.load_saved_settings',
                        return_value={'token': '', 'chat_id': ''}):
            resp = client.post('/api/telegram/configure', json={'bot_token': ''})
        self.assertNotEqual(resp.status_code, 403)
        html = client.get('/').get_data(as_text=True)
        self.assertIn('id="tgBotToken"', html)
        self.assertNotIn('data-testid="push-card"', html)
        session_helper._vault.clear()


SUB = {'endpoint': 'https://web.push.apple.com/abc', 'keys': {'p256dh': 'k', 'auth': 'a'}}
SUB2 = {'endpoint': 'https://fcm.googleapis.com/xyz', 'keys': {'p256dh': 'k2', 'auth': 'a2'}}


class PushTest(ServerTestCase):
    def test_public_key_is_uncompressed_p256_point(self):
        from py_vapid import b64urldecode
        raw = b64urldecode(self.pusher.public_key.encode())
        self.assertEqual(len(raw), 65)
        self.assertEqual(raw[0], 4)

    def test_vapid_key_is_private_and_reused(self):
        path = Path(self.tmp.name) / 'vapid.pem'
        self.assertEqual(os.stat(path).st_mode & 0o777, 0o600)
        again = load_or_create_vapid(path)
        self.assertEqual(again.public_key.public_numbers(), self.pusher._vapid.public_key.public_numbers())

    def test_needs_login(self):
        self.assertEqual(self.app.test_client().get('/api/push/key').status_code, 401)

    def test_subscribe_and_status(self):
        client = self.client_for('me')
        self.assertTrue(client.post('/api/push/subscribe', json={'subscription': SUB}).get_json()['success'])
        status = client.get('/api/push/status?endpoint=' + SUB['endpoint']).get_json()
        self.assertEqual(status, {'devices': 1, 'this_device': True})
        # 재시작해도 남아 있다
        reloaded = SubscriptionStore(Path(self.tmp.name) / 'subs.json')
        self.assertEqual(reloaded.get('me')[0]['endpoint'], SUB['endpoint'])

    def test_rejects_bad_subscription(self):
        client = self.client_for('me')
        bad = {'endpoint': 'http://evil/', 'keys': {'p256dh': 'k', 'auth': 'a'}}
        self.assertEqual(client.post('/api/push/subscribe', json={'subscription': bad}).status_code, 400)

    def test_device_moves_to_new_owner(self):
        self.pusher.store.add('alice', SUB)
        self.pusher.store.add('bob', SUB)
        self.assertEqual(self.pusher.store.get('alice'), [])
        self.assertEqual(len(self.pusher.store.get('bob')), 1)

    def test_send_uses_fresh_claims_per_device_and_drops_gone_devices(self):
        self.pusher.store.add('me', SUB)
        self.pusher.store.add('me', SUB2)
        claims_seen = []

        def fake_webpush(sub, data=None, vapid_claims=None, **kw):
            claims_seen.append(dict(vapid_claims))
            vapid_claims['aud'] = 'mutated-by-webpush'
            if 'apple' in sub['endpoint']:
                raise WebPushException('gone', response=mock.Mock(status_code=410))
            self.assertEqual(json.loads(data)['title'], '제목')

        with mock.patch('server.push.webpush', fake_webpush):
            sent = self.pusher.send('me', '제목', '본문')
        self.assertEqual(sent, 1)
        self.assertTrue(all('aud' not in c for c in claims_seen), '앞 기기의 aud 가 새면 안 된다')
        self.assertEqual([s['endpoint'] for s in self.pusher.store.get('me')], [SUB2['endpoint']])

    def test_macro_success_reaches_owner_by_push(self):
        sent = []
        self.pusher.send_later = lambda user, title, body: sent.append((user, title, body))
        trains = [FakeTrain(0, has_seat=True)]
        service = make_service(trains)
        self.tg.try_start_macro(owner='me')
        with FastRateLimit():
            reservation.run_reservation_loop(
                service, 'korail', [as_selected(trains[0], 0)], SeatOption.GENERAL_FIRST, None,
                owner='me',
            )
        self.assertEqual([(u, t) for u, t, _ in sent], [('me', '🎉 예약 성공')])


if __name__ == '__main__':
    unittest.main(verbosity=2)
