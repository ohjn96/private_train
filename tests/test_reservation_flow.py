# -*- coding: utf-8 -*-
"""예약 플로우 회귀 테스트.

코레일 서버에 붙지 않는다. 가짜 클라이언트를 꽂아 호출 순서/횟수/간격만 본다.

실행:
    python -m unittest discover -s tests -v
"""
import os
import sys
import threading
import time
import unittest

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from korail2 import NoResultsError, SoldOutError

import app.routes.reservation as reservation
from app.services.base_service import SeatOption
from app.services.korail_service import KorailService
from app.services.rate_limit import (
    DEFAULT_MIN_INTERVAL,
    RateLimiter,
    _configured_interval,
    korail_api,
)
from app.services.telegram_service import TelegramService

from support import valid_license_patch

# 이 모듈은 라우트를 직접 두드린다. 라이선스 게이트가 전부 막아버리므로
# 유효한 라이선스가 있는 상태로 고정해두고 돌린다.
_license_patch = None


def setUpModule():
    global _license_patch
    _license_patch = valid_license_patch()
    _license_patch.start()


def tearDownModule():
    if _license_patch is not None:
        _license_patch.stop()


# ---------------------------------------------------------------- 가짜 클라이언트

class FakeTrain:
    """10분 간격으로 늘어선 가짜 열차."""

    def __init__(self, i, has_seat=False):
        self.train_type_name = 'KTX'
        self.train_no = str(100 + i)
        self.dep_date = '20260923'
        self.dep_time = '%02d%02d00' % (6 + (i * 10) // 60, (i * 10) % 60)
        self.arr_date = '20260923'
        self.arr_time = self.dep_time
        self.dep_name = '서울'
        self.arr_name = '부산'
        self._has_seat = has_seat

    def has_general_seat(self):
        return self._has_seat

    def has_special_seat(self):
        return False

    def has_seat(self):
        return self._has_seat


class FakeClient:
    """search_train / reserve 호출을 시각과 함께 기록하는 가짜 코레일 클라이언트."""

    PAGE_SIZE = 10

    def __init__(self, trains, reserve_fails=False):
        self.trains = trains
        self.reserve_fails = reserve_fails
        self.calls = []  # (kind, detail, seat_count, monotonic)

    def reserve(self, train, passengers=None, option=None):
        count = passengers[0].count if passengers else None
        self.calls.append(('reserve', train.train_no, count, time.monotonic()))
        if self.reserve_fails:
            raise SoldOutError()

        class Reservation:
            rsv_id = 'RSV-' + train.train_no

        return Reservation()

    def kinds(self, kind):
        return [c for c in self.calls if c[0] == kind]


def _search_train(self, **kw):
    """코레일의 search_train 흉내: 요청 시각부터 10건씩 돌려준다."""
    start = kw['time']
    self.calls.append(('search', start, None, time.monotonic()))
    later = [i for i, t in enumerate(self.trains) if t.dep_time >= start]
    if not later:
        raise NoResultsError()
    page = self.trains[later[0]:later[0] + self.PAGE_SIZE]
    if not page:
        raise NoResultsError()
    return page


FakeClient.search_train = _search_train


def make_service(trains, reserve_fails=False):
    service = KorailService()
    service._client = FakeClient(trains, reserve_fails=reserve_fails)
    return service


def as_selected(train, index):
    return {
        'index': index,
        'train_name': train.train_type_name,
        'train_number': train.train_no,
        'dep_date': train.dep_date,
        'dep_time': train.dep_time,
        'dep_time_formatted': '',
        'arr_time_formatted': '',
        'dep_station': '서울',
        'arr_station': '부산',
    }


class FastRateLimit:
    """테스트가 실제로 1.5초씩 기다리지 않도록 간격을 줄였다 되돌리는 컨텍스트."""

    def __init__(self, interval=0.01):
        self.interval = interval

    def __enter__(self):
        self.original = korail_api._min_interval
        korail_api._min_interval = self.interval
        korail_api._next_allowed = 0.0
        return self

    def __exit__(self, *exc):
        korail_api._min_interval = self.original
        korail_api._next_allowed = 0.0


def run_macro(service, selected, timeout=6.0, **kwargs):
    """매크로를 돌리고 멈춘다. 성공하면 스스로 멈추고, 아니면 timeout 후 중단."""
    tg = TelegramService.get_instance()
    tg.set_macro_state(False)
    reservation.STOP_MACRO = False
    th = threading.Thread(
        target=reservation.run_reservation_loop,
        args=(service, 'korail', selected, SeatOption.GENERAL_FIRST, None),
        kwargs=kwargs,
        daemon=True,
    )
    th.start()
    th.join(timeout=timeout)
    reservation.STOP_MACRO = True
    th.join(timeout=5)
    tg.set_macro_state(False)


# ---------------------------------------------------------------- 조회 페이징

class SearchPagingTest(unittest.TestCase):
    def setUp(self):
        self.trains = [FakeTrain(i) for i in range(40)]

    def test_single_page_by_default(self):
        """until_time 없이 부르면 API 호출은 딱 한 번."""
        svc = make_service(self.trains)
        with FastRateLimit():
            got = svc.search(dep='서울', arr='부산', date='20260923', time='000000')
        self.assertEqual(len(svc._client.kinds('search')), 1)
        self.assertEqual(len(got), 10)

    def test_pages_until_requested_time(self):
        """until_time 까지 페이지를 넘긴다.

        회귀: 예전에는 파라미터 `time` 이 time 모듈을 가려서 2페이지째 진입 전에
        AttributeError 가 나고, 그게 except 에 삼켜져 늘 1페이지만 돌아왔다.
        """
        svc = make_service(self.trains)
        with FastRateLimit():
            got = svc.search(dep='서울', arr='부산', date='20260923', time='060000',
                             until_time=self.trains[20].dep_time)
        self.assertEqual(len(svc._client.kinds('search')), 3)
        self.assertEqual(len(got), 30)
        self.assertIn(self.trains[20].train_no, [t.train_number for t in got])

    def test_max_pages_is_capped(self):
        svc = make_service(self.trains)
        with FastRateLimit():
            svc.search(dep='서울', arr='부산', date='20260923', time='060000',
                       until_time='235959')
        self.assertLessEqual(len(svc._client.kinds('search')), 5)


# ---------------------------------------------------------------- 호출 간격

class RateLimitTest(unittest.TestCase):
    def test_default_is_1_5_seconds(self):
        self.assertEqual(DEFAULT_MIN_INTERVAL, 1.5)

    def test_never_goes_below_one_second(self):
        os.environ['KORAIL_MIN_API_INTERVAL'] = '0.1'
        try:
            self.assertGreaterEqual(_configured_interval(), 1.0)
        finally:
            del os.environ['KORAIL_MIN_API_INTERVAL']

    def test_spacing_holds_across_threads(self):
        limiter = RateLimiter(0.2)
        stamps, lock = [], threading.Lock()

        def worker():
            for _ in range(3):
                limiter.wait()
                with lock:
                    stamps.append(time.monotonic())

        threads = [threading.Thread(target=worker) for _ in range(3)]
        [t.start() for t in threads]
        [t.join() for t in threads]

        stamps.sort()
        gaps = [b - a for a, b in zip(stamps, stamps[1:])]
        self.assertEqual(len(stamps), 9)
        self.assertGreaterEqual(min(gaps), 0.2 - 0.01)

    def test_reserve_fires_immediately_then_resets_the_gap(self):
        """좌석을 찾은 순간의 예약은 기다리지 않는다. 그 다음 호출은 다시 벌어진다."""
        trains = [FakeTrain(0, has_seat=True), FakeTrain(1)]
        svc = make_service(trains, reserve_fails=True)
        with FastRateLimit(0.6):
            run_macro(svc, [as_selected(trains[0], 0)], timeout=2.5)

        calls = svc._client.calls
        kinds = [c[0] for c in calls]
        self.assertIn('reserve', kinds)
        i = kinds.index('reserve')
        self.assertEqual(kinds[i - 1], 'search')
        self.assertLess(calls[i][3] - calls[i - 1][3], 0.05, '예약이 지연됐다')
        if len(calls) > i + 1:
            self.assertGreaterEqual(calls[i + 1][3] - calls[i][3], 0.6 - 0.01,
                                    '예약 다음 호출이 너무 빨랐다')


# ---------------------------------------------------------------- 여러 열차 예약

class MultiTrainReservationTest(unittest.TestCase):
    def test_one_search_covers_every_selected_train(self):
        """선택 열차마다 조회하지 않는다. 한 번 조회하고 메모리에서 대조한다."""
        trains = [FakeTrain(i, has_seat=(i == 10)) for i in range(40)]
        svc = make_service(trains)
        selected = [as_selected(trains[i], n) for n, i in enumerate((0, 10, 20))]
        with FastRateLimit():
            run_macro(svc, selected, timeout=6)

        reserves = svc._client.kinds('reserve')
        self.assertEqual(len(reserves), 1, '좌석 있는 열차 하나만 예약해야 한다')
        self.assertEqual(reserves[0][1], trains[10].train_no)
        # 가장 늦은 선택 열차(20번)까지 덮으려면 3페이지, 시도는 한 번에 끝난다
        self.assertEqual(len(svc._client.kinds('search')), 3)

    def test_two_passengers_together_is_one_call(self):
        trains = [FakeTrain(0, has_seat=True), FakeTrain(1)]
        svc = make_service(trains)
        with FastRateLimit():
            run_macro(svc, [as_selected(trains[0], 0)], timeout=4, passenger_count=2)

        reserves = svc._client.kinds('reserve')
        self.assertEqual(len(reserves), 1)
        self.assertEqual(reserves[0][2], 2, '2인 동시 예약은 한 번에 2석을 요청해야 한다')

    def test_two_passengers_sequential_takes_one_seat_at_a_time(self):
        trains = [FakeTrain(0, has_seat=True), FakeTrain(1)]
        svc = make_service(trains)
        with FastRateLimit():
            run_macro(svc, [as_selected(trains[0], 0)], timeout=6,
                      passenger_count=2, sequential=True)

        reserves = svc._client.kinds('reserve')
        self.assertEqual(len(reserves), 2, '1석씩 두 번 예약해야 한다')
        self.assertEqual([r[2] for r in reserves], [1, 1])

    def test_sequential_sticks_to_the_first_booked_train(self):
        """순차 예약에서 일행이 서로 다른 열차에 타면 안 된다.

        1회차에 A 열차 1석을 잡고, 그 사이 A 가 매진되고 B 에 좌석이 생겨도
        B 로 갈아타지 않고 A 를 계속 노려야 한다.
        """
        state = {'searches': 0}
        train_a, train_b = FakeTrain(0), FakeTrain(1)

        class Shifting(FakeClient):
            def search_train(self, **kw):
                state['searches'] += 1
                # 1회차 조회에서만 A 에 좌석, 그 뒤로는 B 에만 좌석
                train_a._has_seat = state['searches'] <= 1
                train_b._has_seat = state['searches'] > 1
                self.calls.append(('search', kw['time'], None, time.monotonic()))
                return [train_a, train_b]

        svc = KorailService()
        svc._client = Shifting([train_a, train_b])
        selected = [as_selected(train_a, 0), as_selected(train_b, 1)]
        with FastRateLimit():
            run_macro(svc, selected, timeout=4, passenger_count=2, sequential=True)

        booked = [r[1] for r in svc._client.kinds('reserve')]
        self.assertTrue(booked, '최소 한 번은 예약했어야 한다')
        self.assertEqual(booked[0], train_a.train_no)
        self.assertEqual(set(booked), {train_a.train_no},
                         '첫 좌석을 잡은 열차 외의 열차를 예약했다: %s' % booked)

    def test_stops_after_success(self):
        trains = [FakeTrain(0, has_seat=True)]
        svc = make_service(trains)
        with FastRateLimit():
            run_macro(svc, [as_selected(trains[0], 0)], timeout=4)
        self.assertEqual(len(svc._client.kinds('reserve')), 1,
                         '성공 후에도 계속 예약을 시도하면 안 된다')


# ---------------------------------------------------------------- 동시 실행 가드

class MacroStartGuardTest(unittest.TestCase):
    def setUp(self):
        self.tg = TelegramService.get_instance()
        self.tg.set_macro_state(False)

    def tearDown(self):
        self.tg.set_macro_state(False)

    def test_second_start_is_refused(self):
        """버튼 연타/탭 두 개로 매크로가 두 개 뜨면 같은 열차를 중복 예약한다."""
        results = []

        def claim():
            results.append(self.tg.try_start_macro())

        threads = [threading.Thread(target=claim) for _ in range(5)]
        [t.start() for t in threads]
        [t.join() for t in threads]

        self.assertEqual(results.count(True), 1)
        self.assertEqual(results.count(False), 4)

    def test_slot_is_released_when_the_loop_crashes(self):
        """본체가 죽어도 슬롯이 물려 있으면 다시는 매크로를 못 돌린다."""
        self.assertTrue(self.tg.try_start_macro())
        th = threading.Thread(
            target=reservation.run_reservation_loop,
            args=(None, 'korail', [{'train_name': '키가 빠진 열차'}],
                  SeatOption.GENERAL_FIRST, None),
            daemon=True,
        )
        original_hook = threading.excepthook
        threading.excepthook = lambda args: None  # 일부러 낸 예외라 출력만 막는다
        try:
            th.start()
            th.join(timeout=5)
        finally:
            threading.excepthook = original_hook
        self.assertFalse(self.tg._macro_running)
        self.assertTrue(self.tg.try_start_macro())


# ---------------------------------------------------------------- 선택 인덱스

class SelectionTest(unittest.TestCase):
    def setUp(self):
        from app import create_app
        self.client = create_app().test_client()
        with self.client.session_transaction() as sess:
            sess['auth'] = {'korail': {'logged_in': True, 'user_id': 'tester'}}
            sess['current_provider'] = 'korail'

    def post(self, indices, **extra):
        from werkzeug.datastructures import MultiDict
        data = MultiDict([('train_indices[]', i) for i in indices] + list(extra.items()))
        self.client.post('/reserve_select', data=data)
        with self.client.session_transaction() as sess:
            return sess['search_state']['korail']

    def test_duplicate_selection_is_collapsed(self):
        """데스크톱 행과 모바일 카드가 둘 다 DOM 에 있어 같은 열차가 두 번 올 수 있다."""
        self.assertEqual(self.post(['3', '3', '5'])['selected_indices'], [3, 5])

    def test_order_is_preserved(self):
        self.assertEqual(self.post(['7', '2', '9'])['selected_indices'], [7, 2, 9])

    def test_garbage_index_is_ignored(self):
        self.assertEqual(self.post(['1', 'abc', '2'])['selected_indices'], [1, 2])

    def test_passenger_count_is_clamped(self):
        state = self.post(['4'], passenger_count='9')
        self.assertEqual(state['passenger_count'], 2)


class ServiceReuseTest(unittest.TestCase):
    """페이지를 열 때마다 코레일에 로그인하면 안 된다."""

    def setUp(self):
        from unittest import mock
        from app import create_app
        from app.services import ServiceManager

        self.ServiceManager = ServiceManager
        ServiceManager._services.clear()
        self.logins = []

        def fake_login(svc, user_id, password):
            self.logins.append(user_id)
            svc._user_id, svc._password = user_id, password
            svc._client = type('C', (), {'logined': True})()
            return True

        def fake_logout(svc):
            svc._client = None
            svc._user_id = None

        self.patches = [
            mock.patch.object(KorailService, 'login', fake_login),
            mock.patch.object(KorailService, 'logout', fake_logout),
            mock.patch.object(KorailService, 'is_logged_in',
                              lambda svc: svc._client is not None),
        ]
        [p.start() for p in self.patches]
        self.client = create_app().test_client()

    def tearDown(self):
        [p.stop() for p in self.patches]
        self.ServiceManager._services.clear()

    def sign_in(self, user_id='tester'):
        with self.client.session_transaction() as sess:
            sess['auth'] = {'korail': {'logged_in': True, 'user_id': user_id}}
            sess['credentials'] = {'korail': {'user_id': user_id, 'password': 'pw'}}
            sess['current_provider'] = 'korail'
            sess['search_state'] = {'korail': {
                'trains': [], 'selected_indices': [],
                'seat_option': 'GENERAL_FIRST', 'form_data': {}}}

    def test_login_happens_once_across_requests(self):
        self.sign_in()
        for _ in range(5):
            self.client.get('/')
        self.assertEqual(len(self.logins), 1,
                         '페이지 로드마다 로그인하고 있다: %s' % self.logins)

    def test_relogin_when_the_account_changes(self):
        self.sign_in('first')
        self.client.get('/')
        self.sign_in('second')
        self.client.get('/')
        self.assertEqual(self.logins, ['first', 'second'])

    def test_logout_drops_the_cached_service(self):
        self.sign_in()
        self.client.get('/')
        self.client.post('/logout')
        self.assertEqual(self.ServiceManager._services, {})


if __name__ == '__main__':
    unittest.main(verbosity=2)
