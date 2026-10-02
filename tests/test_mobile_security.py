# -*- coding: utf-8 -*-
"""폰 런타임 보안: 무작위 포트, /__hello 서버 확인, 좌석을 잡으면 작업 삭제(중복 예약 방지),
자동 재개 실패 경로, 로그아웃·카드 삭제와 작업, 요청 로그에서 토큰 숨기기."""
import hashlib
import hmac
import json
import logging
import os
import sys
import threading
import unittest
from datetime import datetime, timedelta
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, os.path.join(ROOT, 'mobile', 'shared'))

import requests

import mobile_runtime
import webui.routes.reservation as reservation
from core.base_service import SeatOption
from core.reservation import END_SUCCESS
from webui.services.telegram_service import TelegramService
from webui.utils import session_helper


class Bridge:
    """플랫폼 연결부 가짜. 호출을 순서대로 모은다."""
    calls: list = []

    @classmethod
    def saveJob(cls, data):
        cls.calls.append(('save', json.loads(data)))

    @classmethod
    def clearJob(cls):
        cls.calls.append(('clear', None))

    @classmethod
    def notifyEvent(cls, *args):
        cls.calls.append(('notify', args))

    @classmethod
    def onMacroState(cls, *args):
        pass

    @classmethod
    def onServerReady(cls, port):
        cls.calls.append(('ready', port))

    @classmethod
    def kinds(cls):
        return [c[0] if c[0] != 'notify' else 'notify:' + c[1][0] for c in cls.calls]


def future(days=1):
    return (datetime.now() + timedelta(days=days)).strftime('%Y%m%d')


class HelloAndPortTest(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        self.app = Flask(__name__)
        self.app.add_url_rule('/', 'index', lambda: 'hi')
        mobile_runtime._require_token(self.app, 'tok-123')
        self.client = self.app.test_client()
        Bridge.calls = []
        mobile_runtime.set_bridge(Bridge)

    def test_hello_proves_token_without_revealing_it(self):
        resp = self.client.get('/__hello?nonce=abc123')
        self.assertEqual(resp.status_code, 200)
        expected = hmac.new(b'tok-123', b'abc123', hashlib.sha256).hexdigest()
        self.assertEqual(resp.get_json(), {'mac': expected})
        self.assertNotIn(b'tok-123', resp.data)
        self.assertEqual(mobile_runtime.hello_mac('tok-123', 'abc123'), expected)

    def test_hello_needs_sane_nonce(self):
        self.assertEqual(self.client.get('/__hello').status_code, 400)
        self.assertEqual(self.client.get('/__hello?nonce=' + 'a' * 129).status_code, 400)

    def test_other_routes_still_need_token(self):
        self.assertEqual(self.client.get('/').status_code, 403)
        self.client.set_cookie('app_token', 'tok-123')
        self.assertEqual(self.client.get('/').status_code, 200)

    def test_port_zero_picks_random_port_and_reports_it(self):
        server = mobile_runtime._bind(self.app, 0)
        try:
            port = server.server_port
            self.assertNotEqual(port, 0)
            self.assertIn(('ready', port), Bridge.calls)
            self.assertEqual(mobile_runtime.server_port(0), port)
        finally:
            server.server_close()

    def test_bridge_without_on_server_ready_is_fine(self):
        class OldBridge:
            pass
        mobile_runtime.set_bridge(OldBridge)
        server = mobile_runtime._bind(self.app, 0)
        server.server_close()
        mobile_runtime.set_bridge(Bridge)

    def test_request_logs_are_quiet(self):
        logging.getLogger('werkzeug').setLevel(logging.INFO)
        mobile_runtime._quiet_request_logs()
        self.assertFalse(logging.getLogger('werkzeug').isEnabledFor(logging.INFO))


class SupervisorCase(unittest.TestCase):
    def setUp(self):
        Bridge.calls = []
        mobile_runtime.set_bridge(Bridge)
        self.original = reservation.run_reservation_loop
        self.listeners = list(reservation._macro_listeners)
        self.session_listeners = list(session_helper._session_listeners)
        mobile_runtime._crash_times.clear()
        mobile_runtime._supervise_macros()
        self.tg = TelegramService.get_instance()
        self.tg.set_macro_state(False)

    def tearDown(self):
        reservation.run_reservation_loop = self.original
        reservation._macro_listeners[:] = self.listeners
        session_helper._session_listeners[:] = self.session_listeners
        self.tg.set_macro_state(False)
        self.tg._macro_owner = None

    def service(self):
        s = mock.Mock()
        s.credentials = {'user_id': 'u', 'password': 'p'}
        return s

    def run_supervised(self, fake_run, card=None, **kw):
        with mock.patch.object(reservation, 'run_reservation', side_effect=fake_run), \
                mock.patch.object(mobile_runtime.threading, 'Timer') as timer:
            self.tg.try_start_macro(owner='u')
            reason = reservation.run_reservation_loop(
                self.service(), 'korail', [{'dep_date': future(), 'dep_time': '080000'}],
                SeatOption.GENERAL_FIRST, card, owner='u', **kw)
        return reason, timer


def reserve_then(reason):
    """좌석을 잡은(예약 성공 알림) 뒤 reason 으로 끝나는 가짜 예약 루프."""
    def fake(service, trains, seat_option, card, reporter=None, **kw):
        reporter.send_reservation_success(train_name='KTX', dep_time='08:00',
                                          dep_station='서울', arr_station='부산')
        Bridge.calls.append(('after_reserved', None))
        return reason
    return fake


class ReservedClearsJobTest(SupervisorCase):
    def test_job_is_cleared_as_soon_as_a_seat_is_taken(self):
        self.run_supervised(reserve_then(END_SUCCESS))
        kinds = Bridge.kinds()
        self.assertEqual(kinds[0], 'save')
        # 결제·마무리 전에 이미 지워져 있다
        self.assertLess(kinds.index('clear'), kinds.index('after_reserved'))

    def test_crash_after_reservation_is_not_restarted(self):
        """결제 도중 죽어도 같은 열차를 다시 예약하지 않는다."""
        reason, timer = self.run_supervised(reserve_then('crash'))
        self.assertEqual(reason, 'crash')
        timer.assert_not_called()
        self.assertNotIn('save', Bridge.kinds()[1:])

    def test_sequential_first_seat_also_clears(self):
        reason, timer = self.run_supervised(reserve_then('crash'), passenger_count=2, sequential=True)
        timer.assert_not_called()
        self.assertIn('clear', Bridge.kinds())

    def test_crash_before_reservation_still_restarts(self):
        reason, timer = self.run_supervised(lambda *a, **k: 'crash')
        timer.assert_called_once()


class SessionEventsTest(SupervisorCase):
    def run_with_event(self, event, user_id, card=None):
        def fake(service, trains, seat_option, card_arg, **kw):
            session_helper.notify_session_event(event, user_id)
            Bridge.calls.append(('card_seen', dict(card_arg) if card_arg else None))
            return 'crash'
        return self.run_supervised(fake, card=card)

    def test_logout_clears_job_and_stops_restart(self):
        reason, timer = self.run_with_event('logout', 'u')
        self.assertEqual(Bridge.kinds()[:2], ['save', 'clear'])
        timer.assert_not_called()

    def test_someone_elses_logout_is_ignored(self):
        reason, timer = self.run_with_event('logout', 'other')
        self.assertEqual(Bridge.kinds()[:2], ['save', 'card_seen'])
        timer.assert_called_once()

    def test_card_clear_removes_card_from_job_and_running_macro(self):
        card = {'card_number': '1234', 'auto_pay': True}
        self.run_with_event('card_cleared', 'u', card=card)
        saves = [c[1] for c in Bridge.calls if c[0] == 'save']
        self.assertEqual(saves[0]['card']['card_number'], '1234')
        self.assertIsNone(saves[1]['card'])
        self.assertIn(('card_seen', None), Bridge.calls)


class StartJobFailurePathsTest(unittest.TestCase):
    JOB = {'user_id': 'u', 'password': 'p', 'owner': 'u', 'fake': False,
           'trains': [{'dep_date': future(), 'dep_time': '080000', 'train_name': 'KTX',
                       'train_number': '1', 'dep_station': '서울', 'arr_station': '부산'}],
           'seat_option': 'GENERAL_FIRST', 'card': None, 'passenger_count': 1,
           'sequential': False, 'call_interval': 1}

    def setUp(self):
        Bridge.calls = []
        mobile_runtime.set_bridge(Bridge)
        self.tg = TelegramService.get_instance()
        self.tg.set_macro_state(False)
        mobile_runtime._ready.set()

    def tearDown(self):
        self.tg.set_macro_state(False)
        self.tg._macro_owner = None

    def test_unexpected_login_error_gives_up_and_clears(self):
        with mock.patch('core.korail_service.KorailService.login', side_effect=ValueError('깨짐')), \
                mock.patch.object(reservation, 'run_reservation_loop') as loop:
            mobile_runtime._start_job(dict(self.JOB), True)
        loop.assert_not_called()
        self.assertEqual(Bridge.kinds(), ['clear', 'notify:gave_up'])
        self.assertFalse(self.tg._macro_running)

    def test_broken_job_gives_up(self):
        job = dict(self.JOB, seat_option='NOPE')
        with mock.patch('core.korail_service.KorailService.login', return_value=True), \
                mock.patch.object(reservation, 'run_reservation_loop') as loop:
            mobile_runtime._start_job(job, False)
        loop.assert_not_called()
        self.assertIn('notify:gave_up', Bridge.kinds())
        self.assertFalse(self.tg._macro_running, '자리를 돌려준다')

    def test_network_error_still_retries(self):
        answers = iter([requests.ConnectionError('x'), True])

        def login(*a):
            v = next(answers)
            if isinstance(v, Exception):
                raise v
            return v
        with mock.patch('core.korail_service.KorailService.login', side_effect=login), \
                mock.patch.object(mobile_runtime.time, 'sleep'), \
                mock.patch.object(reservation, 'run_reservation_loop') as loop:
            mobile_runtime._start_job(dict(self.JOB), False)
        loop.assert_called_once()

    def test_resumed_notice_comes_after_macro_really_starts(self):
        with mock.patch('core.korail_service.KorailService.login', return_value=True), \
                mock.patch.object(reservation, 'run_reservation_loop',
                                  side_effect=lambda *a, **k: Bridge.calls.append(('loop', None))):
            mobile_runtime._start_job(dict(self.JOB), True)
        self.assertEqual(Bridge.kinds(), ['notify:resumed', 'loop'])

    def test_no_resume_notice_when_login_is_refused(self):
        with mock.patch('core.korail_service.KorailService.login', return_value=False):
            mobile_runtime._start_job(dict(self.JOB), True)
        self.assertNotIn('notify:resumed', Bridge.kinds())
        self.assertIn('notify:gave_up', Bridge.kinds())

    def test_does_not_run_without_supervisor(self):
        mobile_runtime._ready.clear()
        try:
            with mock.patch.object(mobile_runtime, 'READY_TIMEOUT', 0.01), \
                    mock.patch('core.korail_service.KorailService.login') as login:
                mobile_runtime._start_job(dict(self.JOB), True)
            login.assert_not_called()
            self.assertEqual(Bridge.kinds(), ['clear', 'notify:gave_up'])
        finally:
            mobile_runtime._ready.set()


class StaleJobTest(unittest.TestCase):
    def test_departed_today_is_stale(self):
        now = datetime(2026, 10, 3, 9, 0)
        job = {'trains': [{'dep_date': '20261003', 'dep_time': '080000'}]}
        self.assertTrue(mobile_runtime._job_is_stale(job, now))
        job['trains'].append({'dep_date': '20261003', 'dep_time': '100000'})
        self.assertFalse(mobile_runtime._job_is_stale(job, now), '아직 안 떠난 열차가 있다')

    def test_dates(self):
        now = datetime(2026, 10, 3, 9, 0)
        self.assertTrue(mobile_runtime._job_is_stale({'trains': [{'dep_date': '20261002'}]}, now))
        self.assertFalse(mobile_runtime._job_is_stale({'trains': [{'dep_date': '20261003'}]}, now))
        self.assertTrue(mobile_runtime._job_is_stale({'trains': []}, now))


if __name__ == '__main__':
    unittest.main(verbosity=2)
