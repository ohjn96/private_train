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
        self.assertFalse(reporter.running)

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
