# -*- coding: utf-8 -*-
"""텔레그램 봇 설정을 이 PC 에만 풀리게 저장한다.

봇 토큰은 그 봇을 완전히 조종할 수 있는 비밀이라 평문으로 두지 않는다.
머신 ID 에서 파생한 키로 AES-GCM 암호화하므로, 파일만 빼돌려 다른 PC 에서
열 수는 없다.

한계는 분명히 해두자: 같은 PC 에서는 머신 ID 를 그대로 구할 수 있으므로,
그 PC 를 장악한 사람에게는 막이 되지 않는다. 노리는 것은 "설정 파일이
백업·동기화 폴더를 타고 흘러나가는" 정도의 사고다.
"""
from __future__ import annotations

import base64
import json
import logging
from pathlib import Path

from Crypto.Cipher import AES
from Crypto.Protocol.KDF import scrypt
from Crypto.Random import get_random_bytes

from app.licensing.config import app_data_dir
from app.licensing.machine import machine_id

logger = logging.getLogger(__name__)

FILE_VERSION = 1
SALT_BYTES = 16
NONCE_BYTES = 12
KEY_BYTES = 32
# scrypt 파라미터. 앱 기동 때 한 번 도는 정도라 이 정도면 충분하다.
SCRYPT_N, SCRYPT_R, SCRYPT_P = 2 ** 14, 8, 1


def store_path() -> Path:
    return app_data_dir() / 'telegram.enc'


def _derive_key(salt: bytes) -> bytes:
    return scrypt(machine_id(), salt, key_len=KEY_BYTES,
                  N=SCRYPT_N, r=SCRYPT_R, p=SCRYPT_P)


def _b64(raw: bytes) -> str:
    return base64.b64encode(raw).decode('ascii')


def save(bot_token: str, chat_id: str | None) -> bool:
    """설정을 암호화해 저장한다. 실패해도 앱은 계속 돌아야 하므로 예외를 삼킨다."""
    if not bot_token:
        return False

    try:
        payload = json.dumps({'bot_token': bot_token, 'chat_id': chat_id or ''},
                             ensure_ascii=False).encode('utf-8')

        salt = get_random_bytes(SALT_BYTES)
        nonce = get_random_bytes(NONCE_BYTES)
        cipher = AES.new(_derive_key(salt), AES.MODE_GCM, nonce=nonce)
        ciphertext, tag = cipher.encrypt_and_digest(payload)

        path = store_path()
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps({
            'v': FILE_VERSION,
            'salt': _b64(salt),
            'nonce': _b64(nonce),
            'tag': _b64(tag),
            'data': _b64(ciphertext),
        }), encoding='utf-8')
        try:
            path.chmod(0o600)
        except OSError:
            pass          # Windows 등에서는 의미 없음
        return True
    except Exception as exc:
        logger.warning(f"텔레그램 설정을 저장하지 못했습니다: {exc}")
        return False


def load() -> dict | None:
    """저장된 설정. 없거나 이 PC 것이 아니면 None."""
    path = store_path()
    try:
        raw = json.loads(path.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return None

    if not isinstance(raw, dict) or raw.get('v') != FILE_VERSION:
        return None

    try:
        salt = base64.b64decode(raw['salt'])
        nonce = base64.b64decode(raw['nonce'])
        tag = base64.b64decode(raw['tag'])
        data = base64.b64decode(raw['data'])
    except (KeyError, ValueError, TypeError):
        return None

    try:
        cipher = AES.new(_derive_key(salt), AES.MODE_GCM, nonce=nonce)
        payload = cipher.decrypt_and_verify(data, tag)
    except (ValueError, KeyError):
        # 다른 PC 에서 만든 파일이거나 내용이 손상됐다
        logger.info("저장된 텔레그램 설정을 풀 수 없습니다 (다른 PC 의 파일일 수 있음).")
        return None

    try:
        config = json.loads(payload.decode('utf-8'))
    except (UnicodeDecodeError, json.JSONDecodeError):
        return None

    if not isinstance(config, dict) or not config.get('bot_token'):
        return None
    return {'bot_token': str(config['bot_token']),
            'chat_id': str(config.get('chat_id') or '') or None}


def clear() -> None:
    try:
        store_path().unlink()
    except OSError:
        pass
