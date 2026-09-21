#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""라이선스 발급 도구 — **발급자(저장소 주인) 전용**.

개인키를 다루므로 이 스크립트는 당신 PC 에서만 돌린다. 개인키는 저장소 바깥
(기본: ~/.config/private_train/license_signing_key.pem)에 두며, 절대 커밋하지 않는다.
개인키가 새면 누구나 무제한 라이선스를 만들 수 있으니 유출 시 keygen 부터 다시 한다.

사용법
------
  최초 1회 — 키 만들기 (공개키는 app/licensing/public_key.py 에 자동 기록)
      python scripts/license_admin.py keygen

  라이선스 발급
      python scripts/license_admin.py issue --machine-id A1B2-C3D4-E5F6-7890 \
                                            --days 30 --name "홍길동"

  발급 내역 보기
      python scripts/license_admin.py list

  철회 (revoked.json 을 다시 써준다 → 커밋/푸시하면 적용)
      python scripts/license_admin.py revoke --license-id 3f9a21c4
      python scripts/license_admin.py revoke --machine-id A1B2-C3D4-E5F6-7890
"""
from __future__ import annotations

import argparse
import json
import os
import re
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from Crypto.PublicKey import ECC                      # noqa: E402

from app.licensing.machine import normalize           # noqa: E402
from app.licensing.policy import POLICY_ID, VALID_MODES  # noqa: E402
from app.licensing.revocation import REVOCATION_LIST_ID  # noqa: E402
from app.licensing.token import build_payload, parse, sign  # noqa: E402

# 개인키와 발급 대장은 저장소 바깥에 둔다 (공개 저장소이므로)
ADMIN_DIR = Path(os.environ.get('LICENSE_ADMIN_DIR',
                                Path.home() / '.config' / 'private_train'))
PRIVATE_KEY_PATH = ADMIN_DIR / 'license_signing_key.pem'
LEDGER_PATH = ADMIN_DIR / 'issued.jsonl'

PUBLIC_KEY_MODULE = ROOT / 'app' / 'licensing' / 'public_key.py'
REVOCATION_FILE = ROOT / 'revoked.json'

# 승인된 라이선스가 놓이는 곳. 앱이 여기서 자기 머신 ID 파일을 받아 자동 등록한다.
# 머신에 묶여 있어 공개돼도 남이 쓸 수 없다.
LICENSES_DIR = ROOT / 'licenses'
LICENSES_INDEX = LICENSES_DIR / 'index.json'

# 라이선스 검사를 켜고 끄는 원격 스위치
POLICY_FILE = ROOT / 'license-policy.json'
POLICY_VALID_DAYS = 3650

# 자동 갱신 설정. 앱은 이 파일을 보지 않는다 — Actions 만 읽으므로 서명이 필요 없다.
AUTORENEW_FILE = LICENSES_DIR / 'autorenew.json'
AUTORENEW_DEFAULT_DAYS = 30

# 철회 목록 토큰의 유효기간 (주기적으로 다시 서명하게 만들어, 오래된 목록이
# 영원히 재사용되는 것을 막는다)
REVOCATION_VALID_DAYS = 3650


# --------------------------------------------------------------------------- 키

def _load_private_key() -> ECC.EccKey:
    if not PRIVATE_KEY_PATH.exists():
        sys.exit(f'개인키가 없습니다: {PRIVATE_KEY_PATH}\n'
                 f'먼저 `python scripts/license_admin.py keygen` 을 실행하세요.')
    return ECC.import_key(PRIVATE_KEY_PATH.read_text(encoding='utf-8'))


def cmd_keygen(args: argparse.Namespace) -> None:
    if PRIVATE_KEY_PATH.exists() and not args.force:
        sys.exit(f'이미 개인키가 있습니다: {PRIVATE_KEY_PATH}\n'
                 '정말 새로 만들려면 --force 를 주세요. '
                 '(기존에 발급한 라이선스는 전부 무효가 됩니다)')

    key = ECC.generate(curve='Ed25519')

    ADMIN_DIR.mkdir(parents=True, exist_ok=True)
    PRIVATE_KEY_PATH.write_text(key.export_key(format='PEM'), encoding='utf-8')
    try:
        PRIVATE_KEY_PATH.chmod(0o600)
    except OSError:
        pass

    public_pem = key.public_key().export_key(format='PEM')
    _write_public_key_module(public_pem)

    print(f'개인키 : {PRIVATE_KEY_PATH}  ← 백업하고, 절대 공유하지 마세요')
    print(f'공개키 : {PUBLIC_KEY_MODULE}  ← 저장소에 커밋하세요')
    print()
    print('개인키를 잃어버리면 새 라이선스를 발급할 수 없고, 새로 만들면 기존 라이선스가')
    print('전부 무효가 됩니다. 암호화된 백업을 반드시 남겨두세요.')


def _write_public_key_module(public_pem: str) -> None:
    body = json.dumps(public_pem)   # 줄바꿈이 들어있어 repr 대신 JSON 문자열로
    source = PUBLIC_KEY_MODULE.read_text(encoding='utf-8')
    marker = 'PUBLIC_KEY_PEM = '
    start = source.index(marker)
    end = source.index('\n', start)
    updated = source[:start] + marker + body + source[end:]
    PUBLIC_KEY_MODULE.write_text(updated, encoding='utf-8')


# --------------------------------------------------------------------------- 발급

def _append_ledger(record: dict) -> None:
    ADMIN_DIR.mkdir(parents=True, exist_ok=True)
    with LEDGER_PATH.open('a', encoding='utf-8') as fh:
        fh.write(json.dumps(record, ensure_ascii=False) + '\n')


def _read_ledger() -> list[dict]:
    if not LEDGER_PATH.exists():
        return []
    records = []
    for line in LEDGER_PATH.read_text(encoding='utf-8').splitlines():
        line = line.strip()
        if not line:
            continue
        try:
            records.append(json.loads(line))
        except json.JSONDecodeError:
            continue
    return records


def cmd_issue(args: argparse.Namespace) -> None:
    machine_id = normalize(args.machine_id)
    if len(machine_id) != 19:    # XXXX-XXXX-XXXX-XXXX
        sys.exit(f'머신 ID 형식이 이상합니다: {args.machine_id!r}\n'
                 '사용자 화면에 보이는 16자리 값을 그대로 넣어주세요.')

    if args.days <= 0:
        sys.exit('--days 는 1 이상이어야 합니다.')

    key = _load_private_key()
    now = int(time.time())
    expires = now + args.days * 86400
    license_id = secrets.token_hex(4)

    payload = build_payload(
        license_id=license_id,
        machine_id=machine_id,
        issued_at=now,
        expires_at=expires,
        name=args.name or '',
        note=args.note or '',
    )
    token = sign(payload, key)

    _append_ledger({
        'license_id': license_id,
        'machine_id': machine_id,
        'name': args.name or '',
        'email': args.email or '',
        'note': args.note or '',
        'issued_at': now,
        'expires_at': expires,
        'days': args.days,
    })

    if args.publish:
        LICENSES_DIR.mkdir(parents=True, exist_ok=True)
        (LICENSES_DIR / f'{machine_id}.key').write_text(token + '\n', encoding='utf-8')
        write_index()

    if args.out:
        Path(args.out).write_text(token + '\n', encoding='utf-8')

    expires_str = datetime.fromtimestamp(expires, timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')
    print('=' * 72)
    print(f'라이선스 ID : {license_id}')
    print(f'머신 ID     : {machine_id}')
    print(f'대상        : {args.name or "-"}')
    print(f'만료        : {expires_str} ({args.days}일)')
    print('=' * 72)
    print()
    print(token)
    print()
    print('위 한 줄을 그대로 회신하세요. 사용자는 앱의 라이선스 화면에 붙여넣으면 됩니다.')


# --------------------------------------------------------------------------- 공개 목록

def write_index() -> None:
    """licenses/*.key 를 훑어 사람이 보기 좋은 목록을 만든다.

    토큰 자체에 모든 정보가 들어있으므로 이 파일은 편의용이다. 앱은 참조하지 않는다.
    """
    from app.licensing.public_key import PUBLIC_KEY_PEM
    if not PUBLIC_KEY_PEM.strip():
        return
    public_key = ECC.import_key(PUBLIC_KEY_PEM)

    LICENSES_DIR.mkdir(parents=True, exist_ok=True)
    entries = []
    for path in sorted(LICENSES_DIR.glob('*.key')):
        try:
            license_obj = parse(path.read_text(encoding='utf-8'), public_key)
        except Exception:
            continue
        entries.append({
            'machine_id': license_obj.machine_id,
            'license_id': license_obj.license_id,
            'name': license_obj.name,
            'issued_at': license_obj.issued_at,
            'expires_at': license_obj.expires_at,
        })

    LICENSES_INDEX.write_text(
        json.dumps({'count': len(entries), 'licenses': entries},
                   ensure_ascii=False, indent=2) + '\n',
        encoding='utf-8',
    )


def cmd_unpublish(args: argparse.Namespace) -> None:
    machine_id = normalize(args.machine_id)
    path = LICENSES_DIR / f'{machine_id}.key'
    if not path.exists():
        sys.exit(f'그런 키가 없습니다: {path}')
    path.unlink()
    write_index()
    print(f'{path} 삭제. 커밋하면 적용됩니다.')
    print('이미 등록된 PC 는 계속 동작하므로, 차단하려면 revoke 를 쓰세요.')


# --------------------------------------------------------------------------- 조회

def cmd_list(args: argparse.Namespace) -> None:
    records = _read_ledger()
    if not records:
        print('발급 내역이 없습니다.')
        return

    revoked = _current_revoked_ids()
    now = int(time.time())

    print(f'{"ID":10} {"머신 ID":21} {"이름":12} {"만료":17} 상태')
    print('-' * 76)
    for record in records:
        expires = record.get('expires_at', 0)
        when = datetime.fromtimestamp(expires, timezone.utc).astimezone().strftime('%Y-%m-%d %H:%M')
        if record.get('license_id') in revoked or record.get('machine_id') in revoked:
            state = '철회됨'
        elif expires <= now:
            state = '만료'
        else:
            state = f'유효 ({(expires - now) // 86400}일)'
        print(f'{record.get("license_id", ""):10} {record.get("machine_id", ""):21} '
              f'{(record.get("name") or "-")[:12]:12} {when:17} {state}')


# --------------------------------------------------------------------------- 철회

def _current_revoked_ids() -> set[str]:
    """revoked.json 에 이미 들어있는 식별자들. 서명 검증 없이 읽는다(내 파일이므로)."""
    if not REVOCATION_FILE.exists():
        return set()
    try:
        data = json.loads(REVOCATION_FILE.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return set()
    ids = data.get('revoked')
    return {str(item) for item in ids} if isinstance(ids, list) else set()


def _write_revocation_file(ids: set[str], key: ECC.EccKey) -> None:
    now = int(time.time())
    payload = build_payload(
        license_id=REVOCATION_LIST_ID,
        machine_id='*',
        issued_at=now,
        expires_at=now + REVOCATION_VALID_DAYS * 86400,
        note=','.join(sorted(ids)),
    )
    token = sign(payload, key)

    # 'revoked' 는 사람이 읽기 위한 사본이고, 앱이 신뢰하는 것은 서명된 'token' 뿐이다.
    REVOCATION_FILE.write_text(
        json.dumps(
            {
                'updated': datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d'),
                'revoked': sorted(ids),
                'token': token,
            },
            ensure_ascii=False, indent=2,
        ) + '\n',
        encoding='utf-8',
    )


def cmd_revoke(args: argparse.Namespace) -> None:
    targets = set()
    if args.license_id:
        targets.add(args.license_id.strip())
    if args.machine_id:
        targets.add(normalize(args.machine_id))
    if not targets:
        sys.exit('--license-id 또는 --machine-id 중 하나는 지정해야 합니다.')

    key = _load_private_key()
    ids = _current_revoked_ids()

    if args.undo:
        removed = targets & ids
        if not removed:
            sys.exit('철회 목록에 없는 대상입니다.')
        ids -= targets
        action = f'철회 해제: {", ".join(sorted(removed))}'
    else:
        ids |= targets
        action = f'철회: {", ".join(sorted(targets))}'

    _write_revocation_file(ids, key)

    # 철회한 머신이 자동 갱신 대상이면 꺼둔다. 안 그러면 다음 갱신 때 되살아난다.
    if not args.undo and args.machine_id:
        settings = read_autorenew()
        machine_id = normalize(args.machine_id)
        if autorenew_days_for(machine_id, settings) is not None:
            settings['machines'][machine_id] = {'off': True}
            write_autorenew(settings)
            print(f'{machine_id}: 자동 갱신도 함께 껐습니다.')

    print(action)
    print(f'{REVOCATION_FILE} 를 갱신했습니다. 커밋 후 푸시하면 적용됩니다:')
    print(f'    git add {REVOCATION_FILE.name} && git commit -m "chore: 라이선스 철회 목록 갱신" && git push')
    print()
    print('앱은 최대 24시간 캐시를 쓰므로, 반영까지 하루 정도 걸릴 수 있습니다.')


# --------------------------------------------------------------------------- 자동 갱신

def read_autorenew() -> dict:
    """자동 갱신 설정. 없으면 '아무도 자동 갱신 안 함'."""
    if not AUTORENEW_FILE.exists():
        return {'all': False, 'default_days': AUTORENEW_DEFAULT_DAYS, 'machines': {}}
    try:
        data = json.loads(AUTORENEW_FILE.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {'all': False, 'default_days': AUTORENEW_DEFAULT_DAYS, 'machines': {}}
    if not isinstance(data, dict):
        return {'all': False, 'default_days': AUTORENEW_DEFAULT_DAYS, 'machines': {}}

    data.setdefault('all', False)
    data.setdefault('default_days', AUTORENEW_DEFAULT_DAYS)
    if not isinstance(data.get('machines'), dict):
        data['machines'] = {}
    return data


def write_autorenew(data: dict) -> None:
    LICENSES_DIR.mkdir(parents=True, exist_ok=True)
    AUTORENEW_FILE.write_text(
        json.dumps(data, ensure_ascii=False, indent=2, sort_keys=True) + '\n',
        encoding='utf-8')


def autorenew_days_for(machine_id: str, settings: dict | None = None) -> int | None:
    """이 머신을 자동 갱신한다면 몇 일짜리로? 대상이 아니면 None."""
    settings = read_autorenew() if settings is None else settings
    entry = settings['machines'].get(machine_id)

    if entry is None:
        if not settings.get('all'):
            return None
        entry = {}

    if entry.get('off'):
        return None

    until = entry.get('until')
    if until:
        try:
            deadline = datetime.strptime(str(until), '%Y-%m-%d').replace(tzinfo=timezone.utc)
        except ValueError:
            deadline = None
        if deadline and datetime.now(timezone.utc) > deadline:
            return None   # 자동 갱신 유효기간이 끝났다

    days = entry.get('days', settings.get('default_days', AUTORENEW_DEFAULT_DAYS))
    try:
        days = int(days)
    except (TypeError, ValueError):
        days = AUTORENEW_DEFAULT_DAYS
    return max(1, days)


def cmd_autorenew(args: argparse.Namespace) -> None:
    settings = read_autorenew()

    # 전역 스위치
    if args.all is not None:
        settings['all'] = args.all
        write_autorenew(settings)
        state = '켬 (모든 라이선스)' if args.all else '끔'
        print(f'전역 자동 갱신: {state}')
        print(f'{AUTORENEW_FILE} 를 커밋하면 적용됩니다.')
        return

    # 조회
    if not args.machine_id:
        print(f'전역 자동 갱신 : {"켜짐" if settings["all"] else "꺼짐"}')
        print(f'기본 기간      : {settings["default_days"]}일')
        if not settings['machines']:
            print('개별 설정      : 없음')
            return
        print('개별 설정      :')
        for mid, entry in sorted(settings['machines'].items()):
            if entry.get('off'):
                print(f'  {mid}  자동 갱신 안 함')
            else:
                until = f', {entry["until"]} 까지' if entry.get('until') else ''
                print(f'  {mid}  {entry.get("days", settings["default_days"])}일마다{until}')
        return

    # 개별 설정
    machine_id = normalize(args.machine_id)
    if args.off:
        settings['machines'][machine_id] = {'off': True}
        print(f'{machine_id}: 자동 갱신 끔')
    else:
        entry = {'days': args.days or settings['default_days']}
        if args.until:
            entry['until'] = args.until
        settings['machines'][machine_id] = entry
        until = f' ({args.until} 까지)' if args.until else ''
        print(f'{machine_id}: {entry["days"]}일마다 자동 갱신{until}')

    write_autorenew(settings)
    print(f'{AUTORENEW_FILE} 를 커밋하면 적용됩니다.')


# --------------------------------------------------------------------------- 정책 스위치

def _read_policy() -> dict:
    if not POLICY_FILE.exists():
        return {}
    try:
        data = json.loads(POLICY_FILE.read_text(encoding='utf-8'))
    except (OSError, json.JSONDecodeError):
        return {}
    return data if isinstance(data, dict) else {}


def cmd_policy(args: argparse.Namespace) -> None:
    current = _read_policy()

    if args.mode is None:
        mode = current.get('mode', 'open')
        seq = current.get('seq', 0)
        print(f'현재 정책 : {mode} (seq {seq})')
        if not POLICY_FILE.exists():
            print('  license-policy.json 이 없습니다 → 앱은 open 으로 동작합니다.')
        if current.get('message'):
            print(f'  안내 문구: {current["message"]}')
        return

    key = _load_private_key()
    now = int(time.time())

    # seq 는 항상 올라간다. 앱이 낮은 seq 를 거부하므로 옛 정책으로 되돌릴 수 없다.
    seq = int(current.get('seq', 0)) + 1

    payload = build_payload(
        license_id=POLICY_ID,
        machine_id='*',
        issued_at=now,
        expires_at=now + POLICY_VALID_DAYS * 86400,
        data={'mode': args.mode, 'seq': seq, 'message': args.message or ''},
    )
    token = sign(payload, key)

    POLICY_FILE.write_text(
        json.dumps(
            {
                'updated': datetime.fromtimestamp(now, timezone.utc).strftime('%Y-%m-%d'),
                'mode': args.mode,
                'seq': seq,
                'message': args.message or '',
                'token': token,
            },
            ensure_ascii=False, indent=2,
        ) + '\n',
        encoding='utf-8',
    )

    explain = {
        'open': '라이선스 검사를 하지 않습니다. 누구나 쓸 수 있습니다.',
        'licensed': '라이선스가 있어야 쓸 수 있습니다.',
        'blocked': '전면 차단됩니다. 라이선스가 있어도 막힙니다.',
    }[args.mode]

    # 오프라인 기본값도 갱신하되, 느슨해지는 방향으로는 자동으로 내리지 않는다.
    # (공개 정책을 open 으로 풀어도, 정책을 못 받은 설치는 잠긴 채로 두기 위해서)
    floor = baked_mode()
    if STRICTNESS[args.mode] >= STRICTNESS[floor]:
        bake_policy(args.mode)
        floor_note = f'오프라인 기본값도 {args.mode} 로 맞췄습니다.'
    else:
        bake_policy(floor)      # 모드는 유지하고 seq 만 따라 올린다
        floor_note = (f'오프라인 기본값은 {floor} 로 유지합니다 '
                      f'(정책을 못 받은 설치는 계속 잠깁니다). '
                      f'정말 풀려면: license_admin.py bake --mode {args.mode}')

    print(f'정책을 {args.mode} (seq {seq}) 로 설정했습니다.')
    print(f'  → {explain}')
    print(f'  {floor_note}')
    print()
    print(f'{POLICY_FILE.name} 을 커밋/푸시해야 적용됩니다:')
    print(f'    git add {POLICY_FILE.name} && git commit -m "chore: 라이선스 정책 {args.mode}" && git push')
    print()
    print('앱은 6시간마다, 그리고 로그인할 때마다 확인합니다.')
    print('raw.githubusercontent.com CDN 캐시 때문에 최대 5분쯤 더 걸릴 수 있습니다.')


# --------------------------------------------------------------------------- 빌드 시점 정책

BUILT_IN_MODULE = ROOT / 'app' / 'licensing' / 'built_in_policy.py'

# 느슨함 < 엄격함. 박히는 값은 "정책을 확인 못 했을 때 가정할 상태"이므로
# 공개 정책을 풀어준다고 해서 같이 풀리면 안 된다.
STRICTNESS = {'open': 0, 'licensed': 1, 'blocked': 2}


def baked_mode() -> str:
    """지금 박혀 있는 오프라인 기본값. (import 캐시를 피해 파일에서 직접 읽는다)"""
    match = re.search(r'^BUILT_IN_MODE = "([^"]*)"',
                      BUILT_IN_MODULE.read_text(encoding='utf-8'), re.M)
    return match.group(1) if match else 'open'


def bake_policy(mode: str | None = None) -> tuple[str, int]:
    """오프라인 기본값을 built_in_policy.py 에 박는다.

    mode 를 주지 않으면 현재 license-policy.json 의 모드를 쓴다.
    seq 는 항상 현재 정책의 것을 따라간다 (옛 정책 재사용 방지의 하한선).

    이 값이 있어야, 인터넷을 막고 처음 실행하는 것만으로 검사를 건너뛰지 못한다.
    """
    current = _read_policy()
    seq = int(current.get('seq', 0) or 0)
    mode = str(current.get('mode', 'open')) if mode is None else mode
    message = str(current.get('message', ''))

    source = BUILT_IN_MODULE.read_text(encoding='utf-8')
    for key, value in (('BUILT_IN_MODE', mode),
                       ('BUILT_IN_SEQ', seq),
                       ('BUILT_IN_MESSAGE', message)):
        literal = json.dumps(value, ensure_ascii=False)
        source = re.sub(rf'^{key} = .*$', f'{key} = {literal}', source, flags=re.M)
    BUILT_IN_MODULE.write_text(source, encoding='utf-8')
    return mode, seq


def cmd_bake(args: argparse.Namespace) -> None:
    if args.mode is None and not args.sync:
        current = _read_policy()
        print(f'오프라인 기본값 : {baked_mode()}  ({BUILT_IN_MODULE.name})')
        print(f'공개 정책       : {current.get("mode", "open")} (seq {current.get("seq", 0)})')
        print()
        print('바꾸려면 --mode open|licensed|blocked, 공개 정책과 맞추려면 --sync')
        return

    mode, seq = bake_policy(args.mode)
    print(f'오프라인 기본값을 {mode} (seq {seq}) 로 박았습니다 → {BUILT_IN_MODULE.name}')
    if mode == 'open':
        print('  주의: 인터넷을 막고 첫 실행하면 검사를 건너뜁니다.')
    else:
        print('  정책을 못 받은 설치도 잠긴 상태로 시작합니다.')
    print('  다음 빌드부터 적용됩니다. 커밋하는 것도 잊지 마세요.')


# --------------------------------------------------------------------------- 검사

def cmd_inspect(args: argparse.Namespace) -> None:
    """발급한 토큰을 사람이 읽을 수 있게 풀어본다 (서명 검증 포함)."""
    from app.licensing.public_key import PUBLIC_KEY_PEM
    if not PUBLIC_KEY_PEM.strip():
        sys.exit('공개키가 설정되어 있지 않습니다. keygen 을 먼저 실행하세요.')

    license_obj = parse(args.token, ECC.import_key(PUBLIC_KEY_PEM))
    print(json.dumps(license_obj.to_public_dict(), ensure_ascii=False, indent=2))


# --------------------------------------------------------------------------- 진입점

def main() -> None:
    parser = argparse.ArgumentParser(
        description='라이선스 발급 도구 (발급자 전용)',
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )
    sub = parser.add_subparsers(dest='command', required=True)

    p_keygen = sub.add_parser('keygen', help='서명 키쌍 생성 (최초 1회)')
    p_keygen.add_argument('--force', action='store_true', help='기존 키를 덮어쓴다')
    p_keygen.set_defaults(func=cmd_keygen)

    p_issue = sub.add_parser('issue', help='라이선스 발급')
    p_issue.add_argument('--machine-id', required=True, help='사용자 화면에 표시된 머신 ID')
    p_issue.add_argument('--days', type=int, default=30, help='유효 기간(일). 기본 30')
    p_issue.add_argument('--name', default='', help='발급 대상 이름/메모')
    p_issue.add_argument('--email', default='', help='연락처 (대장에만 기록, 토큰에는 안 들어감)')
    p_issue.add_argument('--note', default='', help='토큰에 함께 넣을 짧은 메모')
    p_issue.add_argument('--publish', action='store_true',
                         help='licenses/<머신ID>.key 로 기록해 앱이 자동 수령하게 한다')
    p_issue.add_argument('--out', help='토큰만 이 파일에 쓴다 (자동화용)')
    p_issue.set_defaults(func=cmd_issue)

    p_index = sub.add_parser('index', help='licenses/ 를 훑어 index.json 을 다시 쓴다')
    p_index.set_defaults(func=lambda a: write_index())

    p_unpublish = sub.add_parser('unpublish', help='licenses/ 에서 특정 머신의 키를 삭제')
    p_unpublish.add_argument('--machine-id', required=True)
    p_unpublish.set_defaults(func=cmd_unpublish)

    p_list = sub.add_parser('list', help='발급 내역')
    p_list.set_defaults(func=cmd_list)

    p_revoke = sub.add_parser('revoke', help='라이선스 철회')
    p_revoke.add_argument('--license-id', help='철회할 라이선스 ID')
    p_revoke.add_argument('--machine-id', help='머신 ID 통째로 차단')
    p_revoke.add_argument('--undo', action='store_true', help='철회 해제')
    p_revoke.set_defaults(func=cmd_revoke)

    p_auto = sub.add_parser('autorenew', help='만료 임박 시 자동 갱신 설정')
    p_auto.add_argument('--machine-id', help='대상 머신 (없으면 현재 설정만 보여준다)')
    p_auto.add_argument('--days', type=int, help='갱신할 기간(일). 기본 30')
    p_auto.add_argument('--until', help='이 날짜까지만 자동 갱신 (YYYY-MM-DD)')
    p_auto.add_argument('--off', action='store_true', help='이 머신의 자동 갱신 끄기')
    p_auto.add_argument('--all', dest='all', action='store_const', const=True,
                        help='모든 라이선스를 자동 갱신 대상으로')
    p_auto.add_argument('--no-all', dest='all', action='store_const', const=False,
                        help='전역 자동 갱신 끄기')
    p_auto.set_defaults(func=cmd_autorenew, all=None)

    p_policy = sub.add_parser('policy', help='라이선스 검사를 켜고 끄는 원격 스위치')
    p_policy.add_argument('--mode', choices=list(VALID_MODES),
                          help='open=검사 안 함, licensed=라이선스 필요, blocked=전면 차단. '
                               '생략하면 현재 상태만 보여준다')
    p_policy.add_argument('--message', default='', help='차단 화면에 띄울 안내 문구')
    p_policy.set_defaults(func=cmd_policy)

    p_bake = sub.add_parser(
        'bake', help='정책을 못 받았을 때 쓸 오프라인 기본값 (빌드에 박힌다)')
    p_bake.add_argument('--mode', choices=list(VALID_MODES),
                        help='오프라인 기본값. 생략하면 현재 상태만 보여준다')
    p_bake.add_argument('--sync', action='store_true',
                        help='공개 정책과 똑같이 맞춘다')
    p_bake.set_defaults(func=cmd_bake)

    p_inspect = sub.add_parser('inspect', help='토큰 내용 확인')
    p_inspect.add_argument('token', help='TRAIN1....')
    p_inspect.set_defaults(func=cmd_inspect)

    args = parser.parse_args()
    args.func(args)


if __name__ == '__main__':
    main()
