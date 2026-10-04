# -*- coding: utf-8 -*-
"""머신 ID — 이 PC 를 가리키는 안정적인 식별자.

라이선스는 이 값에 묶여 발급되므로, 남에게 라이선스를 넘겨도 쓸 수 없다.
OS 가 제공하는 설치 고유값을 우선 쓰고, 없으면 MAC 주소 + 호스트명으로 내려간다.
원본 값을 그대로 노출하지 않도록 SHA-256 해시의 앞 16자리만 쓴다.
"""
import hashlib
import platform
import subprocess
import sys
import uuid
from pathlib import Path

_cached: str | None = None


def _windows_machine_guid() -> str | None:
    try:
        import winreg  # type: ignore[import-not-found]
    except ImportError:
        return None
    try:
        key = winreg.OpenKey(
            winreg.HKEY_LOCAL_MACHINE,
            r'SOFTWARE\Microsoft\Cryptography',
            0,
            winreg.KEY_READ | winreg.KEY_WOW64_64KEY,
        )
        with key:
            value, _ = winreg.QueryValueEx(key, 'MachineGuid')
        return str(value) or None
    except OSError:
        return None


def _linux_machine_id() -> str | None:
    for path in ('/etc/machine-id', '/var/lib/dbus/machine-id'):
        try:
            value = Path(path).read_text(encoding='utf-8').strip()
        except OSError:
            continue
        if value:
            return value
    return None


def _macos_platform_uuid() -> str | None:
    try:
        out = subprocess.run(
            ['ioreg', '-rd1', '-c', 'IOPlatformExpertDevice'],
            capture_output=True, text=True, timeout=5, check=False,
        ).stdout
    except (OSError, subprocess.SubprocessError):
        return None
    for line in out.splitlines():
        if 'IOPlatformUUID' in line:
            # "IOPlatformUUID" = "XXXXXXXX-...."
            parts = line.split('"')
            if len(parts) >= 4:
                return parts[3]
    return None


def _raw_identifier() -> str:
    if sys.platform == 'win32':
        value = _windows_machine_guid()
    elif sys.platform == 'darwin':
        value = _macos_platform_uuid()
    else:
        value = _linux_machine_id()

    if value:
        return f'os:{value}'

    # 최후의 수단: MAC 주소 + 호스트명. 네트워크 카드가 바뀌면 달라질 수 있다.
    return f'fallback:{uuid.getnode():012x}:{platform.node()}'


def machine_id() -> str:
    """'A1B2-C3D4-E5F6-7890' 형태의 머신 ID."""
    global _cached
    if _cached is None:
        digest = hashlib.sha256(_raw_identifier().encode('utf-8')).hexdigest()[:16].upper()
        _cached = '-'.join(digest[i:i + 4] for i in range(0, 16, 4))
    return _cached


def normalize(value: str) -> str:
    """사용자가 붙여넣은 머신 ID 를 비교 가능한 형태로 정리."""
    cleaned = ''.join(ch for ch in (value or '').upper() if ch.isalnum())
    if len(cleaned) != 16:
        return cleaned
    return '-'.join(cleaned[i:i + 4] for i in range(0, 16, 4))
