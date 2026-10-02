# -*- coding: utf-8 -*-
"""현장에서 겪을 만한 문제들: 다른 기기 로그인 핑퐁, 코레일 차단·속도 제한, 이미 떠난 열차,
예약 응답 끊김(중복 예약), 한국 시간, 예약 뒤 알림·결제 오류, 호출 간격.

코레일에 붙지 않는다. 가짜 클라이언트와 빠른 시계만 쓴다 (0.1초 넘게 자지 않는다).
"""
import importlib
import json
import os
import sys
import threading
import time
import types
import unittest
from datetime import datetime, timedelta, timezone
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import requests

import core.reservation as cr
from core.base_service import ReservationResult, SeatOption
from core.clock import KST, all_departed, train_departed
from core.korail_service import KorailService
from core.rate_limit import RateLimiter
from core.reservation import (
    BLOCK_NOTIFY_AFTER, DEPARTED_MESSAGE, END_STOPPED, END_SUCCESS, KICKED_MESSAGE,
    NotifyingReporter, block_backoff, payment_deadline, run_reservation,
)
from korail2 import KorailBlockedError, NeedToLoginError, SoldOutError
from korail2 import korail2 as k2
from test_core_reservation import RecordingReporter
from test_reservation_flow import FakeTrain, as_selected

#: 루프 안의 time.sleep 을 짧게 (monotonic 은 진짜)
FAST_TIME = types.SimpleNamespace(sleep=lambda s: time.sleep(min(s, 0.005)), monotonic=time.monotonic)

CARD = dict(card_number='1', card_password='12', validation_number='900101', card_expire='2912', auto_pay=True)


class FakeReservation:
    def __init__(self, rsv_id, train_no='100', dep_time='060000', dep_date='20990923',
                 day='20990923', hms='142500'):
        self.rsv_id = rsv_id
        self.train_no = train_no
        self.dep_time = dep_time
        self.dep_date = dep_date
        self.buy_limit_date = day
        self.buy_limit_time = hms


class ScriptClient:
    """search_train / reserve / reservations 가 차례로 script 를 따르는 가짜 코레일 클라이언트."""

    def __init__(self, trains, search_script=(), reserve_script=(), reservations_script=()):
        self.trains = trains
        self.search_script = list(search_script)
        self.reserve_script = list(reserve_script)
        self.reservations_script = list(reservations_script)
        self.calls = []
        self.logined = True

    @staticmethod
    def _step(script):
        if not script:
            return None
        step = script.pop(0)
        if isinstance(step, BaseException) or (isinstance(step, type) and issubclass(step, BaseException)):
            raise step
        return step

    def search_train(self, **kw):
        self.calls.append(('search', kw['time']))
        step = self._step(self.search_script)
        if callable(step):
            step(self)
        return list(self.trains)

    def reserve(self, train, passengers=None, option=None):
        self.calls.append(('reserve', train.train_no, passengers[0].count))
        step = self._step(self.reserve_script)
        return step if step is not None else FakeReservation('RSV-' + train.train_no, train.train_no, train.dep_time)

    def reservations(self):
        self.calls.append(('reservations',))
        step = self._step(self.reservations_script)
        return step if step is not None else []

    def kinds(self, k):
        return [c for c in self.calls if c[0] == k]


def make_svc(client):
    limiter = RateLimiter(1.0)
    limiter._min_interval = 0.001  # 테스트가 1초씩 기다리지 않게 (하한 우회)
    service = KorailService(limiter=limiter)
    service._client = client
    return service


def run(service, selected, card=None, reporter=None, recover=None, stop_after=None, sleeps=None, **kw):
    """예약 루프를 돌린다. _sleep_unless_stopped 는 잠들지 않고 요청된 시간만 기록한다."""
    reporter = reporter or RecordingReporter()
    sleeps = sleeps if sleeps is not None else []
    start = time.monotonic()
    stop = (lambda: time.monotonic() - start > stop_after) if stop_after else (lambda: False)
    with mock.patch.object(cr, 'time', FAST_TIME), \
            mock.patch.object(cr, '_sleep_unless_stopped', lambda s, _stop: sleeps.append(s)):
        reason = run_reservation(service, selected, SeatOption.GENERAL_FIRST, card,
                                 reporter=reporter, should_stop=stop,
                                 recover=recover or (lambda: (True, '재로그인 성공')), **kw)
    return reason, reporter


class AlertingReporter(RecordingReporter):
    def __init__(self):
        super().__init__()
        self.alerts = []

    def send_alert(self, title, body):
        self.alerts.append((title, body))


# ---------------------------------------------------------------- 1. 다른 기기 로그인 핑퐁

class ForcedLogoutPingPongTest(unittest.TestCase):
    def run_kicks(self, kicks, clock_step):
        """조회가 kicks 번 P058 로 튕긴 뒤 좌석을 잡는다. 튕길 때마다 시계를 clock_step 초 민다."""
        t = FakeTrain(0, has_seat=True)
        client = ScriptClient([t], search_script=[NeedToLoginError('P058')] * kicks)
        now = [1000.0]
        recovered = []

        def recover():
            recovered.append(1)
            now[0] += clock_step
            return True, '재로그인 성공'

        sleeps = []
        reporter = AlertingReporter()
        with mock.patch.object(cr, '_clock', lambda: now[0]):
            reason, _ = run(make_svc(client), [as_selected(t, 0)], reporter=reporter,
                            recover=recover, sleeps=sleeps)
        return reason, reporter, sleeps, recovered

    def test_three_kicks_in_ten_minutes_pause_and_notify(self):
        reason, reporter, sleeps, recovered = self.run_kicks(3, clock_step=60)
        self.assertEqual(reason, END_SUCCESS)
        pauses = [s for s in sleeps if s >= 300]
        self.assertEqual(len(pauses), 1, sleeps)
        self.assertLessEqual(pauses[0], 600)
        self.assertEqual(reporter.alerts, [('⚠️ 다른 기기에서 로그인했어요', KICKED_MESSAGE)])
        self.assertTrue(any(KICKED_MESSAGE in m for m in reporter.kinds('warning')))
        self.assertEqual(len(recovered), 3, '쉬고 나서 다시 로그인은 한다')

    def test_kicks_spread_out_do_not_pause(self):
        reason, reporter, sleeps, _ = self.run_kicks(4, clock_step=11 * 60)
        self.assertEqual(reason, END_SUCCESS)
        self.assertFalse([s for s in sleeps if s >= 300])
        self.assertEqual(reporter.alerts, [])

    def test_pause_is_interruptible(self):
        t = FakeTrain(0, has_seat=True)
        client = ScriptClient([t], search_script=[NeedToLoginError('P058')] * 99)
        stop = threading.Event()
        recover_calls = []

        def recover():
            recover_calls.append(1)
            return True, 'ok'

        th = threading.Thread(target=lambda: run_reservation(
            make_svc(client), [as_selected(t, 0)], SeatOption.GENERAL_FIRST, None,
            reporter=RecordingReporter(), should_stop=stop.is_set, recover=recover), daemon=True)
        with mock.patch.object(cr, 'time', FAST_TIME):
            th.start()
            deadline = time.monotonic() + 5
            while len(recover_calls) < 2 and time.monotonic() < deadline:
                time.sleep(0.01)
            time.sleep(0.05)  # 세 번째 튕김 → 긴 쉬기에 들어간다
            stop.set()
            th.join(3)
        self.assertFalse(th.is_alive(), '쉬는 중에도 중단은 바로 먹어야 한다')
        self.assertEqual(len(recover_calls), 2, '쉬는 동안 또 로그인하지 않는다')

    def test_notifying_reporter_forwards_alert_everywhere(self):
        sent = []
        inner = RecordingReporter()
        inner.send_message = mock.Mock(return_value=True)
        rep = NotifyingReporter(inner, lambda *a: sent.append(a))
        cr._alert(rep, '제목', '본문')
        inner.send_message.assert_called_once_with('제목\n본문')
        self.assertEqual(sent, [('warning', '제목', '본문')])


# ---------------------------------------------------------------- 2. 차단·속도 제한

class Resp:
    def __init__(self, text, status=200):
        self.text = text
        self.status_code = status


class KorailBlockedResponseTest(unittest.TestCase):
    def setUp(self):
        self.k = k2.Korail('12345678', 'pw', auto_login=False)

    def test_rate_limited_and_forbidden(self):
        for status in (429, 403):
            with self.assertRaises(KorailBlockedError) as cm:
                self.k._json(Resp('{"strResult": "SUCC"}', status))
            self.assertEqual(cm.exception.status, status)

    def test_html_page(self):
        with self.assertRaises(KorailBlockedError):
            self.k._json(Resp('<html>서비스 점검 중</html>'))

    def test_missing_str_result(self):
        with self.assertRaises(KorailBlockedError):
            self.k._result_check({'code': 'X', 'message': '비정상 접근'})

    def test_normal_failure_is_not_blocked(self):
        with self.assertRaises(SoldOutError):
            self.k._result_check({'strResult': 'FAIL', 'h_msg_cd': 'ERR211161', 'h_msg_txt': ''})

    def test_search_raises_blocked_even_on_later_page(self):
        trains = [FakeTrain(i) for i in range(12)]
        client = ScriptClient(trains[:10], search_script=[None, KorailBlockedError('HTTP 429', '429')])
        with self.assertRaises(KorailBlockedError):
            make_svc(client).search('서울', '부산', '20990923', '060000', until_time=trains[-1].dep_time)


class BlockedBackoffTest(unittest.TestCase):
    def test_backoff_schedule(self):
        self.assertEqual([block_backoff(n) for n in range(1, 8)], [30, 60, 120, 240, 480, 600, 600])
        self.assertEqual(block_backoff(10 ** 6), 600)

    def test_backs_off_notifies_and_resets_on_success(self):
        t = FakeTrain(0, has_seat=False)

        def open_seat(c):
            c.trains[0]._has_seat = True

        blocked = KorailBlockedError('HTTP 429', '429', status=429)
        client = ScriptClient([t], search_script=[blocked] * 4 + [None, blocked, open_seat])
        sleeps = []
        reporter = AlertingReporter()
        reason, _ = run(make_svc(client), [as_selected(t, 0)], reporter=reporter, sleeps=sleeps)
        self.assertEqual(reason, END_SUCCESS)
        self.assertEqual(sleeps, [30, 60, 120, 240, 30], '성공하면 다시 30초부터')
        self.assertEqual(len(reporter.alerts), 1, f'{BLOCK_NOTIFY_AFTER}번 연달아 막히면 한 번 알린다')

    def test_blocked_reserve_is_not_swallowed(self):
        t = FakeTrain(0, has_seat=True)
        client = ScriptClient([t], reserve_script=[KorailBlockedError('HTTP 403', '403', status=403)])
        sleeps = []
        reason, _ = run(make_svc(client), [as_selected(t, 0)], sleeps=sleeps)
        self.assertEqual(reason, END_SUCCESS)
        self.assertEqual(sleeps, [30])

    def test_blocked_relogin_backs_off_instead_of_giving_up(self):
        t = FakeTrain(0, has_seat=True)
        client = ScriptClient([t], search_script=[NeedToLoginError('P058')] * 8)
        service = make_svc(client)
        answers = iter([(None, '막힘')] * 7 + [(True, 'ok')])

        def recover():
            ok, msg = next(answers)
            service.last_login_blocked = ok is None
            return ok, msg

        sleeps = []
        reason, _ = run(service, [as_selected(t, 0)], recover=recover, sleeps=sleeps)
        self.assertEqual(reason, END_SUCCESS)
        self.assertEqual(sleeps[:3], [30, 60, 120])


class RecoveryMessagesTest(unittest.TestCase):
    def test_html_during_relogin_is_temporary(self):
        from webui.routes.reservation import attempt_recovery
        s = make_svc(None)
        with mock.patch.object(requests.Session, 'request',
                               side_effect=lambda *a, **kw: Resp('<html>서비스 점검 중</html>')):
            ok, msg = attempt_recovery('korail', s, {'user_id': '12345678', 'password': 'pw'})
        self.assertIsNone(ok, msg)
        self.assertTrue(s.last_login_blocked)

    def test_key_error_during_relogin_is_temporary(self):
        from webui.routes.reservation import attempt_recovery
        s = make_svc(None)
        with mock.patch.object(KorailService, 'login', side_effect=KeyError('Key')):
            ok, _ = attempt_recovery('korail', s, {'user_id': 'u', 'password': 'p'})
        self.assertIsNone(ok)
        self.assertTrue(s.last_login_blocked)

    def test_rejected_login_reason_reaches_user(self):
        from webui.routes.reservation import attempt_recovery
        s = make_svc(None)
        seq = ['{"strResult":"SUCC","app.login.cphd":null}',
               json.dumps({'strResult': 'FAIL', 'h_msg_txt': '비밀번호 틀림'})]
        with mock.patch.object(requests.Session, 'request', side_effect=lambda *a, **kw: Resp(seq.pop(0))):
            ok, msg = attempt_recovery('korail', s, {'user_id': '12345678', 'password': 'pw'})
        self.assertFalse(ok)

        s = make_svc(None)
        seq = ['{"strResult":"SUCC","app.login.cphd":null}', json.dumps({'code': 'B', 'message': '계정 잠김'})]
        with mock.patch.object(requests.Session, 'request', side_effect=lambda *a, **kw: Resp(seq.pop(0))):
            ok, msg = attempt_recovery('korail', s, {'user_id': '12345678', 'password': 'pw'})
        self.assertFalse(ok)
        self.assertIn('계정 잠김', msg)


# ---------------------------------------------------------------- 3. 이미 떠난 열차

class DepartedTest(unittest.TestCase):
    def test_stops_when_every_train_has_left(self):
        t = FakeTrain(0, has_seat=False)
        client = ScriptClient([t])
        after = datetime(2099, 9, 23, 6, 1, tzinfo=KST)
        sent = []
        reporter = NotifyingReporter(RecordingReporter(), lambda *a: sent.append(a))
        with mock.patch.object(cr, 'now_kst', lambda: after):
            reason, _ = run(make_svc(client), [as_selected(t, 0)], reporter=reporter)
        self.assertEqual(reason, END_STOPPED)
        self.assertEqual(client.kinds('search'), [])
        self.assertEqual(sent, [('stopped', '⏹️ 예약 매크로 중단', DEPARTED_MESSAGE)])

    def test_stops_mid_run_when_last_train_leaves(self):
        a, b = FakeTrain(0), FakeTrain(1)   # 06:00, 06:10
        client = ScriptClient([a, b])
        clock = iter([datetime(2099, 9, 23, 5, 0, tzinfo=KST)] * 6 + [datetime(2099, 9, 23, 6, 10, tzinfo=KST)] * 99)
        with mock.patch.object(cr, 'now_kst', lambda: next(clock)):
            reason, rep = run(make_svc(client), [as_selected(a, 0), as_selected(b, 1)])
        self.assertEqual(reason, END_STOPPED)
        self.assertIn(DEPARTED_MESSAGE, rep.kinds('error'))
        self.assertGreaterEqual(len(client.kinds('search')), 1)

    def test_one_train_left_keeps_going(self):
        self.assertFalse(all_departed(
            [{'dep_date': '20990923', 'dep_time': '060000'}, {'dep_date': '20990923', 'dep_time': '070000'}],
            datetime(2099, 9, 23, 6, 30, tzinfo=KST)))


# ---------------------------------------------------------------- 4. 예약 응답 끊김 → 중복 예약 방지

class ReserveTimeoutTest(unittest.TestCase):
    def test_timeout_but_reserved_counts_as_success_and_pays(self):
        t = FakeTrain(0, has_seat=True)
        rsv = FakeReservation('PNR-1')
        client = ScriptClient([t], reserve_script=[requests.ReadTimeout('rt')], reservations_script=[[rsv]])
        paid = []
        sent = []
        reporter = NotifyingReporter(RecordingReporter(), lambda *a: sent.append(a))
        reason, _ = run(make_svc(client), [as_selected(t, 0)], card=CARD, reporter=reporter,
                        pay=lambda s, c, r: (paid.append(r.details['reservation']), (True, 'ok'))[1])
        self.assertEqual(reason, END_SUCCESS)
        self.assertEqual(len(client.kinds('reserve')), 1, '같은 열차를 또 예약하면 안 된다')
        self.assertEqual(paid, [rsv])
        self.assertEqual([k for k, *_ in sent], ['reserved', 'paid'])

    def test_timeout_and_not_reserved_retries(self):
        t = FakeTrain(0, has_seat=True)
        client = ScriptClient([t], reserve_script=[requests.ConnectionError('ce')], reservations_script=[[]])
        reason, _ = run(make_svc(client), [as_selected(t, 0)])
        self.assertEqual(reason, END_SUCCESS)
        self.assertEqual(len(client.kinds('reserve')), 2)

    def test_lookup_failure_checks_again_before_reserving(self):
        t = FakeTrain(0, has_seat=True)
        client = ScriptClient([t], reserve_script=[requests.ReadTimeout('rt')],
                              reservations_script=[requests.ReadTimeout('again'), [FakeReservation('PNR-2')]])
        reason, rep = run(make_svc(client), [as_selected(t, 0)])
        self.assertEqual(reason, END_SUCCESS)
        self.assertEqual(len(client.kinds('reserve')), 1)
        self.assertEqual(len(client.kinds('reservations')), 2)

    def test_other_train_in_list_is_not_a_match(self):
        t = FakeTrain(0, has_seat=True)
        other = FakeReservation('PNR-X', train_no='999')
        client = ScriptClient([t], reserve_script=[requests.ReadTimeout('rt')], reservations_script=[[other]])
        reason, _ = run(make_svc(client), [as_selected(t, 0)])
        self.assertEqual(reason, END_SUCCESS)
        self.assertEqual(len(client.kinds('reserve')), 2)

    def test_sequential_ignores_the_seat_already_taken(self):
        t = FakeTrain(0, has_seat=True)
        first = FakeReservation('RSV-100')
        client = ScriptClient([t], reserve_script=[first, requests.ReadTimeout('rt')],
                              reservations_script=[[first]])
        reason, _ = run(make_svc(client), [as_selected(t, 0)], passenger_count=2, sequential=True)
        self.assertEqual(reason, END_SUCCESS)
        self.assertEqual(len(client.kinds('reserve')), 3, '앞서 잡은 좌석을 두 번째 좌석으로 착각하면 안 된다')


# ---------------------------------------------------------------- 5. 한국 시간

class KoreanTimeTest(unittest.TestCase):
    def test_deadline_uses_korean_today_on_utc_host(self):
        # UTC 로 도는 서버에서 2099-09-22 15:30 UTC = 2099-09-23 00:30 KST
        utc_now = datetime(2099, 9, 22, 15, 30, tzinfo=timezone.utc)
        with mock.patch('core.clock.datetime') as dt:
            dt.now.side_effect = lambda tz=None: utc_now.astimezone(tz) if tz else utc_now.replace(tzinfo=None)
            got = payment_deadline(types.SimpleNamespace(buy_limit_date='20990923', buy_limit_time='005000'))
        self.assertEqual(got, '00:50')

    def test_departed_with_foreign_timezone_now(self):
        train = {'dep_date': '20990923', 'dep_time': '080000'}
        # 2099-09-22 23:30 UTC = 2099-09-23 08:30 KST → 떠났다
        self.assertTrue(train_departed(train, datetime(2099, 9, 22, 23, 30, tzinfo=timezone.utc)))
        # 2099-09-22 22:30 UTC = 07:30 KST → 아직
        self.assertFalse(train_departed(train, datetime(2099, 9, 22, 22, 30, tzinfo=timezone.utc)))
        # tz 없는 시각은 한국 시각으로 본다
        self.assertFalse(train_departed(train, datetime(2099, 9, 23, 7, 59)))

    def test_falls_back_to_fixed_utc9_without_tzdata(self):
        import core.clock as clock
        try:
            with mock.patch.dict(sys.modules, {'zoneinfo': None}):
                importlib.reload(clock)
                self.assertEqual(clock.KST.utcoffset(None), timedelta(hours=9))
                self.assertEqual(clock.now_kst().utcoffset(), timedelta(hours=9))
        finally:
            importlib.reload(clock)


# ---------------------------------------------------------------- 예약 뒤 알림·결제 오류

class AfterReserveErrorsTest(unittest.TestCase):
    def test_reporter_error_after_reserve_still_succeeds_once(self):
        t = FakeTrain(0, has_seat=True)
        client = ScriptClient([t])

        class Boom(RecordingReporter):
            def send_reservation_success(self, **info):
                raise RuntimeError('telegram 502')

        reason, _ = run(make_svc(client), [as_selected(t, 0)], reporter=Boom(), stop_after=2)
        self.assertEqual(reason, END_SUCCESS)
        self.assertEqual([(c[1], c[2]) for c in client.kinds('reserve')], [('100', 1)])

    def test_pay_error_is_reported_with_pay_guide(self):
        t = FakeTrain(0, has_seat=True)
        client = ScriptClient([t])
        sent = []
        reporter = NotifyingReporter(RecordingReporter(), lambda *a: sent.append(a))

        def pay(*a):
            raise RuntimeError('pay exploded')

        reason, _ = run(make_svc(client), [as_selected(t, 0)], card=CARD, pay=pay, reporter=reporter,
                        stop_after=2)
        self.assertEqual(reason, END_SUCCESS)
        self.assertEqual(len(client.kinds('reserve')), 1)
        kind, _, body = sent[-1]
        self.assertEqual(kind, 'pay_failed')
        self.assertIn('pay exploded', body)
        self.assertIn('직접 결제하세요', body)


# ---------------------------------------------------------------- 호출 간격

class ThrottleTest(unittest.TestCase):
    def test_reserve_follow_up_calls_go_through_throttle(self):
        k = k2.Korail('1', 'p', auto_login=False)
        k.throttle = mock.Mock()

        def request(method, url, **kw):
            if url == k2.KORAIL_TICKETRESERVATION:
                return Resp(json.dumps({'strResult': 'SUCC', 'h_pnr_no': 'PNR1'}))
            return Resp(json.dumps({'strResult': 'FAIL', 'h_msg_cd': 'P100', 'h_msg_txt': ''}))

        train = mock.Mock()
        train.has_seat.return_value = True
        train.has_general_seat.return_value = True
        with mock.patch.object(requests.Session, 'request', side_effect=request), mock.patch('builtins.print'):
            got = k.reserve(train)
        self.assertEqual(got.rsv_id, 'PNR1')
        self.assertEqual(k.throttle.call_count, 1, '예약 목록 조회는 게이트를 거친다 (예약 호출 자체는 아님)')

    def test_pay_does_not_double_wait_with_throttled_client(self):
        service = make_svc(mock.Mock(throttle=object()))
        service._limiter = mock.Mock()
        service.pay_with_card(object(), '1', '12', '900101', '2912')
        service._limiter.wait.assert_not_called()

    def test_wait_is_interruptible(self):
        limiter = RateLimiter(1.0)
        limiter.set_interval(5.0)
        limiter.wait()
        t0 = time.monotonic()
        self.assertEqual(limiter.wait(should_stop=lambda: True), -1.0)
        self.assertLess(time.monotonic() - t0, 0.1)

    def test_search_stops_between_pages(self):
        trains = [FakeTrain(i) for i in range(30)]
        client = ScriptClient(trains[:10])
        service = make_svc(client)
        service._limiter = RateLimiter(5.0)  # 다음 장은 5초 기다려야 한다
        service.should_stop = lambda: len(client.kinds('search')) >= 1
        t0 = time.monotonic()
        got = service.search('서울', '부산', '20990923', '060000', until_time=trains[-1].dep_time)
        self.assertLess(time.monotonic() - t0, 0.5)
        self.assertEqual(len(client.kinds('search')), 1)
        self.assertEqual(len(got), 10)

    def test_overlapping_runs_keep_the_later_interval(self):
        limiter = RateLimiter(2.0)
        a, b = KorailService(limiter=limiter), KorailService(limiter=limiter)
        t = FakeTrain(0)
        a._client, b._client = ScriptClient([t]), ScriptClient([t])
        stop_a, stop_b = threading.Event(), threading.Event()

        def go(service, interval, stop):
            with mock.patch.object(cr, 'time', FAST_TIME):
                run_reservation(service, [as_selected(t, 0)], SeatOption.GENERAL_FIRST, None,
                                reporter=RecordingReporter(), should_stop=stop.is_set,
                                recover=lambda: (True, ''), call_interval=interval)

        ta = threading.Thread(target=go, args=(a, 1, stop_a), daemon=True)
        ta.start()
        time.sleep(0.05)
        tb = threading.Thread(target=go, args=(b, 3, stop_b), daemon=True)
        tb.start()
        # B 의 간격 설정은 A 가 게이트에서 기다리는 동안(락을 쥔 채) 잠시 막힐 수 있다
        deadline = time.monotonic() + 3
        while limiter.min_interval != 3.0 and time.monotonic() < deadline:
            time.sleep(0.01)
        stop_a.set()
        ta.join(5)
        self.assertEqual(limiter.min_interval, 3.0, 'A 가 끝나도 아직 도는 B 의 간격을 되돌리지 않는다')
        stop_b.set()
        tb.join(5)
        self.assertFalse(ta.is_alive() or tb.is_alive())


# ---------------------------------------------------------------- 웹 쪽 마무리

class CrashDetailTest(unittest.TestCase):
    def test_crash_detail_is_the_error_itself(self):
        import webui.routes.reservation as wr
        from webui.services.telegram_service import TelegramService
        tg = TelegramService.get_instance()
        tg.set_macro_state(False)
        tg.try_start_macro(owner='u')
        wr.STOP_MACRO = False
        with mock.patch.object(wr, 'run_reservation', side_effect=RuntimeError('kaboom')):
            wr.run_reservation_loop(make_svc(ScriptClient([])), 'korail', [as_selected(FakeTrain(0), 0)],
                                    SeatOption.GENERAL_FIRST, None)
        self.assertEqual(tg.last_result['reason'], 'crash')
        self.assertEqual(tg.last_result['detail'], 'kaboom')
        tg.set_macro_state(False)


if __name__ == '__main__':
    unittest.main(verbosity=2)
