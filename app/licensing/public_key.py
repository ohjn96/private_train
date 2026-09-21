# -*- coding: utf-8 -*-
"""라이선스 검증용 공개키.

이 값은 공개돼도 안전하다. 공개키로는 서명을 **검증**만 할 수 있고, 새 라이선스를
**발급**하려면 개인키가 필요하다. 개인키는 저장소에 절대 들어오면 안 된다.

값을 채우려면:  python scripts/license_admin.py keygen
"""

# scripts/license_admin.py keygen 이 이 상수를 자동으로 덮어쓴다.
PUBLIC_KEY_PEM = "-----BEGIN PUBLIC KEY-----\nMCowBQYDK2VwAyEAfNUj3dnwMcMrCMmPrnI6VT2b7doMJZswWhSsG5iiAR0=\n-----END PUBLIC KEY-----"


def is_configured() -> bool:
    return bool(PUBLIC_KEY_PEM.strip())
