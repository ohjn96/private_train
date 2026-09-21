#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""GitHub Actions 승인 처리 — 워크플로에서만 호출한다.

이슈 본문에서 머신 ID 를, 댓글에서 명령을 뽑아내고 결과를 GITHUB_OUTPUT 으로 넘긴다.
로직을 YAML 밖에 두어야 테스트할 수 있으므로 여기에 따로 뺐다.

입력은 전부 남이 쓴 문자열이라는 전제로 다룬다. 머신 ID 는 엄격한 형식 검사를
통과한 것만 쓰고, 그 외에는 셸로 흘려보내지 않는다.
"""
from __future__ import annotations

import os
import re
import sys

MACHINE_ID_RE = re.compile(r'\b([0-9A-Fa-f]{4}(?:-[0-9A-Fa-f]{4}){3})\b')
COMMAND_RE = re.compile(
    r'^\s*/(approve|deny|revoke|autorenew)\b\s*(\d+|off)?', re.IGNORECASE)

MIN_DAYS = 1
MAX_DAYS = 3650
DEFAULT_DAYS = 30


def parse_machine_id(issue_body: str) -> str | None:
    """이슈 본문에서 머신 ID 를 찾는다. 형식이 맞는 첫 값만 인정."""
    match = MACHINE_ID_RE.search(issue_body or '')
    return match.group(1).upper() if match else None


def parse_command(comment: str) -> tuple[str, int] | None:
    """'/approve 30' 같은 댓글을 (명령, 일수) 로. 명령이 아니면 None.

    '/autorenew off' 는 ('autorenew', 0) 으로 돌려준다. 0 = 끄기.
    """
    match = COMMAND_RE.match(comment or '')
    if not match:
        return None

    action = match.group(1).lower()
    argument = match.group(2)

    if argument and argument.lower() == 'off':
        if action != 'autorenew':
            return None      # off 는 autorenew 에만 쓴다
        return action, 0

    days = DEFAULT_DAYS
    if argument:
        days = max(MIN_DAYS, min(MAX_DAYS, int(argument)))
    return action, days


def emit(**outputs: str) -> None:
    """GitHub Actions 출력으로 넘긴다."""
    path = os.environ.get('GITHUB_OUTPUT')
    if not path:
        for key, value in outputs.items():
            print(f'{key}={value}')
        return
    with open(path, 'a', encoding='utf-8') as fh:
        for key, value in outputs.items():
            fh.write(f'{key}={value}\n')


def main() -> int:
    comment = os.environ.get('COMMENT_BODY', '')
    body = os.environ.get('ISSUE_BODY', '')

    parsed = parse_command(comment)
    if parsed is None:
        emit(action='none')
        return 0

    action, days = parsed

    machine_id = parse_machine_id(body)
    if machine_id is None:
        emit(action='error',
             message='이슈 본문에서 머신 ID 를 찾지 못했습니다. '
                     'A1B2-C3D4-E5F6-7890 형식인지 확인해주세요.')
        return 0

    emit(action=action, days=str(days), machine_id=machine_id)
    return 0


if __name__ == '__main__':
    sys.exit(main())
