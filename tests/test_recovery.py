# -*- coding: utf-8 -*-
"""자동 복구: 끝난 이유, 일시적 재로그인 실패, 마지막 결과, 폰의 작업 저장·재시작."""
import json
import os
import sys
import types
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

import webui.routes.reservation as reservation
from core.base_service import SeatOption
from core.reservation import END_GAVE_UP, END_STOPPED, END_SUCCESS, run_reservation
from korail2 import NeedToLoginError
from test_core_reservation import RecordingReporter, never_recover
from test_reservation_flow import FakeTrain, FastRateLimit, as_selected, make_service
from webui.services.telegram_service import TelegramService


class EndReasonTest(unittest.TestCase):
    def run_loop(self, trains, **kw):
        service = make_service(trains)
        with FastRateLimit(), mock.patch('core.reservation._sleep_unless_stopped'):
            return run_reservation(service, [as_selected(trains[0], 0)], SeatOption.GENERAL_FIRST, None,
                                   reporter=RecordingReporter(), **kw), service

    def test_success(self):
        reason, _ = self.run_loop([FakeTrain(0, has_seat=True)], should_stop=lambda: False,
                                  recover=never_recover)
        self.assertEqual(reason, END_SUCCESS)

    def test_stopped(self):
        reason, _ = self.run_loop([FakeTrain(0)], should_stop=lambda: True, recover=never_recover)
        self.assertEqual(reason, END_STOPPED)

    def test_gave_up(self):
        trains = [FakeTrain(0, has_seat=True)]
        service = make_service(trains)
        service._client.reserve = mock.Mock(side_effect=NeedToLoginError())
        with FastRateLimit(), mock.patch('core.reservation._sleep_unless_stopped'):
            reason = run_reservation(service, [as_selected(trains[0], 0)], SeatOption.GENERAL_FIRST, None,
                                     reporter=RecordingReporter(), should_stop=lambda: False,
                                     recover=never_recover)
        self.assertEqual(reason, END_GAVE_UP)


class TemporaryRecoveryFailureTest(unittest.TestCase):
    def test_network_failures_do_not_count_toward_giving_up(self):
        """인터넷이 끊겨 재로그인이 실패한 건 포기 횟수에 넣지 않는다."""
        from core.reservation import MAX_RECOVERY_ATTEMPTS
        trains = [FakeTrain(0, has_seat=True)]
        service = make_service(trains)
        calls = {'n': 0}
        original = service._client.reserve

        def reserve(*a, **kw):
            calls['n'] += 1
            if calls['n'] == 1:
                raise NeedToLoginError()
            return original(*a, **kw)

        service._client.reserve = reserve
        answers = iter([(None, '인터넷')] * (MAX_RECOVERY_ATTEMPTS + 3) + [(True, '재로그인 성공')])
        with FastRateLimit(), mock.patch('core.reservation._sleep_unless_stopped'):
            reason = run_reservation(service, [as_selected(trains[0], 0)], SeatOption.GENERAL_FIRST, None,
                                     reporter=RecordingReporter(), should_stop=lambda: False,
                                     recover=lambda: next(answers))
        self.assertEqual(reason, END_SUCCESS)

    def test_attempt_recovery_reports_network_errors_as_temporary(self):
        service = mock.Mock()
        service.login.side_effect = requests.ConnectionError('끊김')
        ok, message = reservation.attempt_recovery('korail', service, {'user_id': 'u', 'password': 'p'})
        self.assertIsNone(ok)
        self.assertIn('인터넷', message)


class LastResultTest(unittest.TestCase):
    def setUp(self):
        self.tg = TelegramService.get_instance()
        self.tg.set_macro_state(False)
        self.tg.last_result = None

    def test_success_result_is_recorded(self):
        trains = [FakeTrain(0, has_seat=True)]
        service = make_service(trains)
        self.tg.try_start_macro(owner='me')
        reservation.STOP_MACRO = False
        with FastRateLimit():
            reason = reservation.run_reservation_loop(service, 'korail', [as_selected(trains[0], 0)],
                                                      SeatOption.GENERAL_FIRST, None, owner='me')
        self.assertEqual(reason, END_SUCCESS)
        result = self.tg.last_result
        self.assertEqual(result['reason'], END_SUCCESS)
        self.assertIn('KTX', result['reserved'])
        self.assertIsNone(result['paid'])  # 카드 없음 → 결제는 사용자 몫
        self.assertTrue(result['ended_at'])
        self.assertIn('last_result', self.tg.get_status())


class FakeBridge:
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


class AndroidSupervisorTest(unittest.TestCase):
    """폰 앱의 작업 저장·삭제·오류 시 재시작 (안드로이드 연결부는 가짜로)."""

    @classmethod
    def setUpClass(cls):
        root = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
        sys.path.insert(0, os.path.join(root, 'mobile', 'app', 'src', 'main', 'python'))
        java = types.ModuleType('java')
        java.jclass = lambda name: FakeBridge
        sys.modules['java'] = java
        import android_main
        cls.android_main = android_main

    def setUp(self):
        FakeBridge.calls = []
        self.original = reservation.run_reservation_loop
        self.android_main._crash_times.clear()
        self.android_main._supervise_macros()
        self.tg = TelegramService.get_instance()
        self.tg.set_macro_state(False)

    def tearDown(self):
        reservation.run_reservation_loop = self.original

    def service(self):
        s = mock.Mock()
        s.credentials = {'user_id': 'u', 'password': 'p'}
        return s

    def run_supervised(self, reason):
        with mock.patch.object(reservation, 'run_reservation', return_value=reason):
            self.tg.try_start_macro(owner='u')
            return reservation.run_reservation_loop(self.service(), 'korail', [{'dep_date': '29991231'}],
                                                    SeatOption.GENERAL_FIRST, None, owner='u')

    def test_job_saved_then_cleared_on_normal_end(self):
        for reason in (END_SUCCESS, END_STOPPED, END_GAVE_UP):
            FakeBridge.calls = []
            self.run_supervised(reason)
            kinds = [c[0] for c in FakeBridge.calls]
            self.assertEqual(kinds, ['save', 'clear'], reason)
            self.assertEqual(FakeBridge.calls[0][1]['password'], 'p')

    def test_crash_restarts_with_same_job_but_not_forever(self):
        with mock.patch.object(self.android_main.threading, 'Timer') as timer:
            for _ in range(self.android_main.CRASH_LIMIT):
                self.run_supervised('crash')
            self.assertEqual(timer.call_count, self.android_main.CRASH_LIMIT)
            self.assertNotIn(('clear', None), FakeBridge.calls)
            # 한도를 넘으면 더 되살리지 않고 작업을 지운다
            self.run_supervised('crash')
            self.assertEqual(timer.call_count, self.android_main.CRASH_LIMIT)
            self.assertEqual(FakeBridge.calls[-1], ('clear', None))

    def test_stale_job_is_not_resumed(self):
        self.assertTrue(self.android_main._job_is_stale({'trains': [{'dep_date': '20000101'}]}))
        self.assertFalse(self.android_main._job_is_stale({'trains': [{'dep_date': '29991231'}]}))


if __name__ == '__main__':
    unittest.main(verbosity=2)


class PaymentGuideTest(unittest.TestCase):
    """예약 성공 뒤 결제 안내: 자동결제 완료 / 실패(직접 결제) / 안 함(직접 결제)."""

    def test_deadline_format(self):
        from datetime import datetime, timedelta
        from core.reservation import payment_deadline
        today = datetime.now().strftime('%Y%m%d')
        other = (datetime.now() + timedelta(days=3))
        r = types.SimpleNamespace(buy_limit_date=today, buy_limit_time='142500')
        self.assertEqual(payment_deadline(r), '14:25')
        r = types.SimpleNamespace(buy_limit_date=other.strftime('%Y%m%d'), buy_limit_time='090000')
        self.assertEqual(payment_deadline(r), f'{other.month}월 {other.day}일 09:00')
        self.assertIsNone(payment_deadline(None))
        self.assertIsNone(payment_deadline(types.SimpleNamespace(buy_limit_date='', buy_limit_time='')))

    def notified(self, card, pay_result):
        from core.reservation import NotifyingReporter
        sent = []
        trains = [FakeTrain(0, has_seat=True)]
        service = make_service(trains)
        reporter = NotifyingReporter(RecordingReporter(), lambda k, t, b: sent.append((k, t, b)))
        with FastRateLimit():
            run_reservation(service, [as_selected(trains[0], 0)], SeatOption.GENERAL_FIRST, card,
                            reporter=reporter, should_stop=lambda: False, recover=never_recover,
                            pay=lambda *a: pay_result)
        return sent, reporter

    CARD = {'card_number': 'x', 'card_password': 'x', 'validation_number': 'x', 'card_expire': 'x'}

    def test_no_autopay_tells_to_pay_manually(self):
        sent, reporter = self.notified(None, None)
        self.assertEqual([k for k, _, _ in sent], ['reserved'])
        self.assertIn('직접 결제', sent[0][2])
        self.assertFalse(reporter.autopay)

    def test_autopay_success(self):
        sent, _ = self.notified(self.CARD, (True, 'ok'))
        self.assertEqual([k for k, _, _ in sent], ['reserved', 'paid'])
        self.assertIn('자동결제', sent[0][2])
        self.assertIn('끝났어요', sent[1][2])

    def test_autopay_failure_is_an_exception_telling_to_pay_manually(self):
        sent, _ = self.notified(self.CARD, (False, '카드 한도 초과'))
        kinds = [k for k, _, _ in sent]
        self.assertEqual(kinds, ['reserved', 'pay_failed'])
        title, body = sent[1][1], sent[1][2]
        self.assertIn('실패', title)
        self.assertIn('카드 한도 초과', body)
        self.assertIn('직접 결제', body)

    def test_last_result_carries_payment_outcome(self):
        tg = TelegramService.get_instance()
        tg.set_macro_state(False)
        trains = [FakeTrain(0, has_seat=True)]
        service = make_service(trains)
        tg.try_start_macro(owner='me')
        reservation.STOP_MACRO = False
        with FastRateLimit(), mock.patch.object(reservation, 'attempt_payment',
                                                return_value=(False, '카드 한도 초과')), \
                mock.patch('core.reservation.attempt_payment', return_value=(False, '카드 한도 초과')):
            reservation.run_reservation_loop(service, 'korail', [as_selected(trains[0], 0)],
                                             SeatOption.GENERAL_FIRST, self.CARD, owner='me')
        result = tg.last_result
        self.assertTrue(result['autopay'])
        self.assertIs(result['paid'], False)
