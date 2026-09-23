# -*- coding: utf-8 -*-
"""코레일 로그인 암호화: pycryptodome 과 순수 파이썬(pyaes, iPhone 용)이 같은 결과를 내는지."""
import importlib
import os
import sys
import unittest
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))


def load_backend(block_crypto: bool):
    import korail2._aes as aes
    if block_crypto:
        with mock.patch.dict(sys.modules, {'Crypto': None, 'Crypto.Cipher': None}):
            return importlib.reload(aes)
    return importlib.reload(aes)


class AesBackendTest(unittest.TestCase):
    def tearDown(self):
        load_backend(False)

    def test_same_output_both_backends(self):
        from Crypto.Cipher import AES
        from Crypto.Util.Padding import pad as crypto_pad
        key = b'2485dd54d9deaa36'
        cases = [b'AD1790000000000', 'my-비밀번호!'.encode(), b'x' * 16, b'']
        fast = load_backend(False)
        self.assertEqual(fast.BACKEND, 'pycryptodome')
        slow = load_backend(True)
        self.assertEqual(slow.BACKEND, 'pyaes')
        for data in cases:
            expected = AES.new(key, AES.MODE_CBC, iv=key).encrypt(crypto_pad(data, 16))
            self.assertEqual(slow.pad(data), crypto_pad(data, 16))
            self.assertEqual(slow.cbc_encrypt(key, key, slow.pad(data)), expected, data)
            self.assertEqual(fast.cbc_encrypt(key, key, fast.pad(data)), expected, data)


if __name__ == '__main__':
    unittest.main()
