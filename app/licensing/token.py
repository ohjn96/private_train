# -*- coding: utf-8 -*-
"""라이선스 토큰의 형식·서명·검증.

형식:  TRAIN1.<payload_b64url>.<signature_b64url>

payload 는 UTF-8 JSON 이고, 서명은 그 **바이트 그대로**에 대해 Ed25519 로 만든다.
(JSON 을 다시 직렬화하지 않고 원본 바이트를 검증하므로 키 순서 같은 것에 영향받지 않는다.)

검증에 필요한 것은 공개키뿐이라 exe 에 넣어도 안전하다.
발급은 개인키를 가진 사람만 할 수 있다 → scripts/issue_license.py
"""
from __future__ import annotations

import base64
import binascii
import json
import time
from dataclasses import dataclass

from Crypto.PublicKey import ECC
from Crypto.Signature import eddsa

PREFIX = 'TRAIN1'
CURRENT_VERSION = 1


class LicenseError(Exception):
    """라이선스가 유효하지 않을 때. message 는 사용자에게 그대로 보여준다."""

    def __init__(self, message: str, code: str = 'invalid'):
        super().__init__(message)
        self.message = message
        self.code = code


def _b64e(raw: bytes) -> str:
    return base64.urlsafe_b64encode(raw).decode('ascii').rstrip('=')


def _b64d(text: str) -> bytes:
    padding = '=' * (-len(text) % 4)
    try:
        return base64.urlsafe_b64decode(text + padding)
    except (binascii.Error, ValueError) as exc:
        raise LicenseError('라이선스 키 형식이 올바르지 않습니다.', 'malformed') from exc


@dataclass(frozen=True)
class License:
    """검증을 통과한 라이선스."""
    license_id: str
    machine_id: str
    issued_at: int
    expires_at: int
    name: str
    note: str
    data: dict

    @property
    def seconds_left(self) -> int:
        return self.expires_at - int(time.time())

    @property
    def days_left(self) -> int:
        # 남은 초를 올림해서 "오늘까지 유효"를 1일로 보이게 한다
        return max(0, -(-self.seconds_left // 86400))

    def to_public_dict(self) -> dict:
        return {
            'license_id': self.license_id,
            'machine_id': self.machine_id,
            'name': self.name,
            'note': self.note,
            'issued_at': self.issued_at,
            'expires_at': self.expires_at,
            'days_left': self.days_left,
        }


def build_payload(
    *,
    license_id: str,
    machine_id: str,
    issued_at: int,
    expires_at: int,
    name: str = '',
    note: str = '',
    data: dict | None = None,
) -> bytes:
    """서명 대상이 되는 정규 payload 바이트."""
    payload = {
        'v': CURRENT_VERSION,
        'lid': license_id,
        'mid': machine_id,
        'iat': issued_at,
        'exp': expires_at,
    }
    if name:
        payload['name'] = name
    if note:
        payload['note'] = note
    if data:
        payload['d'] = data
    return json.dumps(payload, ensure_ascii=False, sort_keys=True,
                      separators=(',', ':')).encode('utf-8')


def sign(payload: bytes, private_key: ECC.EccKey) -> str:
    """payload 를 서명해 완성된 토큰 문자열로 만든다. (발급자 전용)"""
    signature = eddsa.new(private_key, 'rfc8032').sign(payload)
    return f'{PREFIX}.{_b64e(payload)}.{_b64e(signature)}'


def load_public_key(pem_or_raw: str) -> ECC.EccKey:
    return ECC.import_key(pem_or_raw)


def parse(token: str, public_key: ECC.EccKey) -> License:
    """토큰을 검증하고 License 로 돌려준다. 실패하면 LicenseError.

    여기서는 서명과 형식만 본다. 머신 ID 일치·만료·철회는 verify() 가 본다.
    """
    cleaned = ''.join((token or '').split())
    if not cleaned:
        raise LicenseError('라이선스 키가 비어 있습니다.', 'empty')

    parts = cleaned.split('.')
    if len(parts) != 3 or parts[0] != PREFIX:
        raise LicenseError('라이선스 키 형식이 올바르지 않습니다.', 'malformed')

    payload = _b64d(parts[1])
    signature = _b64d(parts[2])

    try:
        eddsa.new(public_key, 'rfc8032').verify(payload, signature)
    except (ValueError, TypeError) as exc:
        raise LicenseError('서명이 올바르지 않습니다. 발급받은 키를 그대로 붙여넣었는지 확인해주세요.',
                           'bad_signature') from exc

    try:
        data = json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise LicenseError('라이선스 내용을 읽을 수 없습니다.', 'malformed') from exc

    if data.get('v') != CURRENT_VERSION:
        raise LicenseError('지원하지 않는 라이선스 버전입니다. 앱을 업데이트해주세요.', 'version')

    try:
        return License(
            license_id=str(data['lid']),
            machine_id=str(data['mid']),
            issued_at=int(data['iat']),
            expires_at=int(data['exp']),
            name=str(data.get('name', '')),
            note=str(data.get('note', '')),
            data=data.get('d') if isinstance(data.get('d'), dict) else {},
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise LicenseError('라이선스 내용이 불완전합니다.', 'malformed') from exc
