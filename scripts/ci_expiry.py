#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""만료가 다가온 라이선스를 찾아낸다 — 알림 워크플로에서 호출한다.

licenses/*.key 를 읽어 서명을 검증하고, 곧 만료되거나 이미 만료된 것을 골라
JSON 으로 내보낸다. 공개키만 있으면 되므로 시크릿이 필요 없다.

    python scripts/ci_expiry.py --within 7 --out expiring.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Crypto.PublicKey import ECC          # noqa: E402

from app.licensing.token import parse     # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts'))
from license_admin import autorenew_days_for   # noqa: E402

LICENSES_DIR = ROOT / 'licenses'
REVOCATION_FILE = ROOT / 'revoked.json'


def revoked_ids() -> set[str]:
    if not REVOCATION_FILE.exists():
        return set()
    try:
        data = json.loads(REVOCATION_FILE.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return set()
    ids = data.get('revoked')
    return {str(item) for item in ids} if isinstance(ids, list) else set()


def collect(within_days: int) -> list[dict]:
    """곧 만료되거나 이미 만료된 라이선스. 철회된 것은 뺀다."""
    from app.licensing.public_key import PUBLIC_KEY_PEM
    if not PUBLIC_KEY_PEM.strip():
        return []
    public_key = ECC.import_key(PUBLIC_KEY_PEM)

    revoked = revoked_ids()
    now = int(time.time())
    cutoff = now + within_days * 86400

    found = []
    for path in sorted(LICENSES_DIR.glob('*.key')):
        try:
            license_obj = parse(path.read_text(encoding='utf-8'), public_key)
        except Exception:
            continue   # 읽을 수 없는 파일은 조용히 건너뛴다

        if license_obj.license_id in revoked or license_obj.machine_id in revoked:
            continue
        if license_obj.expires_at > cutoff:
            continue
        if autorenew_days_for(license_obj.machine_id) is not None:
            continue   # 자동 갱신되므로 알릴 필요가 없다

        seconds_left = license_obj.expires_at - now
        found.append({
            'machine_id': license_obj.machine_id,
            'license_id': license_obj.license_id,
            'name': license_obj.name or '-',
            'expires_at': license_obj.expires_at,
            'expires_str': datetime.fromtimestamp(
                license_obj.expires_at, timezone.utc).strftime('%Y-%m-%d'),
            'days_left': max(0, -(-seconds_left // 86400)) if seconds_left > 0 else 0,
            'expired': seconds_left <= 0,
        })

    found.sort(key=lambda item: item['expires_at'])
    return found


def main() -> int:
    parser = argparse.ArgumentParser(description='만료 임박 라이선스 찾기')
    parser.add_argument('--within', type=int, default=7, help='며칠 안쪽을 볼지 (기본 7)')
    parser.add_argument('--out', help='결과 JSON 을 쓸 파일 (없으면 표준출력)')
    args = parser.parse_args()

    entries = collect(args.within)
    payload = json.dumps(entries, ensure_ascii=False, indent=2)

    if args.out:
        Path(args.out).write_text(payload + '\n', encoding='utf-8')
    else:
        print(payload)

    print(f'만료 임박/만료: {len(entries)}건', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
