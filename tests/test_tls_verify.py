# -*- coding: utf-8 -*-
"""코레일과의 TLS 는 인증서 검증을 끄면 안 된다 (중간자 공격으로 계정·카드가 샌다)."""
import ast
import os
import unittest
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
SCAN_DIRS = ('korail2', 'core', 'webui', 'server', 'desktop', 'mobile')


def _py_files():
    for d in SCAN_DIRS:
        for base, dirs, files in os.walk(os.path.join(ROOT, d)):
            dirs[:] = [x for x in dirs if x not in ('build', '.gradle', 'node_modules')]
            for f in files:
                if f.endswith('.py'):
                    yield os.path.join(base, f)


class NoInsecureTlsTest(unittest.TestCase):
    def test_no_call_passes_verify_false(self):
        offenders = []
        for path in _py_files():
            with open(path, encoding='utf-8') as fp:
                tree = ast.parse(fp.read(), path)
            for node in ast.walk(tree):
                if isinstance(node, ast.Call):
                    for kw in node.keywords:
                        if (kw.arg == 'verify' and isinstance(kw.value, ast.Constant)
                                and kw.value.value is False):
                            offenders.append(f'{os.path.relpath(path, ROOT)}:{node.lineno}')
        self.assertEqual(offenders, [])

    def test_insecure_warning_is_not_silenced(self):
        with open(os.path.join(ROOT, 'korail2', 'korail2.py'), encoding='utf-8') as fp:
            src = fp.read()
        self.assertNotIn('Unverified HTTPS request', src)
        self.assertNotIn('disable_warnings', src)

    def test_korail_session_keeps_default_verification(self):
        from korail2.korail2 import Korail
        k = Korail('id', 'pw', auto_login=False)
        self.assertIs(k._session.verify, True)
        with mock.patch('requests.Session.send') as send:
            send.return_value = mock.Mock(status_code=200, text='{}', headers={})
            try:
                k._session.get('https://smart.letskorail.com/x')
            except Exception:
                pass
            self.assertTrue(send.called)
            self.assertNotIn(send.call_args.kwargs.get('verify'), (False, None))


if __name__ == '__main__':
    unittest.main()
