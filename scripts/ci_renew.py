#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""자동 갱신 대상을 찾는다 — 갱신 워크플로에서 호출한다.

licenses/autorenew.json 에서 켜둔 머신 중, 만료가 임박한 것만 골라낸다.
실제 발급은 워크플로가 license_admin.py issue 로 한다 (개인키가 필요하므로).

    python scripts/ci_renew.py --within 3 --out renew.json
"""
from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Crypto.PublicKey import ECC                                    # noqa: E402

from app.licensing.token import parse                              # noqa: E402

sys.path.insert(0, str(ROOT / 'scripts'))
from license_admin import (                                          # noqa: E402
    LICENSES_DIR, autorenew_days_for, read_autorenew,
)
from ci_expiry import revoked_ids                                    # noqa: E402


def collect(within_days: int) -> list[dict]:
    """갱신해야 할 라이선스 목록."""
    from app.licensing.public_key import PUBLIC_KEY_PEM
    if not PUBLIC_KEY_PEM.strip():
        return []
    public_key = ECC.import_key(PUBLIC_KEY_PEM)

    settings = read_autorenew()
    revoked = revoked_ids()
    cutoff = int(time.time()) + within_days * 86400

    due = []
    for path in sorted(LICENSES_DIR.glob('*.key')):
        try:
            license_obj = parse(path.read_text(encoding='utf-8'), public_key)
        except Exception:
            continue

        if license_obj.license_id in revoked or license_obj.machine_id in revoked:
            continue
        if license_obj.expires_at > cutoff:
            continue

        days = autorenew_days_for(license_obj.machine_id, settings)
        if days is None:
            continue   # 자동 갱신 대상이 아니다 → 만료 알림 쪽에서 처리한다

        due.append({
            'machine_id': license_obj.machine_id,
            'name': license_obj.name or '',
            'days': days,
            'old_license_id': license_obj.license_id,
            'old_expires_at': license_obj.expires_at,
        })

    return due


def main() -> int:
    parser = argparse.ArgumentParser(description='자동 갱신 대상 찾기')
    parser.add_argument('--within', type=int, default=3, help='남은 일수 기준 (기본 3)')
    parser.add_argument('--out', help='결과 JSON 을 쓸 파일')
    args = parser.parse_args()

    due = collect(args.within)
    payload = json.dumps(due, ensure_ascii=False, indent=2)

    if args.out:
        Path(args.out).write_text(payload + '\n', encoding='utf-8')
    else:
        print(payload)

    print(f'자동 갱신 대상: {len(due)}건', file=sys.stderr)
    return 0


if __name__ == '__main__':
    sys.exit(main())
