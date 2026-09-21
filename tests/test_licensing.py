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
    source='remote' 로 두는 이유: 'builtin' 이면 "정책을 확인 못 함"으로 보고
    'offline' 안내를 내보내기 때문이다.
    """
    return mock.patch(
        'app.licensing.current_policy',
        return_value=policy_mod.Policy(mode=policy_mod.LICENSED, seq=1, source='remote'))


def builtin_open():
    """빌드에 박힌 오프라인 기본값을 open 으로 고정하는 patcher.

    저장소에 실제로 박혀 있는 값이 무엇이든 테스트가 흔들리지 않게 한다.
    """
    return mock.patch.object(
        policy_mod, 'built_in',
        return_value=policy_mod.Policy(mode=policy_mod.OPEN, seq=0, source='builtin'))


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
            builtin_open(),
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
            builtin_open(),
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
        self.builtin = builtin_open()
        self.builtin.start()

    def tearDown(self):
        self.builtin.stop()
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


class BuiltInPolicyTest(unittest.TestCase):
    """빌드에 박히는 오프라인 기본값.

    이게 없으면 "인터넷을 막고 첫 실행"만으로 라이선스 검사를 건너뛸 수 있다.
    """

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
        self.pub = mock.patch.object(licensing, '_public_key',
                                     return_value=self.key.public_key())
        self.pub.start()
        self.machine = mock.patch.object(licensing, 'machine_id', return_value=MACHINE)
        self.machine.start()

    def tearDown(self):
        self.machine.stop()
        self.pub.stop()
        self.env.stop()
        self.licensing.invalidate_cache()
        self.tmp.cleanup()

    def _baked(self, mode, seq=1):
        return mock.patch.object(
            policy_mod, 'built_in',
            return_value=policy_mod.Policy(mode=mode, seq=seq, source='builtin'))

    def _offline(self):
        return mock.patch.object(policy_mod, '_fetch', return_value=None)

    def _serving(self, mode, seq):
        now = int(time.time())
        payload = build_payload(
            license_id=policy_mod.POLICY_ID, machine_id='*',
            issued_at=now, expires_at=now + 86400,
            data={'mode': mode, 'seq': seq, 'message': ''})
        token = sign(payload, self.key)
        return mock.patch.object(policy_mod, '_fetch', return_value=token)

    def test_offline_first_run_falls_back_to_builtin(self):
        with self._baked('licensed'), self._offline():
            status = self.licensing.current_status(refresh=True, force_policy=True)
        self.assertFalse(status.valid)
        self.assertEqual(status.code, 'offline')

    def test_builtin_open_still_allows_offline(self):
        """기본값이 open 인 빌드는 예전처럼 그냥 돌아간다 (하위 호환)."""
        with self._baked('open', seq=0), self._offline():
            status = self.licensing.current_status(refresh=True, force_policy=True)
        self.assertTrue(status.valid)
        self.assertFalse(status.enforced)

    def test_remote_open_can_release_a_locked_build(self):
        """박힌 licensed 를, seq 가 더 큰 공개 정책이 풀어줄 수 있다."""
        with self._baked('licensed', seq=1), self._serving('open', 2):
            status = self.licensing.current_status(refresh=True, force_policy=True)
        self.assertTrue(status.valid)
        self.assertFalse(status.enforced)

    def test_older_open_policy_cannot_release_it(self):
        """낮은 seq 의 open 을 들이밀어 박힌 잠금을 풀 수는 없다."""
        with self._baked('licensed', seq=5), self._serving('open', 2):
            status = self.licensing.current_status(refresh=True, force_policy=True)
        self.assertFalse(status.valid)

    def test_policy_source_is_reported(self):
        with self._baked('licensed', seq=1), self._offline():
            self.assertEqual(self.licensing.current_policy(force=True).source, 'builtin')
        with self._baked('licensed', seq=1), self._serving('licensed', 2):
            self.assertEqual(self.licensing.current_policy(force=True).source, 'remote')

    def test_offline_message_differs_from_plain_missing(self):
        """오프라인 때문에 잠긴 것과 라이선스가 없는 것은 안내가 달라야 한다."""
        with self._baked('licensed', seq=1), self._offline():
            offline = self.licensing.current_status(refresh=True, force_policy=True)
        self.licensing.invalidate_cache()
        with self._baked('licensed', seq=1), self._serving('licensed', 2):
            missing = self.licensing.current_status(refresh=True, force_policy=True)
        self.assertEqual(offline.code, 'offline')
        self.assertEqual(missing.code, 'missing')
        self.assertNotEqual(offline.message, missing.message)


class BakePolicyToolTest(unittest.TestCase):
    """scripts/bake_policy.py — 오프라인 기본값을 코드에 박는 쪽."""

    @classmethod
    def setUpClass(cls):
        scripts = Path(__file__).resolve().parent.parent / 'scripts'
        sys.path.insert(0, str(scripts))
        import bake_policy
        cls.tool = bake_policy

    def test_reads_the_module_from_disk(self):
        """import 캐시가 아니라 파일에서 읽어야 한다 (같은 프로세스에서 다시 쓰므로)."""
        mode, seq, _ = self.tool.current()
        self.assertIn(mode, policy_mod.VALID_MODES)
        self.assertIsInstance(seq, int)

    def test_roundtrip_preserves_other_values(self):
        original = self.tool.current()
        try:
            self.tool.write('blocked', 41, '점검')
            self.assertEqual(self.tool.current(), ('blocked', 41, '점검'))
        finally:
            self.tool.write(*original)
        self.assertEqual(self.tool.current(), original)

    def test_sync_keeps_mode_and_does_not_lower_seq(self):
        """배포 저장소가 낮은 seq 를 주더라도 내려가면 안 된다.
        앱이 낮은 seq 를 거부하므로, 내려가면 정상 정책이 거절당한다."""
        original = self.tool.current()
        try:
            self.tool.write('licensed', 9, '')
            with mock.patch.object(self.tool, 'fetch_remote',
                                   return_value={'mode': 'open', 'seq': 3}):
                mode, seq = self.tool.sync()
            self.assertEqual((mode, seq), ('licensed', 9))
            self.assertEqual(self.tool.current()[:2], ('licensed', 9))
        finally:
            self.tool.write(*original)

    def test_sync_takes_a_higher_seq(self):
        original = self.tool.current()
        try:
            self.tool.write('licensed', 2, '')
            with mock.patch.object(self.tool, 'fetch_remote',
                                   return_value={'mode': 'open', 'seq': 7}):
                mode, seq = self.tool.sync()
            self.assertEqual((mode, seq), ('licensed', 7))   # 모드는 유지, seq 만 따라간다
        finally:
            self.tool.write(*original)

    def test_sync_survives_a_failed_fetch(self):
        original = self.tool.current()
        try:
            self.tool.write('licensed', 5, '')
            with mock.patch.object(self.tool, 'fetch_remote', return_value=None):
                self.assertEqual(self.tool.sync(), ('licensed', 5))
        finally:
            self.tool.write(*original)
