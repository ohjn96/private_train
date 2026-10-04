# -*- coding: utf-8 -*-
"""자동 갱신 규칙이 두 저장소에서 같은 답을 내는지.

판정 로직이 두 군데에 있다.

    비공개  scripts/license_admin.py   autorenew_days_for()   실제로 갱신하는 쪽
    공개    docs/assets/js/model.js    autoRenewDays()        현황판에 보여주는 쪽

어긋나면 현황판엔 '자동 갱신됨'으로 보이는데 실제로는 갱신되지 않는다.
저장소가 갈려 있어 한쪽만 고치기 쉬우므로 여기서 대조한다.

배포 저장소를 체크아웃해 두고 실행:
    git clone https://github.com/ohjn96/train-reservation.git dist
    LICENSE_DIST_DIR=dist python -m unittest discover -s tests
"""
import json
import os
import re
import sys
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import dukpy
except ImportError:
    dukpy = None

ROOT = Path(__file__).resolve().parent.parent
DIST = Path(os.environ.get('LICENSE_DIST_DIR', ROOT / 'dist'))
JS_DIR = DIST / 'docs' / 'assets' / 'js'

IMPORT_RE = re.compile(r'^\s*import\s.*?;\s*$', re.M)
EXPORT_RE = re.compile(r'^\s*export\s+(?=(?:const|function|class|let|var)\b)', re.M)


def bundle() -> str:
    """ES 모듈 구문만 걷어내 한 스코프에서 평가할 수 있게 만든다."""
    parts = []
    for name in ('config.js', 'model.js'):
        text = (JS_DIR / name).read_text(encoding='utf-8')
        parts.append(EXPORT_RE.sub('', IMPORT_RE.sub('', text)))
    return '\n'.join(parts)


@unittest.skipUnless(dukpy, 'dukpy 미설치 (pip install dukpy)')
@unittest.skipUnless(JS_DIR.exists(),
                     '배포 저장소 체크아웃 없음 (LICENSE_DIST_DIR 지정 필요)')
class AutoRenewParityTest(unittest.TestCase):

    @classmethod
    def setUpClass(cls):
        cls.js = bundle()
        sys.path.insert(0, str(ROOT / 'scripts'))

    def assert_same(self, settings, machine_id):
        from license_admin import autorenew_days_for

        js = dukpy.evaljs(
            f'{self.js}\n'
            f'var s = {json.dumps(settings, ensure_ascii=False)};\n'
            f'autoRenewDays({json.dumps(machine_id)}, s)')
        py = autorenew_days_for(machine_id, dict(settings))
        self.assertEqual(js, py, f'{machine_id} / {settings}')

    def test_rules_agree(self):
        mid = 'AAAA-0000-0000-0001'
        cases = [
            {'all': False, 'default_days': 30, 'machines': {}},
            {'all': True,  'default_days': 30, 'machines': {}},
            {'all': True,  'default_days': 30, 'machines': {mid: {'off': True}}},
            {'all': False, 'default_days': 30, 'machines': {mid: {'days': 7}}},
            {'all': False, 'default_days': 14, 'machines': {mid: {}}},
            {'all': False, 'default_days': 30, 'machines': {mid: {'days': 30, 'until': '2000-01-01'}}},
            {'all': False, 'default_days': 30, 'machines': {mid: {'days': 30, 'until': '2999-01-01'}}},
        ]
        for settings in cases:
            with self.subTest(settings=settings):
                self.assert_same(settings, mid)


if __name__ == '__main__':
    unittest.main()
