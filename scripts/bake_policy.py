#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""오프라인 기본값을 exe 에 굽는다 — 앱(비공개) 쪽 도구.

원격 정책을 **한 번도** 받아보지 못한 설치는 기준이 없다. 이 값이 없으면
"인터넷을 막고 첫 실행"만으로 라이선스 검사를 건너뛸 수 있다.

정책 파일 자체는 공개 배포 저장소에 있으므로, seq 는 거기서 받아온다.
모드는 여기서 정한다 — 공개 정책을 open 으로 풀어도 이 값은 따라 내려가지
않아야 하기 때문이다.

    python scripts/bake_policy.py                  # 지금 박혀 있는 값 보기
    python scripts/bake_policy.py --mode licensed  # 정책을 못 받으면 잠기게
    python scripts/bake_policy.py --sync           # 배포 저장소의 seq 만 맞춤 (빌드가 호출)
"""
from __future__ import annotations

import argparse
import json
import re
import sys
import urllib.error
import urllib.request
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))

from app.licensing import config                      # noqa: E402
from app.licensing.policy import VALID_MODES          # noqa: E402

MODULE = ROOT / 'app' / 'licensing' / 'built_in_policy.py'
FETCH_TIMEOUT = 10


def current() -> tuple[str, int, str]:
    src = MODULE.read_text(encoding='utf-8')

    def grab(key, default):
        match = re.search(rf'^{key} = (.+)$', src, re.M)
        return json.loads(match.group(1)) if match else default

    return grab('BUILT_IN_MODE', 'open'), grab('BUILT_IN_SEQ', 0), grab('BUILT_IN_MESSAGE', '')


def write(mode: str, seq: int, message: str) -> None:
    src = MODULE.read_text(encoding='utf-8')
    for key, value in (('BUILT_IN_MODE', mode),
                       ('BUILT_IN_SEQ', seq),
                       ('BUILT_IN_MESSAGE', message)):
        src = re.sub(rf'^{key} = .*$', f'{key} = {json.dumps(value, ensure_ascii=False)}',
                     src, flags=re.M)
    MODULE.write_text(src, encoding='utf-8')


def fetch_remote() -> dict | None:
    """배포 저장소의 현재 정책. 받아오지 못하면 None."""
    try:
        request = urllib.request.Request(config.POLICY_URL,
                                         headers={'User-Agent': 'TrainReservation-build'})
        with urllib.request.urlopen(request, timeout=FETCH_TIMEOUT) as response:
            data = json.loads(response.read(64 * 1024).decode('utf-8'))
    except (urllib.error.URLError, OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def sync() -> tuple[str, int]:
    """모드는 그대로 두고 seq 만 배포 저장소에 맞춘다. 빌드가 부른다."""
    mode, seq, message = current()

    remote = fetch_remote()
    if remote is None:
        print(f'  경고: 정책을 받아오지 못했습니다 ({config.POLICY_URL})')
        print(f'  박혀 있던 값을 그대로 씁니다: {mode} (seq {seq})')
        return mode, seq

    remote_seq = int(remote.get('seq', 0) or 0)
    if remote_seq < seq:
        # 앱은 낮은 seq 를 거부한다. 여기서 낮추면 정상 정책이 거절당한다.
        print(f'  경고: 배포 저장소의 seq({remote_seq})가 박힌 값({seq})보다 낮습니다. 유지합니다.')
        return mode, seq

    write(mode, remote_seq, message)
    return mode, remote_seq


def main() -> int:
    parser = argparse.ArgumentParser(description='오프라인 기본값 굽기')
    parser.add_argument('--mode', choices=list(VALID_MODES),
                        help='정책을 못 받았을 때 가정할 상태')
    parser.add_argument('--sync', action='store_true',
                        help='배포 저장소의 seq 를 받아와 맞춘다 (모드는 유지)')
    args = parser.parse_args()

    if args.sync and not args.mode:
        mode, seq = sync()
        print(f'오프라인 기본값: {mode} (seq {seq})')
        return 0

    if args.mode is None:
        mode, seq, _ = current()
        remote = fetch_remote()
        print(f'오프라인 기본값 : {mode} (seq {seq})   ← {MODULE.relative_to(ROOT)}')
        if remote:
            print(f'공개 정책       : {remote.get("mode")} (seq {remote.get("seq")})')
        else:
            print('공개 정책       : 받아오지 못함')
        print()
        print('바꾸려면 --mode open|licensed|blocked')
        return 0

    _, seq, message = current()
    remote = fetch_remote()
    if remote:
        seq = max(seq, int(remote.get('seq', 0) or 0))
    write(args.mode, seq, message)

    print(f'오프라인 기본값을 {args.mode} (seq {seq}) 로 박았습니다.')
    if args.mode == 'open':
        print('  주의: 인터넷을 막고 첫 실행하면 검사를 건너뜁니다.')
    else:
        print('  정책을 못 받은 설치도 잠긴 상태로 시작합니다.')
    print('  다음 빌드부터 적용됩니다. 커밋하는 것도 잊지 마세요.')
    return 0


if __name__ == '__main__':
    sys.exit(main())
