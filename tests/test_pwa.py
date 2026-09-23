# -*- coding: utf-8 -*-
"""홈 화면에 추가(PWA) 관련 응답."""
import json
import os
import unittest
from unittest import mock


def status_of(client, path):
    """정적 파일 응답은 파일 핸들을 쥐고 있으니 닫아 준다 (ResourceWarning 방지)."""
    resp = client.get(path)
    resp.close()
    return resp.status_code


def make_client(**env):
    from app import create_app
    with mock.patch.dict(os.environ, env):
        return create_app().test_client()


class PwaTest(unittest.TestCase):
    def test_manifest(self):
        resp = make_client().get('/manifest.webmanifest')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, 'application/manifest+json')
        data = json.loads(resp.data)
        self.assertEqual(data['display'], 'standalone')
        sizes = {icon['sizes'] for icon in data['icons']}
        self.assertTrue({'192x192', '512x512'} <= sizes)

    def test_icons_exist(self):
        client = make_client()
        data = json.loads(client.get('/manifest.webmanifest').data)
        for icon in data['icons']:
            self.assertEqual(status_of(client, icon['src']), 200, icon['src'])
        self.assertEqual(status_of(client, '/static/icons/apple-touch-icon.png'), 200)

    def test_service_worker_is_served_from_root(self):
        resp = make_client().get('/sw.js')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, 'text/javascript')

    def test_gate_does_not_block_pwa_files(self):
        """브라우저는 manifest 를 쿠키 없이 가져가므로 관문에 막히면 설치가 안 된다."""
        client = make_client(APP_PASSWORD='letmein')
        self.assertEqual(client.get('/manifest.webmanifest').status_code, 200)
        self.assertEqual(client.get('/sw.js').status_code, 200)
        self.assertEqual(status_of(client, '/static/icons/icon-192.png'), 200)
        self.assertEqual(client.get('/login').status_code, 302)

    def test_pages_link_the_manifest(self):
        html = make_client().get('/login').data.decode()
        self.assertIn('rel="manifest"', html)
        self.assertIn('apple-touch-icon', html)


class CookieSecureTest(unittest.TestCase):
    def test_off_by_default(self):
        self.assertFalse(make_client().application.config['SESSION_COOKIE_SECURE'])

    def test_on_behind_https(self):
        self.assertTrue(make_client(COOKIE_SECURE='1').application.config['SESSION_COOKIE_SECURE'])


if __name__ == '__main__':
    unittest.main(verbosity=2)


class VersionOverrideTest(unittest.TestCase):
    """안드로이드 앱은 VERSION 파일을 싣지 않고 버전을 환경변수로 넘긴다."""

    def test_env_wins(self):
        from app.version import get_version
        with mock.patch.dict(os.environ, {'TRAIN_APP_VERSION': '9.9.9'}):
            self.assertEqual(get_version(), '9.9.9')

    def test_file_otherwise(self):
        from pathlib import Path
        from app.version import get_version
        expected = (Path(__file__).resolve().parent.parent / 'VERSION').read_text().strip()
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('TRAIN_APP_VERSION', None)
            self.assertEqual(get_version(), expected)
