# -*- coding: utf-8 -*-
"""홈 화면에 추가(PWA) 관련 응답."""
import json
import os
import unittest
from unittest import mock


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
            self.assertEqual(client.get(icon['src']).status_code, 200, icon['src'])
        self.assertEqual(client.get('/static/icons/apple-touch-icon.png').status_code, 200)

    def test_service_worker_is_served_from_root(self):
        resp = make_client().get('/sw.js')
        self.assertEqual(resp.status_code, 200)
        self.assertEqual(resp.mimetype, 'text/javascript')

    def test_gate_does_not_block_pwa_files(self):
        """브라우저는 manifest 를 쿠키 없이 가져가므로 관문에 막히면 설치가 안 된다."""
        client = make_client(APP_PASSWORD='letmein')
        self.assertEqual(client.get('/manifest.webmanifest').status_code, 200)
        self.assertEqual(client.get('/sw.js').status_code, 200)
        self.assertEqual(client.get('/static/icons/icon-192.png').status_code, 200)
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
