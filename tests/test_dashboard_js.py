# -*- coding: utf-8 -*-
"""대시보드 계산 로직(docs/assets/js/model.js)을 실제로 실행해서 검증한다.

model.js 는 DOM 도 fetch 도 건드리지 않는 순수 함수 모음이라 그대로 돌려볼 수 있다.
ES 모듈 구문만 걷어내고 JS 엔진에 먹인다.

dukpy 가 없으면 건너뛴다 (테스트 전용 의존성이라 필수로 두지 않는다):
    pip install dukpy
"""
import json
import os
import re
import sys
import time
import unittest
from pathlib import Path

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

try:
    import dukpy
except ImportError:
    dukpy = None

JS_DIR = Path(__file__).resolve().parent.parent / 'docs' / 'assets' / 'js'

IMPORT_RE = re.compile(r'^\s*import\s.*?;\s*$', re.M)
EXPORT_RE = re.compile(r'^\s*export\s+(?=(?:const|function|class|let|var)\b)', re.M)


def strip_modules(source: str) -> str:
    """import 를 지우고 export 키워드를 떼어, 한 스코프에서 이어붙일 수 있게 만든다."""
    source = IMPORT_RE.sub('', source)
    return EXPORT_RE.sub('', source)


def build_bundle() -> str:
    parts = [strip_modules((JS_DIR / name).read_text(encoding='utf-8'))
             for name in ('config.js', 'model.js')]
    return '\n'.join(parts)


@unittest.skipUnless(dukpy, 'dukpy 가 설치돼 있지 않아 건너뜁니다 (pip install dukpy)')
class ModelJsTest(unittest.TestCase):
    """docs/assets/js/model.js"""

    @classmethod
    def setUpClass(cls):
        cls.bundle = build_bundle()

    def run_js(self, expression, **context):
        """번들을 로드한 뒤 표현식을 평가한다. context 는 JS 변수로 주입된다."""
        setup = '\n'.join(
            f'var {name} = {json.dumps(value, ensure_ascii=False)};'
            for name, value in context.items()
        )
        return dukpy.evaljs(f'{self.bundle}\n{setup}\n{expression}')

    # ------------------------------------------------------------------ 남은 일수

    def test_days_left(self):
        now = 1_700_000_000_000                       # ms
        in_three_days = (now // 1000) + 3 * 86400
        self.assertEqual(self.run_js(f'daysLeft({in_three_days}, {now})'), 3)
        self.assertEqual(self.run_js(f'daysLeft({now // 1000}, {now})'), 0)
        past = (now // 1000) - 86400
        self.assertLessEqual(self.run_js(f'daysLeft({past}, {now})'), 0)

    def test_format_date(self):
        self.assertEqual(self.run_js('formatDate(1700000000)'), '2023-11-14')

    # ------------------------------------------------------------------ 자동 갱신 규칙

    def test_autorenew_off_by_default(self):
        self.assertIsNone(self.run_js(
            'autoRenewDays("AAAA-0000-0000-0001", settings)',
            settings={'all': False, 'default_days': 30, 'machines': {}}))

    def test_autorenew_per_machine(self):
        settings = {'all': False, 'default_days': 30,
                    'machines': {'AAAA-0000-0000-0001': {'days': 45}}}
        self.assertEqual(self.run_js(
            'autoRenewDays("AAAA-0000-0000-0001", settings)', settings=settings), 45)
        self.assertIsNone(self.run_js(
            'autoRenewDays("BBBB-0000-0000-0002", settings)', settings=settings))

    def test_autorenew_all_with_opt_out(self):
        settings = {'all': True, 'default_days': 30,
                    'machines': {'BBBB-0000-0000-0002': {'off': True}}}
        self.assertEqual(self.run_js(
            'autoRenewDays("AAAA-0000-0000-0001", settings)', settings=settings), 30)
        self.assertIsNone(self.run_js(
            'autoRenewDays("BBBB-0000-0000-0002", settings)', settings=settings))

    def test_autorenew_expires_with_until(self):
        past = {'all': False, 'default_days': 30,
                'machines': {'AAAA-0000-0000-0001': {'days': 30, 'until': '2000-01-01'}}}
        self.assertIsNone(self.run_js(
            'autoRenewDays("AAAA-0000-0000-0001", settings)', settings=past))

        future = {'all': False, 'default_days': 30,
                  'machines': {'AAAA-0000-0000-0001': {'days': 30, 'until': '2999-01-01'}}}
        self.assertEqual(self.run_js(
            'autoRenewDays("AAAA-0000-0000-0001", settings)', settings=future), 30)

    def test_no_settings_file_means_off(self):
        self.assertIsNone(self.run_js('autoRenewDays("AAAA-0000-0000-0001", null)'))

    # ------------------------------------------------------------------ 상태 판정

    def _describe(self, days, *, revoked=False, autorenew=None, name='테스터'):
        now = int(time.time())
        license_obj = {
            'machine_id': 'AAAA-0000-0000-0001',
            'license_id': 'abcd1234',
            'name': name,
            'issued_at': now,
            'expires_at': now + int(days * 86400),
        }
        revoked_ids = ['abcd1234'] if revoked else []
        return self.run_js(
            'describe(lic, {revoked: new Set(revokedIds), autorenew: settings})',
            lic=license_obj, revokedIds=revoked_ids, settings=autorenew)

    def test_status_valid(self):
        row = self._describe(30)
        self.assertEqual(row['status'], 'valid')
        self.assertEqual(row['statusLabel'], '유효')
        self.assertFalse(row['inactive'])

    def test_status_soon(self):
        row = self._describe(2)
        self.assertEqual(row['status'], 'soon')
        self.assertEqual(row['statusLabel'], '곧 만료')

    def test_status_expired(self):
        row = self._describe(-1)
        self.assertEqual(row['status'], 'expired')
        self.assertTrue(row['inactive'])

    def test_status_revoked_beats_everything(self):
        row = self._describe(100, revoked=True)
        self.assertEqual(row['status'], 'revoked')
        self.assertTrue(row['inactive'])
        self.assertIsNone(row['autoRenew'])

    def test_renewing_soon_flag(self):
        settings = {'all': True, 'default_days': 30, 'machines': {}}
        # 2일 남음 + 자동 갱신 → 다음 실행 때 갱신된다
        row = self._describe(2, autorenew=settings)
        self.assertEqual(row['autoRenew'], 30)
        self.assertTrue(row['renewingSoon'])
        # 30일 남음 → 아직 갱신할 때가 아니다
        row = self._describe(30, autorenew=settings)
        self.assertEqual(row['autoRenew'], 30)
        self.assertFalse(row['renewingSoon'])

    def test_revoked_license_is_not_auto_renewed(self):
        settings = {'all': True, 'default_days': 30, 'machines': {}}
        row = self._describe(1, revoked=True, autorenew=settings)
        self.assertIsNone(row['autoRenew'])
        self.assertFalse(row['renewingSoon'])

    # ------------------------------------------------------------------ 유효기간 레일

    def test_remaining_fraction(self):
        """발급~만료 구간에서 남은 비율. 유효기간 레일의 길이가 된다."""
        now_ms = 1_700_000_000_000
        now_s = now_ms // 1000
        lic = {'issued_at': now_s - 30 * 86400, 'expires_at': now_s + 30 * 86400}
        self.assertAlmostEqual(
            self.run_js('remainingFraction(lic, now)', lic=lic, now=now_ms), 0.5, places=2)

    def test_remaining_fraction_is_clamped(self):
        now_ms = 1_700_000_000_000
        now_s = now_ms // 1000
        expired = {'issued_at': now_s - 60 * 86400, 'expires_at': now_s - 86400}
        self.assertEqual(self.run_js('remainingFraction(lic, now)', lic=expired, now=now_ms), 0)
        future = {'issued_at': now_s + 86400, 'expires_at': now_s + 60 * 86400}
        self.assertEqual(self.run_js('remainingFraction(lic, now)', lic=future, now=now_ms), 1)

    def test_remaining_fraction_without_issue_date(self):
        """예전 index.json 에는 발급일이 없을 수 있다 — 레일을 못 그린다."""
        self.assertIsNone(self.run_js(
            'remainingFraction(lic, now)',
            lic={'expires_at': 1_700_100_000}, now=1_700_000_000_000))

    def test_describe_carries_the_fraction(self):
        now = int(time.time())
        lic = {'machine_id': 'A1B2-C3D4-E5F6-7890', 'license_id': 'x', 'name': '',
               'issued_at': now - 10 * 86400, 'expires_at': now + 10 * 86400}
        row = self.run_js('describe(lic, {revoked: new Set()})', lic=lic)
        self.assertIsNotNone(row['remaining'])
        self.assertGreater(row['remaining'], 0.4)
        self.assertLess(row['remaining'], 0.6)

    # ------------------------------------------------------------------ 정렬 / 집계

    def test_sort_puts_inactive_last_and_urgent_first(self):
        now = int(time.time())
        rows = [
            {'machine_id': 'A', 'license_id': 'a', 'name': '', 'expires_at': now + 50 * 86400},
            {'machine_id': 'B', 'license_id': 'b', 'name': '', 'expires_at': now - 86400},
            {'machine_id': 'C', 'license_id': 'c', 'name': '', 'expires_at': now + 2 * 86400},
        ]
        order = self.run_js(
            'sortForDisplay(rows.map(function(r){return describe(r, {revoked: new Set()});}))'
            '.map(function(r){return r.machine_id;})',
            rows=rows)
        self.assertEqual(order, ['C', 'A', 'B'])

    def test_summarize(self):
        now = int(time.time())
        rows = [
            {'machine_id': 'A', 'license_id': 'a', 'name': '', 'expires_at': now + 50 * 86400},
            {'machine_id': 'B', 'license_id': 'b', 'name': '', 'expires_at': now + 2 * 86400},
            {'machine_id': 'C', 'license_id': 'c', 'name': '', 'expires_at': now - 86400},
        ]
        counts = self.run_js(
            'summarize(rows.map(function(r){return describe(r, {revoked: new Set()});}))',
            rows=rows)
        self.assertEqual(counts, {'total': 3, 'valid': 1, 'soon': 1, 'inactive': 1, 'auto': 0})

    # ------------------------------------------------------------------ 정책 배너

    def test_policy_defaults_to_open_when_missing(self):
        view = self.run_js('describePolicy(null)')
        self.assertEqual(view['mode'], 'open')
        self.assertIn('꺼짐', view['text'])

    def test_policy_modes(self):
        for mode, needle in [('licensed', '켜짐'), ('blocked', '전면 차단')]:
            view = self.run_js('describePolicy(p)', p={'mode': mode, 'seq': 3})
            self.assertEqual(view['mode'], mode)
            self.assertIn(needle, view['text'])
            self.assertEqual(view['seq'], 3)

    def test_unknown_policy_mode_falls_back_to_open(self):
        view = self.run_js('describePolicy(p)', p={'mode': 'nonsense', 'seq': 1})
        self.assertIn('꺼짐', view['text'])


@unittest.skipUnless(dukpy, 'dukpy 가 설치돼 있지 않아 건너뜁니다')
class ModelMatchesPythonTest(unittest.TestCase):
    """JS 의 자동 갱신 규칙이 Actions 쪽 파이썬 구현과 같은 답을 내는지.

    두 구현이 어긋나면 대시보드에 '자동 갱신됨'으로 보이는데 실제로는 안 되는
    상황이 생긴다.
    """

    @classmethod
    def setUpClass(cls):
        cls.bundle = build_bundle()
        sys.path.insert(0, str(Path(__file__).resolve().parent.parent / 'scripts'))

    def assert_same(self, settings, machine_id):
        from license_admin import autorenew_days_for

        js = dukpy.evaljs(
            f'{self.bundle}\n'
            f'var s = {json.dumps(settings, ensure_ascii=False)};\n'
            f'autoRenewDays({json.dumps(machine_id)}, s)')
        py = autorenew_days_for(machine_id, dict(settings))
        self.assertEqual(js, py, f'{machine_id} / {settings}')

    def test_rules_agree(self):
        cases = [
            ({'all': False, 'default_days': 30, 'machines': {}}, 'AAAA-0000-0000-0001'),
            ({'all': True, 'default_days': 30, 'machines': {}}, 'AAAA-0000-0000-0001'),
            ({'all': True, 'default_days': 30,
              'machines': {'AAAA-0000-0000-0001': {'off': True}}}, 'AAAA-0000-0000-0001'),
            ({'all': False, 'default_days': 30,
              'machines': {'AAAA-0000-0000-0001': {'days': 7}}}, 'AAAA-0000-0000-0001'),
            ({'all': False, 'default_days': 14,
              'machines': {'AAAA-0000-0000-0001': {}}}, 'AAAA-0000-0000-0001'),
            ({'all': False, 'default_days': 30,
              'machines': {'AAAA-0000-0000-0001': {'days': 30, 'until': '2000-01-01'}}},
             'AAAA-0000-0000-0001'),
            ({'all': False, 'default_days': 30,
              'machines': {'AAAA-0000-0000-0001': {'days': 30, 'until': '2999-01-01'}}},
             'AAAA-0000-0000-0001'),
        ]
        for settings, machine_id in cases:
            with self.subTest(settings=settings):
                self.assert_same(settings, machine_id)


if __name__ == '__main__':
    unittest.main()
