# -*- coding: utf-8 -*-
"""라이선스 게이트가 앱에 제대로 걸리는지 (전체 ON / 허용한 것만 / 전체 OFF)."""
import io
import os
import sys
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

import licensing  # noqa: E402
from licensing import LicenseStatus  # noqa: E402
from licensing.policy import resolve_mode  # noqa: E402
from tests.support import valid_license_patch  # noqa: E402
from webui import create_app  # noqa: E402

SAME_ORIGIN = {'Origin': 'http://localhost'}


def status_patch(status):
    return mock.patch('licensing.current_status', return_value=status)


ALL_OFF = LicenseStatus(False, 'blocked', '점검 중입니다.')
NOT_ALLOWED = LicenseStatus(False, 'missing', '라이선스가 등록되어 있지 않습니다.')
ALL_ON = LicenseStatus(True, 'not_enforced', enforced=False)


class ResolveModeTest(unittest.TestCase):
    def test_aliases(self):
        self.assertEqual(resolve_mode('on'), 'open')
        self.assertEqual(resolve_mode('allow'), 'licensed')
        self.assertEqual(resolve_mode('OFF'), 'blocked')
        self.assertEqual(resolve_mode('licensed'), 'licensed')
        with self.assertRaises(ValueError):
            resolve_mode('maybe')


class GateWiringTest(unittest.TestCase):
    def test_gate_is_off_by_default(self):
        # 폰 앱·서버·테스트용 앱은 검사하지 않는다
        app = create_app(server_mode=False)
        self.assertFalse(app.config['LICENSE_GATE'])
        self.assertNotIn('license', app.blueprints)
        with mock.patch('licensing.current_status') as status:
            app.test_client().get('/login')
            status.assert_not_called()

    def test_all_off_blocks_every_page(self):
        app = create_app(server_mode=False, license_gate=True)
        with status_patch(ALL_OFF), mock.patch('licensing.machine_id', return_value='AAAA-BBBB-CCCC-DDDD'):
            client = app.test_client()
            resp = client.get('/login')
            self.assertEqual(resp.status_code, 302)
            self.assertTrue(resp.headers['Location'].endswith('/license'))

            page = client.get('/license').get_data(as_text=True)
            self.assertIn('사용이', page)
            self.assertIn('점검 중입니다.', page)

            api = client.get('/api/telegram/status', headers={'Accept': 'application/json'})
            self.assertEqual(api.status_code, 403)
            self.assertEqual(api.get_json()['code'], 'blocked')

    def test_allowed_only_without_license_shows_request_page(self):
        app = create_app(server_mode=False, license_gate=True)
        with status_patch(NOT_ALLOWED), mock.patch('licensing.machine_id', return_value='AAAA-BBBB-CCCC-DDDD'):
            client = app.test_client()
            self.assertEqual(client.get('/').status_code, 302)
            page = client.get('/license').get_data(as_text=True)
            self.assertIn('AAAA-BBBB-CCCC-DDDD', page)
            self.assertIn('라이선스 요청하기', page)

    def test_all_on_lets_everyone_in(self):
        app = create_app(server_mode=False, license_gate=True)
        with status_patch(ALL_ON):
            self.assertEqual(app.test_client().get('/login').status_code, 200)

    def test_login_rechecks_remote_switch_first(self):
        # 로그인 POST 는 캐시를 무시하고 원격 스위치를 새로 본다. 막혔으면 코레일에 보내지도 않는다.
        app = create_app(server_mode=False, license_gate=True)
        calls = []

        def fake_status(**kwargs):
            calls.append(kwargs)
            return ALL_ON if not kwargs.get('force_policy') else ALL_OFF

        from webui.services.service_manager import ServiceManager
        with mock.patch('licensing.current_status', side_effect=fake_status), \
                mock.patch.object(ServiceManager, 'login') as login:
            resp = app.test_client().post('/login', data={'user_id': 'me@example.com', 'password': 'x'},
                                          headers=SAME_ORIGIN)
        self.assertEqual(resp.status_code, 302)
        self.assertTrue(resp.headers['Location'].endswith('/license'))
        self.assertIn({'force_policy': True}, calls)
        login.assert_not_called()

    def test_access_gate_page_stays_open(self):
        with mock.patch.dict(os.environ, {'APP_PASSWORD': 'pw'}):
            app = create_app(server_mode=False, license_gate=True)
        with status_patch(ALL_OFF):
            self.assertEqual(app.test_client().get('/gate').status_code, 200)


class HeadlessLicenseTest(unittest.TestCase):
    ARGS = ['--id', 'tester', '--pw', 'secret', '--dep', '서울', '--arr', '부산', '--date', '20991231']

    def run_main(self, status):
        from desktop import headless
        out, err = io.StringIO(), io.StringIO()
        with status_patch(status), mock.patch('licensing.machine_id', return_value='AAAA-BBBB-CCCC-DDDD'), \
                mock.patch.object(headless, 'KorailService') as service, \
                redirect_stdout(out), redirect_stderr(err):
            code = headless.main(self.ARGS)
        return headless, code, out.getvalue() + err.getvalue(), service

    def test_blocked_stops_before_login(self):
        headless, code, output, service = self.run_main(ALL_OFF)
        self.assertEqual(code, headless.EXIT_LICENSE)
        self.assertIn('점검 중입니다.', output)
        service.assert_not_called()

    def test_missing_license_shows_machine_id(self):
        headless, code, output, service = self.run_main(NOT_ALLOWED)
        self.assertEqual(code, headless.EXIT_LICENSE)
        self.assertIn('AAAA-BBBB-CCCC-DDDD', output)
        service.assert_not_called()


class SupportPatchTest(unittest.TestCase):
    def test_valid_patch(self):
        with valid_license_patch():
            self.assertTrue(licensing.current_status().valid)


if __name__ == '__main__':
    unittest.main()
