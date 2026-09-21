# -*- coding: utf-8 -*-
"""텔레그램 폴링과 설정 저장 회귀 테스트.

네트워크를 타지 않는다. _api_call 을 가짜로 바꿔 끼운다.

실행:
    python -m unittest discover -s tests -v
"""
import os
import sys
import tempfile
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from app.services.telegram_service import TelegramService


def setUpModule():
    """예외를 일부러 던지는 테스트가 많아 로그가 출력을 덮는다."""
    import logging
    logging.getLogger('app.services.telegram_service').setLevel(logging.CRITICAL)


def message(update_id, text, chat_id='555'):
    return {
        'update_id': update_id,
        'message': {
            'text': text,
            'chat': {'id': chat_id},
            'from': {'first_name': '테스터'},
        },
    }


class PollLoopTest(unittest.TestCase):
    """_poll_loop — 메시지 하나가 터져도 봇 전체가 멎으면 안 된다."""

    def setUp(self):
        self.tg = TelegramService()
        self.tg.bot_token = 'fake:token'
        self.tg.chat_id = '555'

    def drain(self, updates, handler):
        """한 바퀴 분량의 업데이트를 실제 루프에 흘려보낸다."""
        remaining = list(updates)

        def get_updates():
            if not remaining:
                self.tg._polling_active = False
                return []
            batch, remaining[:] = list(remaining), []
            return batch

        with mock.patch.object(self.tg, '_get_updates', side_effect=get_updates), \
             mock.patch.object(self.tg, '_handle_update', side_effect=handler), \
             mock.patch('app.services.telegram_service.time.sleep'):
            self.tg._polling_active = True
            self.tg._poll_loop()

    def test_offset_advances_even_when_a_handler_blows_up(self):
        """이게 깨지면 같은 메시지를 무한히 다시 받아 봇이 통째로 멎는다."""
        seen = []

        def handler(update):
            seen.append(update['update_id'])
            raise RuntimeError('처리 중 예외')

        self.drain([message(10, '/status')], handler)
        self.assertEqual(self.tg._last_update_id, 11)
        self.assertEqual(seen, [10])

    def test_one_bad_message_does_not_block_the_next(self):
        handled = []

        def handler(update):
            handled.append(update['update_id'])
            if update['update_id'] == 20:
                raise ValueError('깨진 메시지')

        self.drain([message(20, '???'), message(21, '/status')], handler)
        self.assertEqual(handled, [20, 21])
        self.assertEqual(self.tg._last_update_id, 22)

    def test_handled_messages_advance_normally(self):
        seen = []
        self.drain([message(30, '/status'), message(31, '/help')],
                   lambda update: seen.append(update['update_id']))
        self.assertEqual(seen, [30, 31])
        self.assertEqual(self.tg._last_update_id, 32)


class BacklogTest(unittest.TestCase):
    """_skip_backlog — 어제 친 명령이 오늘 실행되면 안 된다."""

    def setUp(self):
        self.tg = TelegramService()
        self.tg.bot_token = 'fake:token'

    def test_skips_past_old_updates(self):
        with mock.patch.object(self.tg, '_api_call',
                               return_value=[{'update_id': 99}]):
            self.tg._skip_backlog()
        self.assertEqual(self.tg._last_update_id, 100)

    def test_no_backlog_leaves_offset_alone(self):
        with mock.patch.object(self.tg, '_api_call', return_value=[]):
            self.tg._skip_backlog()
        self.assertEqual(self.tg._last_update_id, 0)

    def test_api_failure_is_not_fatal(self):
        with mock.patch.object(self.tg, '_api_call', side_effect=RuntimeError('네트워크')):
            self.tg._skip_backlog()          # 예외가 새어 나오면 안 된다
        self.assertEqual(self.tg._last_update_id, 0)


class CommandMenuTest(unittest.TestCase):
    """setMyCommands — 입력창 명령어 메뉴."""

    def setUp(self):
        self.tg = TelegramService()
        self.tg.bot_token = 'fake:token'

    def test_registers_every_documented_command(self):
        with mock.patch.object(self.tg, '_api_call', return_value=True) as call:
            self.assertTrue(self.tg.register_commands())

        method, payload = call.call_args[0]
        self.assertEqual(method, 'setMyCommands')
        names = [c['command'] for c in payload['commands']]
        for expected in ('start', 'help', 'status', 'stop', 'reserve', 'trains'):
            self.assertIn(expected, names)
        # 텔레그램은 슬래시 없는 소문자 이름을 요구한다
        for name in names:
            self.assertFalse(name.startswith('/'), name)
            self.assertEqual(name, name.lower())

    def test_failure_is_reported_not_raised(self):
        with mock.patch.object(self.tg, '_api_call', return_value=None):
            self.assertFalse(self.tg.register_commands())


class StoreTest(unittest.TestCase):
    """설정 저장 — 이 PC 에서만 풀려야 한다."""

    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.env = mock.patch.dict(os.environ,
                                   {'TRAIN_LICENSE_DIR': str(Path(self.tmp.name))})
        self.env.start()
        from app.services import telegram_store
        self.store = telegram_store

    def tearDown(self):
        self.env.stop()
        self.tmp.cleanup()

    def test_roundtrip(self):
        self.assertTrue(self.store.save('123:ABC', '777'))
        self.assertEqual(self.store.load(),
                         {'bot_token': '123:ABC', 'chat_id': '777'})

    def test_token_is_not_stored_in_the_clear(self):
        self.store.save('123:SUPERSECRET', '777')
        raw = self.store.store_path().read_text(encoding='utf-8')
        self.assertNotIn('SUPERSECRET', raw)

    def test_another_machine_cannot_read_it(self):
        self.store.save('123:ABC', '777')
        with mock.patch.object(self.store, 'machine_id',
                               return_value='FFFF-FFFF-FFFF-FFFF'):
            self.assertIsNone(self.store.load())

    def test_tampered_file_is_rejected(self):
        self.store.save('123:ABC', '777')
        path = self.store.store_path()
        import json
        raw = json.loads(path.read_text(encoding='utf-8'))
        raw['data'] = raw['data'][:-4] + 'AAAA'      # 본문을 건드린다
        path.write_text(json.dumps(raw), encoding='utf-8')
        self.assertIsNone(self.store.load())

    def test_missing_file(self):
        self.assertIsNone(self.store.load())

    def test_clear(self):
        self.store.save('123:ABC', '777')
        self.store.clear()
        self.assertIsNone(self.store.load())

    def test_empty_token_is_not_saved(self):
        self.assertFalse(self.store.save('', '777'))

    def test_chat_id_is_optional(self):
        self.store.save('123:ABC', None)
        self.assertEqual(self.store.load()['chat_id'], None)


if __name__ == '__main__':
    unittest.main()
