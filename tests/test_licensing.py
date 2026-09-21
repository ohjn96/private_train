# -*- coding: utf-8 -*-
"""라이선스 게이트 회귀 테스트.

네트워크를 타지 않는다. 철회 목록 조회는 가짜 함수로 대체한다.

실행:
    python -m unittest discover -s tests -v
"""
import json
import os
import sys
import tempfile
import time
import unittest
from pathlib import Path
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from Crypto.PublicKey import ECC

from app.licensing import config, policy as policy_mod, store, token as token_mod
from app.licensing.machine import normalize
from app.licensing.token import LicenseError, build_payload, parse, sign

MACHINE = 'A1B2-C3D4-E5F6-7890'
OTHER_MACHINE = 'FFFF-EEEE-DDDD-CCCC'


def make_token(key, *, machine_id=MACHINE, days=30, license_id='test0001',
               issued_at=None, name='테스터', note='', data=None):
    now = int(time.time()) if issued_at is None else issued_at
    payload = build_payload(
        license_id=license_id,
        machine_id=machine_id,
        issued_at=now,
        expires_at=now + days * 86400,
        name=name,
        note=note,
        data=data,
    )
    return sign(payload, key)


def enforcing_policy():
    """라이선스 검사가 켜진 상태로 고정하는 patcher.

    기본 정책은 open(검사 안 함)이라, 검사 동작을 보는 테스트는 이걸 걸어야 한다.
    """
    return mock.patch('app.licensing.current_policy',
                      return_value=policy_mod.Policy(mode=policy_mod.LICENSED, seq=1))


class TokenTest(unittest.TestCase):
    """서명·파싱 자체의 성질."""

    @classmethod
    def setUpClass(cls):
        cls.key = ECC.generate(curve='Ed25519')
        cls.pub = cls.key.public_key()

    def test_roundtrip(self):
        license_obj = parse(make_token(self.key), self.pub)
        self.assertEqual(license_obj.machine_id, MACHINE)
        self.assertEqual(license_obj.name, '테스터')
        self.assertEqual(license_obj.days_left, 30)

    def test_tampered_payload_is_rejected(self):
        """만료일을 늘리려고 payload 를 고치면 서명이 깨진다."""
        original = make_token(self.key, days=1)
        prefix, payload_b64, sig = original.split('.')

        import base64
        raw = base64.urlsafe_b64decode(payload_b64 + '=' * (-len(payload_b64) % 4))
        data = json.loads(raw)
        data['exp'] = data['exp'] + 365 * 86400
        forged = base64.urlsafe_b64encode(
            json.dumps(data, sort_keys=True, separators=(',', ':')).encode()
        ).decode().rstrip('=')

        with self.assertRaises(LicenseError) as ctx:
            parse(f'{prefix}.{forged}.{sig}', self.pub)
        self.assertEqual(ctx.exception.code, 'bad_signature')

    def test_other_key_cannot_forge(self):
        """개인키가 없으면 유효한 토큰을 만들 수 없다."""
        attacker = ECC.generate(curve='Ed25519')
        forged = make_token(attacker, days=9999)
        with self.assertRaises(LicenseError) as ctx:
            parse(forged, self.pub)
        self.assertEqual(ctx.exception.code, 'bad_signature')

    def test_malformed_inputs(self):
        for bad in ('', '   ', 'nonsense', 'TRAIN1.only-two-parts', 'OTHER1.a.b'):
            with self.assertRaises(LicenseError):
                parse(bad, self.pub)

    def test_whitespace_is_tolerated(self):
        """메일 클라이언트가 줄바꿈을 끼워넣어도 읽힌다."""
        raw = make_token(self.key)
        wrapped = '\n'.join(raw[i:i + 40] for i in range(0, len(raw), 40))
        self.assertEqual(parse(wrapped, self.pub).machine_id, MACHINE)


class VerifyTest(unittest.TestCase):
    """머신 바인딩·만료·시계 되돌리기·철회까지 포함한 전체 검사."""

    def setUp(self):
        self.key = ECC.generate(curve='Ed25519')
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)

        self.env = mock.patch.dict(os.environ, {
            'TRAIN_LICENSE_DIR': str(base / 'data'),
            'TRAIN_LICENSE_SHADOW': str(base / 'shadow'),
        })
        self.env.start()

        import app.licensing as licensing
        self.licensing = licensing
        licensing.invalidate_cache()

        self.patches = [
            enforcing_policy(),
            mock.patch.object(licensing, '_public_key', return_value=self.key.public_key()),
            mock.patch.object(licensing, 'machine_id', return_value=MACHINE),
            mock.patch.object(licensing.revocation, 'revoked_ids', return_value=set()),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        self.licensing.invalidate_cache()
        self.tmp.cleanup()

    def test_valid_license_activates(self):
        license_obj = self.licensing.activate(make_token(self.key))
        self.assertEqual(license_obj.machine_id, MACHINE)
        self.assertTrue(self.licensing.current_status(refresh=True).valid)

    def test_license_for_another_machine_is_rejected(self):
        with self.assertRaises(LicenseError) as ctx:
            self.licensing.activate(make_token(self.key, machine_id=OTHER_MACHINE))
        self.assertEqual(ctx.exception.code, 'machine_mismatch')

    def test_expired_license_is_rejected(self):
        expired = make_token(self.key, days=1, issued_at=int(time.time()) - 10 * 86400)
        with self.assertRaises(LicenseError) as ctx:
            self.licensing.activate(expired)
        self.assertEqual(ctx.exception.code, 'expired')

    def test_status_is_missing_without_license(self):
        status = self.licensing.current_status(refresh=True)
        self.assertFalse(status.valid)
        self.assertEqual(status.code, 'missing')

    def test_deactivate_clears(self):
        self.licensing.activate(make_token(self.key))
        self.licensing.deactivate()
        self.assertFalse(self.licensing.current_status(refresh=True).valid)

    def test_revoked_license_is_rejected(self):
        self.licensing.activate(make_token(self.key, license_id='deadbeef'))
        with mock.patch.object(self.licensing.revocation, 'revoked_ids',
                               return_value={'deadbeef'}):
            status = self.licensing.current_status(refresh=True)
        self.assertFalse(status.valid)
        self.assertEqual(status.code, 'revoked')

    def test_revoking_a_machine_blocks_every_license_on_it(self):
        self.licensing.activate(make_token(self.key, license_id='aaaa1111'))
        with mock.patch.object(self.licensing.revocation, 'revoked_ids',
                               return_value={MACHINE}):
            self.assertEqual(self.licensing.current_status(refresh=True).code, 'revoked')

    def test_clock_rollback_is_detected(self):
        self.licensing.activate(make_token(self.key))

        # 앱이 미래 시점까지 본 적이 있다고 기록해두고, 현재 시각이 그보다
        # 한참 과거이면 (= 시계를 되돌렸으면) 거부해야 한다
        future = int(time.time()) + 400 * 86400
        store.touch_last_seen(future)

        status = self.licensing.current_status(refresh=True)
        self.assertFalse(status.valid)
        self.assertEqual(status.code, 'clock_rollback')

    def test_small_clock_drift_is_tolerated(self):
        self.licensing.activate(make_token(self.key))
        store.touch_last_seen(int(time.time()) + int(config.CLOCK_ROLLBACK_TOLERANCE / 2))
        self.assertTrue(self.licensing.current_status(refresh=True).valid)

    def test_last_seen_survives_state_file_deletion(self):
        """주 상태 파일을 지워도 보조 기록으로 시계 되돌리기를 잡는다."""
        self.licensing.activate(make_token(self.key))
        future = int(time.time()) + 400 * 86400
        store.touch_last_seen(future)

        store.state_path().unlink()
        self.assertGreaterEqual(store.last_seen(), future)


class RevocationListTest(unittest.TestCase):
    """철회 목록도 서명돼 있어야 한다."""

    def setUp(self):
        self.key = ECC.generate(curve='Ed25519')
        from app.licensing import revocation
        self.revocation = revocation

    def _list_token(self, key, ids):
        now = int(time.time())
        payload = build_payload(
            license_id=self.revocation.REVOCATION_LIST_ID,
            machine_id='*',
            issued_at=now,
            expires_at=now + 86400,
            note=','.join(ids),
        )
        return sign(payload, key)

    def test_signed_list_parses(self):
        token = self._list_token(self.key, ['aaaa', 'bbbb'])
        ids = self.revocation._parse_list(token, self.key.public_key())
        self.assertEqual(ids, {'aaaa', 'bbbb'})

    def test_unsigned_list_is_rejected(self):
        """남이 만든 가짜 철회 목록으로 멀쩡한 사용자를 막을 수 없다."""
        attacker = ECC.generate(curve='Ed25519')
        token = self._list_token(attacker, ['victim'])
        with self.assertRaises(LicenseError):
            self.revocation._parse_list(token, self.key.public_key())

    def test_license_token_is_not_accepted_as_a_list(self):
        token = make_token(self.key, license_id='notalist')
        with self.assertRaises(LicenseError) as ctx:
            self.revocation._parse_list(token, self.key.public_key())
        self.assertEqual(ctx.exception.code, 'not_a_list')


class MachineIdTest(unittest.TestCase):

    def test_normalize_formats_and_uppercases(self):
        self.assertEqual(normalize('a1b2c3d4e5f67890'), MACHINE)
        self.assertEqual(normalize('a1b2-c3d4-e5f6-7890'), MACHINE)
        self.assertEqual(normalize(' A1B2 C3D4 E5F6 7890 '), MACHINE)

    def test_machine_id_is_stable(self):
        from app.licensing.machine import machine_id
        self.assertEqual(machine_id(), machine_id())
        self.assertRegex(machine_id(), r'^[0-9A-F]{4}(-[0-9A-F]{4}){3}$')


if __name__ == '__main__':
    unittest.main()


class ApprovalCommandTest(unittest.TestCase):
    """GitHub 댓글 명령 해석 (scripts/ci_approve.py).

    남이 쓴 문자열을 다루므로, 인정하는 형식을 좁게 유지하는 게 핵심이다.
    """

    @classmethod
    def setUpClass(cls):
        scripts = Path(__file__).resolve().parent.parent / 'scripts'
        sys.path.insert(0, str(scripts))
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


class AutoActivationTest(unittest.TestCase):
    """승인된 키를 앱이 스스로 받아 등록하는 경로."""

    def setUp(self):
        self.key = ECC.generate(curve='Ed25519')
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)

        self.env = mock.patch.dict(os.environ, {
            'TRAIN_LICENSE_DIR': str(base / 'data'),
            'TRAIN_LICENSE_SHADOW': str(base / 'shadow'),
        })
        self.env.start()

        import app.licensing as licensing
        self.licensing = licensing
        licensing.invalidate_cache()

        self.patches = [
            enforcing_policy(),
            mock.patch.object(licensing, '_public_key', return_value=self.key.public_key()),
            mock.patch.object(licensing, 'machine_id', return_value=MACHINE),
            mock.patch.object(licensing.revocation, 'revoked_ids', return_value=set()),
        ]
        for p in self.patches:
            p.start()

    def tearDown(self):
        for p in self.patches:
            p.stop()
        self.env.stop()
        self.licensing.invalidate_cache()
        self.tmp.cleanup()

    def test_approved_key_activates_without_user_action(self):
        token = make_token(self.key)
        with mock.patch.object(self.licensing.delivery, 'fetch_issued_token',
                               return_value=token):
            status = self.licensing.try_auto_activate()
        self.assertTrue(status.valid)

    def test_nothing_published_yet(self):
        with mock.patch.object(self.licensing.delivery, 'fetch_issued_token',
                               return_value=None):
            status = self.licensing.try_auto_activate()
        self.assertFalse(status.valid)
        self.assertEqual(status.code, 'missing')

    def test_published_key_for_another_machine_is_not_accepted(self):
        """배포 경로가 엉뚱한 키를 주더라도 서명·머신 검사를 통과하지 못한다."""
        token = make_token(self.key, machine_id=OTHER_MACHINE)
        with mock.patch.object(self.licensing.delivery, 'fetch_issued_token',
                               return_value=token):
            status = self.licensing.try_auto_activate()
        self.assertFalse(status.valid)

    def test_forged_key_from_delivery_is_rejected(self):
        attacker = ECC.generate(curve='Ed25519')
        token = make_token(attacker, days=9999)
        with mock.patch.object(self.licensing.delivery, 'fetch_issued_token',
                               return_value=token):
            status = self.licensing.try_auto_activate()
        self.assertFalse(status.valid)

    def test_renewal_replaces_the_old_key(self):
        self.licensing.activate(make_token(self.key, days=1, license_id='old'))
        renewed = make_token(self.key, days=90, license_id='new')
        with mock.patch.object(self.licensing.delivery, 'fetch_issued_token',
                               return_value=renewed):
            status = self.licensing.try_auto_activate()
        self.assertTrue(status.valid)
        self.assertEqual(status.license.license_id, 'new')
        self.assertEqual(status.days_left, 90)


class PolicySwitchTest(unittest.TestCase):
    """원격 스위치 — 지금은 꺼져 있고, 발급자가 켜면 잠긴다."""

    def setUp(self):
        self.key = ECC.generate(curve='Ed25519')
        self.tmp = tempfile.TemporaryDirectory()
        base = Path(self.tmp.name)
        self.env = mock.patch.dict(os.environ, {
            'TRAIN_LICENSE_DIR': str(base / 'data'),
            'TRAIN_LICENSE_SHADOW': str(base / 'shadow'),
        })
        self.env.start()

        import app.licensing as licensing
        self.licensing = licensing
        licensing.invalidate_cache()

        self.machine = mock.patch.object(licensing, 'machine_id', return_value=MACHINE)
        self.machine.start()
        self.pub = mock.patch.object(licensing, '_public_key',
                                     return_value=self.key.public_key())
        self.pub.start()

    def tearDown(self):
        self.pub.stop()
        self.machine.stop()
        self.env.stop()
        self.licensing.invalidate_cache()
        self.tmp.cleanup()

    def _policy_token(self, mode, seq, key=None, message=''):
        now = int(time.time())
        payload = build_payload(
            license_id=policy_mod.POLICY_ID,
            machine_id='*',
            issued_at=now,
            expires_at=now + 86400,
            data={'mode': mode, 'seq': seq, 'message': message},
        )
        return sign(payload, key or self.key)

    def _serve(self, token):
        """정책 파일을 그 토큰으로 내려주는 척한다. None 이면 네트워크 실패."""
        body = None if token is None else json.dumps({'token': token})
        return mock.patch.object(
            policy_mod, '_fetch',
            side_effect=lambda url: (json.loads(body)['token'] if body else None),
        )

    # ------------------------------------------------------------------ 기본값

    def test_no_policy_file_means_open(self):
        """정책을 못 받아오면 아무도 막지 않는다 (지금 상태)."""
        with self._serve(None):
            status = self.licensing.current_status(refresh=True)
        self.assertTrue(status.valid)
        self.assertFalse(status.enforced)
        self.assertEqual(status.code, 'not_enforced')

    def test_open_policy_allows_without_license(self):
        with self._serve(self._policy_token(policy_mod.OPEN, 1)):
            status = self.licensing.current_status(refresh=True, force_policy=True)
        self.assertTrue(status.valid)
        self.assertFalse(status.enforced)

    # ------------------------------------------------------------------ 켜기

    def test_licensed_policy_locks_the_app(self):
        with self._serve(self._policy_token(policy_mod.LICENSED, 1)):
            status = self.licensing.current_status(refresh=True, force_policy=True)
        self.assertFalse(status.valid)
        self.assertEqual(status.code, 'missing')

    def test_blocked_policy_blocks_even_with_a_license(self):
        with mock.patch.object(self.licensing.revocation, 'revoked_ids', return_value=set()), \
             self._serve(self._policy_token(policy_mod.LICENSED, 1)):
            self.licensing.activate(make_token(self.key))
            self.assertTrue(self.licensing.current_status(refresh=True).valid)

        with self._serve(self._policy_token(policy_mod.BLOCKED, 2, message='점검 중')):
            status = self.licensing.current_status(refresh=True, force_policy=True)
        self.assertFalse(status.valid)
        self.assertEqual(status.code, 'blocked')
        self.assertEqual(status.message, '점검 중')

    # ------------------------------------------------------------------ 우회 방지

    def test_going_offline_does_not_undo_the_switch(self):
        """스위치를 본 뒤 인터넷을 끊어도 잠긴 상태가 유지된다."""
        with self._serve(self._policy_token(policy_mod.LICENSED, 1)):
            self.licensing.current_status(refresh=True, force_policy=True)

        with self._serve(None):
            status = self.licensing.current_status(refresh=True, force_policy=True)
        self.assertFalse(status.valid)
        self.assertEqual(status.code, 'missing')

    def test_old_policy_cannot_be_replayed(self):
        """낮은 seq 의 옛 정책을 다시 들이밀어 잠금을 풀 수 없다."""
        with self._serve(self._policy_token(policy_mod.LICENSED, 5)):
            self.licensing.current_status(refresh=True, force_policy=True)

        with self._serve(self._policy_token(policy_mod.OPEN, 2)):
            status = self.licensing.current_status(refresh=True, force_policy=True)
        self.assertFalse(status.valid)   # seq 2 는 거부되고 seq 5 가 유지된다

    def test_newer_policy_can_unlock(self):
        with self._serve(self._policy_token(policy_mod.LICENSED, 5)):
            self.licensing.current_status(refresh=True, force_policy=True)

        with self._serve(self._policy_token(policy_mod.OPEN, 6)):
            status = self.licensing.current_status(refresh=True, force_policy=True)
        self.assertTrue(status.valid)
        self.assertFalse(status.enforced)

    def test_forged_policy_is_ignored(self):
        """남이 서명한 정책으로 남을 차단시킬 수 없다."""
        attacker = ECC.generate(curve='Ed25519')
        with self._serve(self._policy_token(policy_mod.BLOCKED, 9, key=attacker)):
            status = self.licensing.current_status(refresh=True, force_policy=True)
        self.assertTrue(status.valid)
        self.assertFalse(status.enforced)

    def test_license_token_is_not_accepted_as_a_policy(self):
        with self._serve(make_token(self.key)):
            status = self.licensing.current_status(refresh=True, force_policy=True)
        self.assertTrue(status.valid)
        self.assertFalse(status.enforced)

    def test_unknown_mode_is_ignored(self):
        with self._serve(self._policy_token('nonsense', 3)):
            status = self.licensing.current_status(refresh=True, force_policy=True)
        self.assertTrue(status.valid)
