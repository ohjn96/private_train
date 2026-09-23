# -*- coding: utf-8 -*-
"""폰 헬스체크: 로그마다 진행 시각 갱신, 예약·결제 중 표시(phase), 배터리 예외 경로."""
import json
import os
import sys
import time
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, 'mobile', 'shared'))

import mobile_runtime
import webui.routes.reservation as reservation
from core.base_service import SeatOption
from core.reservation import END_SUCCESS
from webui.services.telegram_service import TelegramService


class Bridge:
    calls: list = []
    exempt = False

    @classmethod
    def saveJob(cls, data):
        cls.calls.append(('save', None))

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
    def batteryStatus(cls):
        return json.dumps({'exempt': cls.exempt, 'maker': 'samsung', 'tip': '절전 예외'})

    @classmethod
    def requestBatteryExemption(cls):
        cls.calls.append(('ask_battery', None))
        return True


class Result:
    def __init__(self, success):
        self.success = success


class PhaseTrackingTest(unittest.TestCase):
    def setUp(self):
        mobile_runtime.set_bridge(Bridge)
        mobile_runtime._set_phase(None)

    def service(self, seen):
        class Service:
            def search(self, **kw):
                seen.append(('search', mobile_runtime.current_phase()[0]))
                return []

            def reserve(self, *a, **kw):
                seen.append(('reserve', mobile_runtime.current_phase()[0]))
                return Result(True)

            def pay_with_card(self, *a, **kw):
                seen.append(('pay', mobile_runtime.current_phase()[0]))
                return Result(True)
        return Service()

    def test_reserve_then_payment_is_flagged_until_paid(self):
        seen = []
        s = self.service(seen)
        untrack = mobile_runtime._track_phases(s, {'auto_pay': True})
        s.search()
        s.reserve()
        # 좌석을 잡고 자동결제가 남았으면 결제가 끝날 때까지 'payment'
        self.assertEqual(mobile_runtime.current_phase()[0], 'payment')
        s.pay_with_card()
        self.assertIsNone(mobile_runtime.current_phase()[0])
        self.assertEqual(seen, [('search', None), ('reserve', 'reserve'), ('pay', 'payment')])
        untrack()
        # 인스턴스 덮어쓰기가 지워져 원래 메서드로 돌아온다
        self.assertNotIn('reserve', vars(s))

    def test_no_autopay_clears_after_reserve(self):
        s = self.service([])
        untrack = mobile_runtime._track_phases(s, None)
        s.reserve()
        self.assertIsNone(mobile_runtime.current_phase()[0])
        untrack()

    def test_next_search_clears_held_phase(self):
        s = self.service([])
        untrack = mobile_runtime._track_phases(s, {'auto_pay': True})
        s.reserve()
        s.search()
        self.assertIsNone(mobile_runtime.current_phase()[0])
        untrack()

    def test_reserve_error_clears_phase(self):
        class Boom:
            def reserve(self, *a, **kw):
                raise RuntimeError('x')
        s = Boom()
        untrack = mobile_runtime._track_phases(s, {'auto_pay': True})
        with self.assertRaises(RuntimeError):
            s.reserve()
        self.assertIsNone(mobile_runtime.current_phase()[0])
        untrack()


class SupervisedPhaseTest(unittest.TestCase):
    """감싼 매크로 루프 안에서는 phase 가 잡히고, 끝나면 반드시 풀린다."""

    def setUp(self):
        mobile_runtime.set_bridge(Bridge)
        self.original = reservation.run_reservation_loop
        mobile_runtime._supervise_macros()
        self.tg = TelegramService.get_instance()
        self.tg.set_macro_state(False)

    def tearDown(self):
        reservation.run_reservation_loop = self.original
        mobile_runtime._set_phase(None)

    def test_phase_cleared_when_loop_ends(self):
        seen = {}

        def fake_run(service, *a, **kw):
            service.reserve()
            seen['phase'] = mobile_runtime.current_phase()[0]
            return END_SUCCESS

        s = mock.Mock()
        s.credentials = {'user_id': 'u', 'password': 'p'}
        s.reserve.return_value = Result(True)
        with mock.patch.object(reservation, 'run_reservation', side_effect=fake_run):
            self.tg.try_start_macro(owner='u')
            reservation.run_reservation_loop(s, 'korail', [{'dep_date': '29991231'}],
                                             SeatOption.GENERAL_FIRST, {'auto_pay': True}, owner='u')
        self.assertEqual(seen['phase'], 'payment')
        self.assertIsNone(mobile_runtime.current_phase()[0])


class HealthAndHeartbeatTest(unittest.TestCase):
    def setUp(self):
        from flask import Flask
        mobile_runtime.set_bridge(Bridge)
        Bridge.calls = []
        self.app = Flask(__name__)
        mobile_runtime._add_health_route(self.app)
        mobile_runtime._add_battery_routes(self.app)
        self.client = self.app.test_client()
        self.tg = TelegramService.get_instance()

    def tearDown(self):
        mobile_runtime._set_phase(None)
        self.tg.set_macro_state(False)

    def test_health_reports_phase(self):
        self.tg.try_start_macro(owner='u')
        mobile_runtime._set_phase('payment')
        data = self.client.get('/__health').get_json()
        self.assertTrue(data['macro_running'])
        self.assertEqual(data['phase'], 'payment')
        mobile_runtime._set_phase(None)
        self.assertIsNone(self.client.get('/__health').get_json()['phase'])

    def test_push_log_is_a_heartbeat(self):
        tg = mock.Mock()
        tg._macro_running = False
        tg._macro_info = {}
        with mock.patch.object(TelegramService, 'get_instance', return_value=tg), \
                mock.patch.object(mobile_runtime, '_supervise_macros'), \
                mock.patch.object(reservation, 'add_macro_listener'):
            mobile_runtime._wire_platform()
        mobile_runtime._last_progress['at'] = time.monotonic() - 500
        tg.push_log('log', '재로그인 대기 중')
        self.assertLess(time.monotonic() - mobile_runtime._last_progress['at'], 5)

    def test_battery_status_and_request(self):
        Bridge.exempt = False
        data = self.client.get('/__app/battery').get_json()
        self.assertEqual(data['exempt'], False)
        self.assertTrue(data['supported'])
        self.assertEqual(self.client.post('/__app/battery').status_code, 200)
        self.assertIn(('ask_battery', None), Bridge.calls)
        # 다른 사이트에서 온 요청은 거절
        resp = self.client.post('/__app/battery', headers={'Sec-Fetch-Site': 'cross-site'})
        self.assertEqual(resp.status_code, 403)

    def test_battery_route_404_without_bridge_support(self):
        class Plain:
            pass
        mobile_runtime.set_bridge(Plain)
        self.assertEqual(self.client.get('/__app/battery').status_code, 404)
        mobile_runtime.set_bridge(Bridge)


if __name__ == '__main__':
    unittest.main(verbosity=2)
