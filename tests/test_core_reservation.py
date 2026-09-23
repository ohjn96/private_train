# -*- coding: utf-8 -*-
"""core.reservation 은 Flask·전역 상태 없이 돈다. 서버에서 사용자별로 따로 돌릴 수 있어야 한다."""
import os
import sys
import threading
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from core.base_service import SeatOption
from core.reservation import run_reservation
from test_reservation_flow import FakeTrain, FastRateLimit, as_selected, make_service


class RecordingReporter:
    """Reporter 프로토콜을 만족하는 가장 단순한 구현. 받은 로그만 모은다."""

    def __init__(self):
        self.logs = []
        self.running = None

    def clear_logs(self):
        self.logs.clear()

    def push_log(self, event_type, message, **extra):
        self.logs.append((event_type, message))

    def set_macro_state(self, running, info=None):
        self.running = running

    def update_attempt(self, attempt):
        pass

    def send_message(self, text):
        return True

    def send_macro_started(self, train_count, trains_summary):
        pass

    def send_macro_stopped(self):
        pass

    def send_reservation_success(self, **info):
        pass

    def send_payment_result(self, success, message):
        return True

    def kinds(self, event_type):
        return [m for t, m in self.logs if t == event_type]


def never_recover():
    return False, '복구 안 함'


class CoreReservationTest(unittest.TestCase):
    def test_runs_without_flask_and_stops_on_success(self):
        trains = [FakeTrain(0, has_seat=True)]
        service = make_service(trains)
        reporter = RecordingReporter()
        with FastRateLimit():
            run_reservation(
                service, [as_selected(trains[0], 0)], SeatOption.GENERAL_FIRST, None,
                reporter=reporter, should_stop=lambda: False, recover=never_recover,
            )
        self.assertEqual(len(service._client.kinds('reserve')), 1)
        self.assertTrue(reporter.kinds('success'))
        # 실행 슬롯은 core 가 아니라 부르는 쪽이 정리를 마친 뒤 맨 마지막에 푼다
        self.assertTrue(reporter.running)

    def test_two_users_do_not_share_stop_or_logs(self):
        """한 사람이 멈춰도 다른 사람의 매크로는 계속 돈다."""
        trains = [FakeTrain(0, has_seat=False)]
        stops = {'a': threading.Event(), 'b': threading.Event()}
        reporters = {'a': RecordingReporter(), 'b': RecordingReporter()}
        threads = {}
        with FastRateLimit():
            for who in ('a', 'b'):
                threads[who] = threading.Thread(
                    target=run_reservation,
                    args=(make_service(trains), [as_selected(trains[0], 0)],
                          SeatOption.GENERAL_FIRST, None),
                    kwargs=dict(reporter=reporters[who], should_stop=stops[who].is_set,
                                recover=never_recover),
                    daemon=True,
                )
                threads[who].start()

            stops['a'].set()
            threads['a'].join(timeout=5)
            self.assertFalse(threads['a'].is_alive(), 'a 가 멈추지 않았다')
            self.assertTrue(threads['b'].is_alive(), 'a 를 멈췄는데 b 도 멈췄다')

            stops['b'].set()
            threads['b'].join(timeout=5)

        self.assertTrue(reporters['a'].kinds('stopped'))
        self.assertTrue(reporters['b'].kinds('stopped'))


if __name__ == '__main__':
    unittest.main(verbosity=2)


class CallIntervalTest(unittest.TestCase):
    def test_clamp(self):
        from core.rate_limit import clamp_call_interval, DEFAULT_MIN_INTERVAL
        self.assertEqual(clamp_call_interval('2'), 2.0)
        self.assertEqual(clamp_call_interval(0.2), 1.0)
        self.assertEqual(clamp_call_interval(10), 3.0)
        self.assertEqual(clamp_call_interval('abc'), DEFAULT_MIN_INTERVAL)
        self.assertEqual(clamp_call_interval(float('nan')), DEFAULT_MIN_INTERVAL)

    def test_limiter_never_below_one_second(self):
        from core.rate_limit import RateLimiter
        limiter = RateLimiter(2.0)
        limiter.set_interval(0.1)
        self.assertEqual(limiter.min_interval, 1.0)

    def test_run_uses_chosen_interval_then_restores(self):
        from core.rate_limit import RateLimiter
        trains = [FakeTrain(0, has_seat=True)]
        service = make_service(trains)
        service._limiter = RateLimiter(2.0)
        seen = []
        original_search = service.search

        def spy_search(**kw):
            seen.append(service.call_interval)
            service._limiter._next_allowed = 0.0  # 테스트가 실제로 기다리지 않게
            return original_search(**kw)

        service.search = spy_search
        reporter = RecordingReporter()
        run_reservation(
            service, [as_selected(trains[0], 0)], SeatOption.GENERAL_FIRST, None,
            reporter=reporter, should_stop=lambda: False, recover=never_recover,
            call_interval=3,
        )
        self.assertEqual(seen, [3.0])
        self.assertEqual(service.call_interval, 2.0, '끝나면 원래 간격으로')
        self.assertIn('호출 간격 3초', reporter.logs[0][1])

    def test_services_can_have_separate_limiters(self):
        """서버에서 사용자마다 간격을 따로 가질 수 있다."""
        from core.korail_service import KorailService
        from core.rate_limit import RateLimiter, korail_api
        a, b = KorailService(RateLimiter(1.0)), KorailService(RateLimiter(3.0))
        self.assertEqual((a.call_interval, b.call_interval), (1.0, 3.0))
        self.assertIs(KorailService()._limiter, korail_api, '기본은 전역 게이트 (데스크톱)')


class WebCallIntervalTest(unittest.TestCase):
    def setUp(self):
        from webui import create_app
        from test_reservation_flow import sign_in
        self.client = create_app().test_client()
        sign_in(self.client, 'tester')

    def chosen(self, value):
        self.client.post('/reserve_select', data={'train_indices[]': '0', 'call_interval': value})
        with self.client.session_transaction() as sess:
            return sess['search_state']['korail']['call_interval']

    def test_choice_is_stored(self):
        self.assertEqual(self.chosen('1.5'), 1.5)

    def test_out_of_range_is_clamped(self):
        self.assertEqual(self.chosen('0.1'), 1.0)
        self.assertEqual(self.chosen('99'), 3.0)


# ---------------------------------------------------------------- 검토에서 찾은 버그 재발 방지

from unittest import mock

from korail2 import NeedToLoginError
from korail2.korail2 import ReservedOnly


class SessionExpiryTest(unittest.TestCase):
    """세션이 만료되면(P058) 예약 실패로 삼키지 말고 다시 로그인한다."""

    def run_with(self, recover_results, expired_reserves=1):
        trains = [FakeTrain(0, has_seat=True)]
        service = make_service(trains)
        calls = {'reserve': 0}
        original = service._client.reserve

        def reserve(*a, **kw):
            calls['reserve'] += 1
            if calls['reserve'] <= expired_reserves:
                raise NeedToLoginError()
            return original(*a, **kw)

        service._client.reserve = reserve
        results = iter(recover_results)
        recovered = []

        def recover():
            ok = next(results, False)
            recovered.append(ok)
            return ok, '재로그인 성공' if ok else '재로그인 실패'

        reporter = RecordingReporter()
        with FastRateLimit(), mock.patch('core.reservation._sleep_unless_stopped'):
            run_reservation(service, [as_selected(trains[0], 0)], SeatOption.GENERAL_FIRST, None,
                            reporter=reporter, should_stop=lambda: False, recover=recover)
        return reporter, recovered, calls

    def test_expired_session_is_recovered_then_reserved(self):
        reporter, recovered, calls = self.run_with([True])
        self.assertEqual(recovered, [True])
        self.assertEqual(calls['reserve'], 2)
        # 재로그인은 "success" 로그가 아니다 (화면이 예약 성공으로 착각한다)
        self.assertEqual(len(reporter.kinds('success')), 1)
        self.assertTrue(reporter.kinds('success')[0].startswith('예약 성공'))

    def test_gives_up_after_max_attempts_instead_of_spinning(self):
        from core.reservation import MAX_RECOVERY_ATTEMPTS
        reporter, recovered, _ = self.run_with([False] * 50, expired_reserves=10**6)
        self.assertEqual(len(recovered), MAX_RECOVERY_ATTEMPTS)
        self.assertTrue(reporter.kinds('stopped'))


class ReservedButDetailsFailedTest(unittest.TestCase):
    def test_reservation_counts_even_if_lookup_failed(self):
        """예약번호를 받았으면 성공이다. 목록 조회 실패로 다른 열차를 또 예약하면 안 된다."""
        trains = [FakeTrain(0, has_seat=True), FakeTrain(1, has_seat=True)]
        service = make_service(trains)
        service._client.reserve = lambda *a, **kw: ReservedOnly('PNR-1')
        reporter = RecordingReporter()
        with FastRateLimit():
            run_reservation(service, [as_selected(trains[0], 0), as_selected(trains[1], 1)],
                            SeatOption.GENERAL_FIRST, {'card_number': 'x', 'card_password': 'x',
                                                       'validation_number': 'x', 'card_expire': 'x'},
                            reporter=reporter, should_stop=lambda: False, recover=never_recover)
        self.assertEqual(len(reporter.kinds('success')), 1, '한 편만 예약해야 한다')
        self.assertTrue(any('코레일 앱에서 결제' in m for m in reporter.kinds('error')))


class SearchErrorsTest(unittest.TestCase):
    def test_first_page_failure_is_not_hidden_as_no_trains(self):
        service = make_service([FakeTrain(0)])
        service._client.search_train = mock.Mock(side_effect=ConnectionError('차단'))
        with FastRateLimit(), self.assertRaises(ConnectionError):
            service.search(dep='서울', arr='부산', date='20260923', time='060000')

    def test_same_minute_train_on_next_page_is_kept(self):
        """1페이지 마지막과 같은 분에 출발하는 열차가 2페이지로 밀려도 놓치지 않는다."""
        trains = [FakeTrain(i) for i in range(10)]
        twin = FakeTrain(9)
        twin.train_no = '999'  # 9번과 같은 시각
        trains.append(twin)
        trains.append(FakeTrain(12))
        service = make_service(trains)
        with FastRateLimit():
            got = service.search(dep='서울', arr='부산', date='20260923', time='060000',
                                 until_time=trains[-1].dep_time)
        self.assertIn('999', [t.train_number for t in got])


class ReserveGapTest(unittest.TestCase):
    def test_back_to_back_reserves_keep_one_second_rule(self):
        import core.rate_limit as rl
        limiter = rl.RateLimiter(0.01)
        with mock.patch.object(rl, 'HARD_MIN_INTERVAL', 0.2):
            import time as _t
            t0 = _t.monotonic()
            limiter.note_call()
            limiter.note_call()
            self.assertGreaterEqual(_t.monotonic() - t0, 0.19)


class StopBeforeThreadStartsTest(unittest.TestCase):
    def test_stop_pressed_before_loop_runs_is_honored(self):
        """시작 직후(스레드가 돌기 전) 누른 중단이 지워지면 안 된다."""
        import webui.routes.reservation as reservation
        from webui.services.telegram_service import TelegramService
        trains = [FakeTrain(0)]
        service = make_service(trains)
        tg = TelegramService.get_instance()
        tg.set_macro_state(False)
        tg.try_start_macro()
        reservation.STOP_MACRO = True
        with FastRateLimit():
            reservation.run_reservation_loop(service, 'korail', [as_selected(trains[0], 0)],
                                             SeatOption.GENERAL_FIRST, None)
        self.assertEqual(len(service._client.kinds('search')), 0)
        self.assertFalse(tg._macro_running)
