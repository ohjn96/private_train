# -*- coding: utf-8 -*-
"""라이선스 게이트.

바깥에서 쓰는 것은 이 네 가지다.

    machine_id()          이 PC 의 머신 ID (사용자가 발급자에게 알려줄 값)
    current_status()      지금 라이선스 상태
    activate(token)       사용자가 붙여넣은 키를 검증하고 저장
    deactivate()          저장된 키 삭제

정직하게 적어두는 한계: 이 검사는 사용자의 PC 에서 돌아간다. exe 를 뜯어
검사를 건너뛰는 것까지는 막지 못한다. 목표는 "받은 exe 를 그대로 남에게
넘겨도 쓸 수 없게" 만드는 것이다.
"""
from __future__ import annotations

import threading
import time
from dataclasses import dataclass

from app.licensing import config, delivery, policy as policy_mod, revocation, store
from app.licensing.machine import machine_id, normalize
from app.licensing.public_key import PUBLIC_KEY_PEM, is_configured
from app.licensing.token import License, LicenseError, load_public_key, parse

__all__ = [
    'LicenseError', 'LicenseStatus',
    'machine_id', 'normalize', 'current_status', 'current_policy',
    'activate', 'deactivate', 'try_auto_activate', 'invalidate_cache',
]

# 상태를 매 요청마다 다시 계산하지 않기 위한 짧은 캐시
_CACHE_TTL = 30.0
_lock = threading.Lock()
_cached: tuple[float, 'LicenseStatus'] | None = None


@dataclass(frozen=True)
class LicenseStatus:
    valid: bool
    code: str                      # 'ok' | 'missing' | 'expired' | ... (실패 사유)
    message: str = ''
    license: License | None = None
    enforced: bool = True          # 정책이 'licensed' 라서 실제로 막고 있는지

    @property
    def days_left(self) -> int:
        return self.license.days_left if self.license else 0

    @property
    def expiring_soon(self) -> bool:
        return (self.valid and self.enforced and self.license is not None
                and self.days_left <= config.EXPIRY_WARNING_DAYS)


def _public_key():
    if not is_configured():
        raise LicenseError(
            '이 빌드에는 라이선스 공개키가 들어있지 않습니다. '
            '배포자에게 정식 빌드를 요청해주세요.',
            'no_public_key',
        )
    return load_public_key(PUBLIC_KEY_PEM)


def _check(token: str) -> License:
    """토큰 하나를 끝까지 검사한다. 통과하면 License, 아니면 LicenseError."""
    key = _public_key()
    license_obj = parse(token, key)

    if normalize(license_obj.machine_id) != machine_id():
        raise LicenseError(
            '이 라이선스는 다른 PC 앞으로 발급된 것입니다. '
            f'이 PC 의 머신 ID 는 {machine_id()} 입니다.',
            'machine_mismatch',
        )

    now = int(time.time())

    # 시계 되돌리기: 지금까지 본 가장 늦은 시각보다 과거로 돌아가 있으면 거부
    seen = store.last_seen()
    if seen and now < seen - config.CLOCK_ROLLBACK_TOLERANCE:
        raise LicenseError(
            '시스템 시계가 과거로 설정되어 있습니다. 시간을 올바르게 맞춘 뒤 다시 실행해주세요.',
            'clock_rollback',
        )

    if now >= license_obj.expires_at:
        raise LicenseError('라이선스가 만료되었습니다. 발급자에게 연장을 요청해주세요.', 'expired')

    revoked = revocation.revoked_ids(key)
    if license_obj.license_id in revoked or machine_id() in revoked:
        raise LicenseError('이 라이선스는 발급자에 의해 철회되었습니다.', 'revoked')

    store.touch_last_seen(now)
    return license_obj


def current_policy(*, force: bool = False) -> policy_mod.Policy:
    """지금 적용 중인 원격 정책.

    공개키가 없는 빌드는 정책을 검증할 수 없으므로 open 으로 본다.
    (어차피 라이선스도 검증할 수 없어서 강제할 것이 없다.)
    """
    try:
        key = _public_key()
    except LicenseError:
        return policy_mod.Policy()
    try:
        return policy_mod.current(key, force=force)
    except (ValueError, LicenseError):
        return policy_mod.Policy()


def _compute_status(*, force_policy: bool = False) -> LicenseStatus:
    active = current_policy(force=force_policy)

    if active.blocks_everything:
        return LicenseStatus(
            False, 'blocked',
            active.message or '현재 이 프로그램의 사용이 중지되었습니다.',
            enforced=True,
        )

    if not active.requires_license:
        # 검사를 켜지 않은 상태. 등록된 라이선스가 있으면 정보만 붙여준다.
        token = store.read_license()
        license_obj = None
        if token:
            try:
                license_obj = _check(token)
            except LicenseError:
                license_obj = None
        return LicenseStatus(True, 'not_enforced', license=license_obj, enforced=False)

    token = store.read_license()
    if not token:
        if active.unverified:
            # 원격 정책을 한 번도 못 받았다. 인터넷이 막혀 있을 가능성이 크다.
            return LicenseStatus(
                False, 'offline',
                '라이선스 정책을 확인하지 못했습니다. 인터넷 연결을 확인한 뒤 다시 실행해주세요.',
            )
        return LicenseStatus(False, 'missing', '라이선스가 등록되어 있지 않습니다.')
    try:
        return LicenseStatus(True, 'ok', license=_check(token))
    except LicenseError as exc:
        return LicenseStatus(False, exc.code, exc.message)


def current_status(*, refresh: bool = False, force_policy: bool = False) -> LicenseStatus:
    """현재 라이선스 상태 (짧게 캐시된다).

    force_policy 는 원격 스위치를 지금 당장 다시 확인한다. 인터넷에 붙는 게
    확실한 시점(로그인)에만 쓴다.
    """
    global _cached
    with _lock:
        if not refresh and not force_policy and _cached is not None:
            cached_at, status = _cached
            if time.monotonic() - cached_at < _CACHE_TTL:
                return status
        status = _compute_status(force_policy=force_policy)
        _cached = (time.monotonic(), status)
        return status


def invalidate_cache() -> None:
    global _cached
    with _lock:
        _cached = None


def activate(token: str) -> License:
    """사용자가 붙여넣은 키를 검증하고 저장한다. 실패하면 LicenseError."""
    license_obj = _check(token)
    store.write_license(token)
    invalidate_cache()
    return license_obj


def deactivate() -> None:
    store.clear_license()
    invalidate_cache()


def try_auto_activate() -> LicenseStatus:
    """발급자가 승인해 올려둔 키가 있으면 알아서 가져다 등록한다.

    사용자가 키를 복붙하지 않아도 되게 하는 경로다. 받아온 값도 똑같이 서명 검증을
    거치므로, 이 경로가 엉뚱한 값을 주더라도 통과하지 못한다.
    """
    token = delivery.fetch_issued_token(machine_id())
    if token and token != store.read_license():
        try:
            activate(token)
        except LicenseError:
            pass   # 아직 안 맞는 키면 그냥 현재 상태를 돌려준다
    return current_status(refresh=True)
