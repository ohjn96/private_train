# -*- coding: utf-8 -*-
"""화면 CSS·글꼴을 앱에 같이 싣는지: Tailwind 는 미리 만든 app.css, 글꼴은 static/fonts.

실행 중에 CDN(Tailwind Play)·Google Fonts 를 부르지 않아야 연결이 약하거나 없을 때도
화면이 그대로 나온다. app.css 는 scripts/build_css.sh 로 다시 만든다.
"""
import os
import re
import sys
import unittest

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

STATIC = os.path.join(ROOT, 'webui', 'static')
TEMPLATES = os.path.join(ROOT, 'webui', 'templates')


def read(*parts):
    with open(os.path.join(*parts), encoding='utf-8') as f:
        return f.read()


class PrebuiltCssTest(unittest.TestCase):
    def test_app_css_is_built(self):
        css = read(STATIC, 'css', 'app.css')
        self.assertGreater(len(css), 5000)
        # 미리 만든 결과물이어야 한다 (@tailwind 지시문이 남아 있으면 원본을 그대로 올린 것)
        self.assertNotIn('@tailwind', css)
        # 팔레트 클래스와 예전 인라인 <style> 규칙이 들어 있다
        for needle in ('.bg-rail-ground', '.text-rail-ink', '.bg-ktx-primary', '.btn-primary', '.log-container'):
            self.assertIn(needle, css)

    def test_base_links_css_not_play_cdn(self):
        base = read(TEMPLATES, 'base.html')
        self.assertIn("filename='css/app.css'", base)
        self.assertIn("filename='css/fonts.css'", base)
        self.assertNotIn('tailwindcss.js', base)
        self.assertNotIn('tailwind.config', base)
        self.assertFalse(os.path.exists(os.path.join(STATIC, 'vendor', 'tailwindcss.js')))

    def test_no_external_fonts_or_styles(self):
        for name in os.listdir(TEMPLATES):
            html = read(TEMPLATES, name)
            self.assertNotIn('fonts.googleapis.com', html, name)
            self.assertNotIn('fonts.gstatic.com', html, name)
        from webui import CONTENT_SECURITY_POLICY
        self.assertNotIn('googleapis', CONTENT_SECURITY_POLICY)
        self.assertNotIn('gstatic', CONTENT_SECURITY_POLICY)

    def test_font_files_exist(self):
        css = read(STATIC, 'css', 'fonts.css')
        urls = re.findall(r'url\(\.\./fonts/([^)]+)\)', css)
        self.assertTrue(urls)
        for name in urls:
            self.assertTrue(os.path.isfile(os.path.join(STATIC, 'fonts', name)), name)
        for family in ('IBM Plex Sans KR', 'IBM Plex Mono'):
            self.assertIn(family, css)

    def test_css_and_fonts_are_served(self):
        from webui import create_app
        client = create_app(server_mode=False).test_client()
        for path in ('/static/css/app.css', '/static/css/fonts.css', '/static/fonts/plexsanskr-400-119.woff2'):
            resp = client.get(path)
            self.assertEqual(resp.status_code, 200, path)
            resp.close()


if __name__ == '__main__':
    unittest.main(verbosity=2)
