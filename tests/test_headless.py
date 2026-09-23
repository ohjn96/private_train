# -*- coding: utf-8 -*-
"""헤드리스 러너 회귀 테스트.

코레일 서버에 붙지 않는다. 가짜 클라이언트를 꽂아 입력 해석, 열차 선택,
종료 코드, 그리고 매크로가 웹과 같은 루프로 넘어가는지를 본다.

실행:
    python -m unittest discover -s tests -v
"""
import io
import os
import sys
import threading
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import desktop.main as main_module
from desktop import headless
from webui.services.base_service import SeatOption, TrainInfo, TrainProvider
from webui.services.telegram_service import TelegramService


def make_train(number='101', dep_time='080000', general=True, special=False):
    """조회 결과 한 건."""
    return TrainInfo(
        provider=TrainProvider.KORAIL,
        train_name='KTX',
        train_number=number,
        dep_date='20261003',
        dep_time=dep_time,
        arr_date='20261003',
        arr_time='104000',
        dep_station='서울',
        arr_station='부산',
        general_seat_available=general,
        special_seat_available=special,
    )


BASE_ARGS = [
    '--id', 'tester', '--pw', 'secret',
    '--dep', '서울', '--arr', '부산', '--date', '20261003',
]


class ParseTimeTest(unittest.TestCase):
    """시각 표기를 너그럽게 받되, 틀린 건 확실히 거른다."""

    def test_accepts_common_forms(self):
        for value, expected in [
            ('8', '080000'), ('08', '080000'), ('0830', '083000'),
            ('08:30', '083000'), ('083015', '083015'), ('08:30:15', '083015'),
        ]:
            with self.subTest(value=value):
                self.assertEqual(headless.parse_time(value, '시작'), expected)

    def test_rejects_bad_input(self):
        for value in ['', 'abc', '830', '25:00', '08:70', '1234567']:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    headless.parse_time(value, '시작')

    def test_shift_time_clamps_at_midnight(self):
        self.assertEqual(headless.shift_time('080000', 3), '110000')
        self.assertEqual(headless.shift_time('230000', 3), '235959')


class ParseDateTest(unittest.TestCase):

    def test_accepts_both_forms(self):
        self.assertEqual(headless.parse_date('20261003'), '20261003')
        self.assertEqual(headless.parse_date('2026-10-03'), '20261003')

    def test_rejects_impossible_date(self):
        for value in ['20261301', '2026103', 'tomorrow']:
            with self.subTest(value=value):
                with self.assertRaises(ValueError):
                    headless.parse_date(value)


class SelectTrainsTest(unittest.TestCase):
    """열차번호를 주면 그것만, 아니면 시간 범위로 고른다."""

    def setUp(self):
        self.trains = [
            make_train('101', '070000'),
            make_train('103', '090000'),
            make_train('105', '110000'),
        ]

    def test_filters_by_time_window(self):
        picked = headless.select_trains(self.trains, [], '080000', '100000')
        self.assertEqual([t.train_number for t in picked], ['103'])

    def test_time_window_is_inclusive(self):
        picked = headless.select_trains(self.trains, [], '070000', '110000')
        self.assertEqual(len(picked), 3)

    def test_explicit_numbers_ignore_window(self):
        picked = headless.select_trains(self.trains, ['101', '105'], '080000', '100000')
        self.assertEqual([t.train_number for t in picked], ['101', '105'])

    def test_unknown_number_matches_nothing(self):
        self.assertEqual(headless.select_trains(self.trains, ['999'], '000000', '235959'), [])


class MaskTest(unittest.TestCase):

    def test_hides_middle(self):
        self.assertEqual(headless.mask('1234567890'), '12******90')

    def test_short_id_fully_hidden(self):
        self.assertEqual(headless.mask('abcd'), '****')


class BuildCardTest(unittest.TestCase):

    def _args(self, **overrides):
        argv = list(BASE_ARGS)
        for flag, value in overrides.items():
            argv += [f"--{flag.replace('_', '-')}", value]
        return headless.build_parser().parse_args(argv)

    def test_no_card_number_means_no_payment(self):
        self.assertIsNone(headless.build_card(self._args()))

    def test_partial_card_info_is_rejected(self):
        with self.assertRaises(ValueError) as caught:
            headless.build_card(self._args(card_number='1234'))
        self.assertIn('--card-password', str(caught.exception))

    def test_full_card_info(self):
        card = headless.build_card(self._args(
            card_number='1234', card_password='12',
            card_validation='900101', card_expire='2812',
        ))
        self.assertEqual(card['card_number'], '1234')
        self.assertTrue(card['auto_pay'])


class ConfigValidationTest(unittest.TestCase):
    """설정이 틀리면 로그인 시도조차 하지 않고 종료해야 한다."""

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = headless.main(argv)
        return code, out.getvalue() + err.getvalue()

    def test_missing_required_options(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            code, output = self.run_main([])
        self.assertEqual(code, headless.EXIT_CONFIG)
        self.assertIn('--id (KORAIL_ID)', output)

    def test_bad_date(self):
        code, output = self.run_main(BASE_ARGS[:-1] + ['20261301'])
        self.assertEqual(code, headless.EXIT_CONFIG)
        self.assertIn('날짜 형식', output)

    def test_end_before_start(self):
        code, output = self.run_main(BASE_ARGS + ['--from', '12:00', '--to', '08:00'])
        self.assertEqual(code, headless.EXIT_CONFIG)
        self.assertIn('빠릅니다', output)

    def test_env_vars_are_used(self):
        env = {
            'KORAIL_ID': 'tester', 'KORAIL_PW': 'secret',
            'TRAIN_DEP': '서울', 'TRAIN_ARR': '부산', 'TRAIN_DATE': '20261301',
        }
        with mock.patch.dict(os.environ, env, clear=True):
            code, output = self.run_main([])
        # 필수값은 환경변수로 다 채워졌으므로, 막히는 지점은 날짜 형식이어야 한다
        self.assertEqual(code, headless.EXIT_CONFIG)
        self.assertIn('날짜 형식', output)


class RunTest(unittest.TestCase):
    """로그인 → 조회 → 매크로로 이어지는 흐름."""

    def setUp(self):
        self.service = mock.Mock()
        self.service.login.return_value = True
        self.service.search.return_value = [
            make_train('101', '083000'), make_train('103', '093000'),
        ]
        patcher = mock.patch.object(headless, 'KorailService', return_value=self.service)
        patcher.start()
        self.addCleanup(patcher.stop)

        # TelegramService 는 싱글턴이라 매크로 슬롯이 테스트 사이에 남는다.
        # 실제 실행에서는 run_reservation_loop 가 끝나며 돌려주지만 여기선 그걸
        # 가짜로 바꿔 끼우므로 직접 비워 준다.
        self.telegram = TelegramService.get_instance()
        self.telegram.set_macro_state(False)
        self.addCleanup(self.telegram.set_macro_state, False)
        self.addCleanup(self.telegram.set_log_sink, None)

    def run_main(self, argv):
        out, err = io.StringIO(), io.StringIO()
        with redirect_stdout(out), redirect_stderr(err):
            code = headless.main(argv)
        return code, out.getvalue() + err.getvalue()

    def test_login_failure_stops_before_search(self):
        self.service.login.return_value = False
        code, output = self.run_main(BASE_ARGS)
        self.assertEqual(code, headless.EXIT_LOGIN)
        self.assertIn('로그인 실패', output)
        self.service.search.assert_not_called()

    def test_no_matching_train_lists_what_was_found(self):
        code, output = self.run_main(BASE_ARGS + ['--from', '20:00', '--to', '22:00'])
        self.assertEqual(code, headless.EXIT_NO_TRAIN)
        self.assertIn('조건에 맞는 열차가 없습니다', output)
        self.assertIn('101', output)  # 조회된 열차를 함께 보여준다

    def test_dry_run_does_not_start_macro(self):
        with mock.patch('webui.routes.reservation.run_reservation_loop') as loop:
            code, output = self.run_main(BASE_ARGS + ['--from', '08:00', '--dry-run'])
        self.assertEqual(code, headless.EXIT_OK)
        self.assertIn('dry-run', output)
        loop.assert_not_called()

    def test_search_covers_the_requested_window(self):
        with mock.patch('webui.routes.reservation.run_reservation_loop'):
            self.run_main(BASE_ARGS + ['--from', '08:00', '--to', '10:00'])
        kwargs = self.service.search.call_args.kwargs
        self.assertEqual(kwargs['time'], '080000')
        self.assertEqual(kwargs['until_time'], '100000')
        self.assertTrue(kwargs['include_no_seats'])

    def test_macro_receives_selected_trains(self):
        with mock.patch('webui.routes.reservation.run_reservation_loop') as loop:
            code, _ = self.run_main(BASE_ARGS + ['--from', '08:00', '--to', '09:00'])
        self.assertEqual(code, headless.EXIT_OK)
        service, provider, trains, seat_option, card = loop.call_args.args
        self.assertIs(service, self.service)
        self.assertEqual(provider, 'korail')
        self.assertEqual([t['train_number'] for t in trains], ['101'])
        self.assertEqual(seat_option, SeatOption.GENERAL_FIRST)
        self.assertIsNone(card)
        self.assertEqual(loop.call_args.kwargs, {'passenger_count': 1, 'sequential': False})

    def test_sequential_only_applies_to_multiple_seats(self):
        with mock.patch('webui.routes.reservation.run_reservation_loop') as loop:
            self.run_main(BASE_ARGS + ['--from', '08:00', '--sequential'])
        self.assertFalse(loop.call_args.kwargs['sequential'])

        self.telegram.set_macro_state(False)
        with mock.patch('webui.routes.reservation.run_reservation_loop') as loop:
            self.run_main(BASE_ARGS + ['--from', '08:00', '--passengers', '2', '--sequential'])
        self.assertEqual(loop.call_args.kwargs, {'passenger_count': 2, 'sequential': True})

    def test_credentials_are_handed_to_telegram_for_recovery(self):
        with mock.patch('webui.routes.reservation.run_reservation_loop'):
            self.run_main(BASE_ARGS + ['--from', '08:00'])
        self.assertEqual(
            self.telegram._stored_credentials,
            {'user_id': 'tester', 'password': 'secret'},
        )


class RecoveryWithoutFlaskTest(unittest.TestCase):
    """헤드리스에는 Flask 세션이 없다. 그래도 재로그인이 되어야 한다."""

    def test_falls_back_to_service_credentials(self):
        import webui.routes.reservation as reservation

        service = mock.Mock()
        service.credentials = {'user_id': 'tester', 'password': 'secret'}
        service.login.return_value = True

        # 요청 컨텍스트 밖이므로 get_credentials 는 RuntimeError 를 낸다
        ok, message = reservation.attempt_recovery('korail', service)

        self.assertTrue(ok, message)
        service.login.assert_called_once_with('tester', 'secret')

    def test_reports_failure_when_nothing_is_stored(self):
        import webui.routes.reservation as reservation

        service = mock.Mock(spec=['logout', 'login'])  # credentials 속성 없음
        ok, message = reservation.attempt_recovery('korail', service)

        self.assertFalse(ok)
        self.assertIn('저장된 로그인 정보가 없습니다', message)


class ToDictTest(unittest.TestCase):
    """예약 루프가 열차를 찾는 데 쓰는 키가 빠지면 안 된다."""

    def test_has_keys_the_macro_matches_on(self):
        data = make_train('101', '083000').to_dict(index=3)
        self.assertEqual(data['index'], 3)
        for key in ('train_number', 'dep_time', 'dep_date', 'dep_station',
                    'arr_station', 'train_name'):
            self.assertIn(key, data)
        self.assertEqual(data['dep_time_formatted'], '08:30')


class BuildPlanTest(unittest.TestCase):
    """열차를 다 주면 한 방 모드, 하나도 안 주고 토큰만 주면 대기 모드."""

    def plan(self, *extra_args):
        args = headless.build_parser().parse_args(list(extra_args))
        return headless.build_plan(args)

    def test_account_is_required(self):
        with self.assertRaises(headless.ConfigError) as caught:
            self.plan('--dep', '서울', '--arr', '부산', '--date', '20261003')
        self.assertIn('--id (KORAIL_ID)', str(caught.exception))

    def test_full_trip_is_oneshot(self):
        plan = self.plan(*BASE_ARGS, '--from', '08:00', '--to', '10:00')
        self.assertIsNotNone(plan.trip)
        self.assertEqual(plan.trip.dep, '서울')
        self.assertEqual((plan.trip.since, plan.trip.until), ('080000', '100000'))

    def test_token_without_trip_is_standby(self):
        plan = self.plan('--id', 'tester', '--pw', 'secret', '--telegram-token', 'T')
        self.assertIsNone(plan.trip)

    def test_nothing_at_all_is_rejected(self):
        with self.assertRaises(headless.ConfigError) as caught:
            self.plan('--id', 'tester', '--pw', 'secret')
        self.assertIn('--telegram-token', str(caught.exception))

    def test_partial_trip_is_rejected(self):
        with self.assertRaises(headless.ConfigError) as caught:
            self.plan('--id', 'tester', '--pw', 'secret', '--dep', '서울',
                      '--telegram-token', 'T')
        self.assertIn('셋 다', str(caught.exception))

    def test_default_window_is_three_hours(self):
        plan = self.plan(*BASE_ARGS, '--from', '08:00')
        self.assertEqual(plan.trip.until, '110000')


class StandbyTest(unittest.TestCase):
    """대기 모드는 텔레그램이 붙어 있어야만 의미가 있다."""

    def test_refuses_without_a_connection(self):
        telegram = mock.Mock()
        telegram.is_connected = False
        out = io.StringIO()
        with redirect_stdout(out), redirect_stderr(out):
            code = headless.run_standby(telegram, threading.Event())
        self.assertEqual(code, headless.EXIT_CONFIG)
        self.assertIn('대기 모드를 쓸 수 없습니다', out.getvalue())

    def test_waits_until_stopped(self):
        telegram = mock.Mock()
        telegram.is_connected = True
        telegram.chat_id = '123'
        stop = threading.Event()
        threading.Timer(0.05, stop.set).start()

        out = io.StringIO()
        with redirect_stdout(out):
            code = headless.run_standby(telegram, stop)

        self.assertEqual(code, headless.EXIT_OK)
        self.assertIn('/reserve', out.getvalue())

    def test_main_enters_standby_and_wires_telegram(self):
        service = mock.Mock()
        service.login.return_value = True
        telegram = TelegramService.get_instance()

        with mock.patch.object(headless, 'KorailService', return_value=service), \
             mock.patch.object(headless, 'connect_telegram', return_value=telegram), \
             mock.patch.object(headless, 'run_standby', return_value=headless.EXIT_OK) as standby:
            out = io.StringIO()
            with redirect_stdout(out):
                code = headless.main([
                    '--id', 'tester', '--pw', 'secret', '--telegram-token', 'T',
                ])

        self.assertEqual(code, headless.EXIT_OK)
        standby.assert_called_once()
        self.assertIn('대기 모드', out.getvalue())
        # /reserve 가 스스로 로그인할 수 있도록 자격증명이 넘어가 있어야 한다
        self.assertEqual(
            telegram._stored_credentials, {'user_id': 'tester', 'password': 'secret'}
        )
        service.search.assert_not_called()


class EntryPointTest(unittest.TestCase):
    """main.py 하나로 웹과 헤드리스를 모두 띄운다."""

    def test_flag_selects_headless(self):
        self.assertTrue(main_module.wants_headless(['--headless', '--id', 'x']))
        self.assertFalse(main_module.wants_headless([]))

    def test_env_selects_headless(self):
        with mock.patch.dict(os.environ, {'HEADLESS': '1'}):
            self.assertTrue(main_module.wants_headless([]))
        with mock.patch.dict(os.environ, {'HEADLESS': 'no'}):
            self.assertFalse(main_module.wants_headless([]))

    def test_utf8_setup_tolerates_odd_streams(self):
        # StringIO 는 reconfigure 가 없고, 아래 object() 는 아무것도 없다.
        # 어느 쪽이든 조용히 넘어가야 한다 (테스트 러너의 stdout 도 이런 모양이다).
        with mock.patch.object(sys, 'stdout', io.StringIO()), \
             mock.patch.object(sys, 'stderr', object()):
            main_module._force_utf8_output()

    def test_flag_is_stripped_before_handoff(self):
        with mock.patch('desktop.headless.main', return_value=0) as headless_main:
            main_module.run_headless(['--headless', '--id', 'x', '--pw', 'y'])
        headless_main.assert_called_once_with(['--id', 'x', '--pw', 'y'])


if __name__ == '__main__':
    unittest.main()
