# -*- coding: utf-8 -*-
"""코레일 로그인에 쓰는 AES-CBC 암호화.

pycryptodome(C 확장)이 있으면 그걸 쓰고, 없으면(iPhone 처럼 C 확장을 싣기 어려운 곳)
순수 파이썬 pyaes 로 같은 결과를 낸다. 두 방식의 출력은 바이트 단위로 같다
(tests/test_aes_backends.py).
"""
BLOCK_SIZE = 16

try:
    from Crypto.Cipher import AES as _AES

    BACKEND = 'pycryptodome'

    def cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
        return _AES.new(key, _AES.MODE_CBC, iv=iv).encrypt(data)

except ImportError:  # pragma: no cover - 백엔드에 따라 한쪽만 돈다
    import pyaes as _pyaes

    BACKEND = 'pyaes'

    def cbc_encrypt(key: bytes, iv: bytes, data: bytes) -> bytes:
        mode = _pyaes.AESModeOfOperationCBC(key, iv=iv)
        return b''.join(mode.encrypt(data[i:i + BLOCK_SIZE]) for i in range(0, len(data), BLOCK_SIZE))


def pad(data: bytes, block_size: int = BLOCK_SIZE) -> bytes:
    """PKCS#7 패딩 (pycryptodome 의 Crypto.Util.Padding.pad 와 같다)."""
    n = block_size - len(data) % block_size
    return data + bytes([n]) * n
