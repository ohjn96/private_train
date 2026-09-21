# -*- coding: utf-8 -*-
"""승인 명령 해석과 자동 갱신 판정 회귀 테스트.

네트워크를 타지 않는다.

실행:
    python -m unittest discover -s tests -v
"""
import os
import sys
import unittest
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / 'scripts'))

MACHINE = 'A1B2-C3D4-E5F6-7890'
OTHER_MACHINE = 'FFFF-EEEE-DDDD-CCCC'


class ApprovalCommandTest(unittest.TestCase):
    """GitHub 댓글 명령 해석 (scripts/ci_approve.py).

    남이 쓴 문자열을 다루므로, 인정하는 형식을 좁게 유지하는 게 핵심이다.
    """

    @classmethod
    def setUpClass(cls):
        import ci_approve
        cls.mod = ci_approve

    def test_approve_variants(self):
        self.assertEqual(self.mod.parse_command('/approve'), ('approve', 30))
        self.assertEqual(self.mod.parse_command('/approve 90'), ('approve', 90))
        self.assertEqual(self.mod.parse_command('  /approve 7  '), ('approve', 7))
        self.assertEqual(self.mod.parse_command('/APPROVE'), ('approve', 30))

    def test_other_commands(self):
        self.assertEqual(self.mod.parse_command('/deny')[0], 'deny')
        self.assertEqual(self.mod.parse_command('/revoke')[0], 'revoke')

    def test_non_commands_are_ignored(self):
        for text in ('', '고마워요', 'approve', '나중에 /approve 할게요', '/approvex'):
            self.assertIsNone(self.mod.parse_command(text), text)

    def test_days_are_clamped(self):
        self.assertEqual(self.mod.parse_command('/approve 0')[1], self.mod.MIN_DAYS)
        self.assertEqual(self.mod.parse_command('/approve 99999')[1], self.mod.MAX_DAYS)

    def test_machine_id_extraction(self):
        body = '### 머신 ID\n\na1b2-c3d4-e5f6-7890\n\n### 용도\n\n출퇴근'
        self.assertEqual(self.mod.parse_machine_id(body), MACHINE)

    def test_machine_id_missing_or_malformed(self):
        for body in ('', '머신 ID: 없음', 'A1B2-C3D4-E5F6', 'ZZZZ-ZZZZ-ZZZZ-ZZZZ'):
            self.assertIsNone(self.mod.parse_machine_id(body), body)

    # -------------------------------------------------------------- 자동 승인

    def test_write_access_requesters_are_auto_approved(self):
        """저장소 쓰기 권한자가 이슈를 열면 댓글 없이 바로 발급된다."""
        for who in ('OWNER', 'MEMBER', 'COLLABORATOR', 'collaborator'):
            self.assertEqual(self.mod.decide('issues', '', who),
                             ('approve', self.mod.TRUSTED_DAYS), who)

    def test_outsiders_are_not_auto_approved(self):
        """CONTRIBUTOR 는 PR 이 머지된 적 있을 뿐 쓰기 권한이 아니다."""
        for who in ('NONE', 'CONTRIBUTOR', 'FIRST_TIME_CONTRIBUTOR', '', 'MANNEQUIN'):
            self.assertIsNone(self.mod.decide('issues', '', who), who)

    def test_opening_an_issue_ignores_command_text(self):
        """본문에 /approve 를 적어둔다고 승인되지는 않는다."""
        self.assertIsNone(self.mod.decide('issues', '/approve 3650', 'NONE'))

    def test_comments_still_go_through_command_parsing(self):
        self.assertEqual(self.mod.decide('issue_comment', '/approve 30', 'NONE'),
                         ('approve', 30))
        self.assertIsNone(self.mod.decide('issue_comment', '고맙습니다', 'OWNER'))


class AutoRenewSettingsTest(unittest.TestCase):
    """자동 갱신 판정 (scripts/license_admin.py) — dukpy 없이도 도는 쪽."""

    @classmethod
    def setUpClass(cls):
        import license_admin
        cls.admin = license_admin

    def days_for(self, settings, machine_id=MACHINE):
        base = {'all': False, 'default_days': 30, 'machines': {}}
        base.update(settings)
        return self.admin.autorenew_days_for(machine_id, base)

    def test_off_by_default(self):
        self.assertIsNone(self.days_for({}))

    def test_enabled_for_one_machine(self):
        self.assertEqual(self.days_for({'machines': {MACHINE: {'days': 45}}}), 45)
        self.assertIsNone(self.days_for({'machines': {MACHINE: {'days': 45}}}, OTHER_MACHINE))

    def test_global_switch_with_opt_out(self):
        self.assertEqual(self.days_for({'all': True}), 30)
        self.assertIsNone(self.days_for({'all': True, 'machines': {MACHINE: {'off': True}}}))

    def test_default_days_applies_when_entry_is_empty(self):
        self.assertEqual(self.days_for({'default_days': 14, 'machines': {MACHINE: {}}}), 14)

    def test_until_date_stops_renewal(self):
        self.assertIsNone(self.days_for(
            {'machines': {MACHINE: {'days': 30, 'until': '2000-01-01'}}}))
        self.assertEqual(self.days_for(
            {'machines': {MACHINE: {'days': 30, 'until': '2999-01-01'}}}), 30)

    def test_malformed_values_fall_back(self):
        self.assertEqual(self.days_for({'machines': {MACHINE: {'days': 'abc'}}}), 30)
        self.assertEqual(self.days_for(
            {'machines': {MACHINE: {'days': 30, 'until': '엉터리'}}}), 30)

    def test_days_are_at_least_one(self):
        self.assertEqual(self.days_for({'machines': {MACHINE: {'days': 0}}}), 1)
        self.assertEqual(self.days_for({'machines': {MACHINE: {'days': -5}}}), 1)


if __name__ == '__main__':
    unittest.main()


class PolicyCommandTest(unittest.TestCase):
    """/policy — 원격에서 검사 켜고 끄기."""

    @classmethod
    def setUpClass(cls):
        import ci_approve
        cls.mod = ci_approve

    def test_modes(self):
        for mode in ('open', 'licensed', 'blocked'):
            self.assertEqual(self.mod.parse_policy(f'/policy {mode}'), mode)
        self.assertEqual(self.mod.parse_policy('/POLICY Licensed'), 'licensed')
        self.assertEqual(self.mod.parse_policy('  /policy open  '), 'open')

    def test_mode_is_required(self):
        """모드를 안 적으면 아무것도 하지 않는다 — 실수로 바꾸는 일이 없게."""
        self.assertIsNone(self.mod.parse_policy('/policy'))

    def test_unknown_mode_is_ignored(self):
        for text in ('/policy 뭐', '/policy on', '/policy off', '/policy licensed 30'):
            self.assertIsNone(self.mod.parse_policy(text), text)

    def test_not_a_policy_command(self):
        for text in ('', '/approve 30', '정책 바꿔줘', '나중에 /policy open 할게'):
            self.assertIsNone(self.mod.parse_policy(text), text)

    def test_policy_does_not_collide_with_other_commands(self):
        """/policy 는 명령 해석기에 걸리지 않아야 한다 (머신 ID 가 필요 없으므로)."""
        self.assertIsNone(self.mod.parse_command('/policy licensed'))
