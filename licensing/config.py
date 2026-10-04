# -*- coding: utf-8 -*-
"""라이선스 시스템 설정.

이 저장소는 공개이므로, 여기 적힌 값은 전부 남에게 보인다고 가정하고 쓴다.
비밀로 유지해야 하는 것은 **서명 개인키 하나뿐**이며 저장소 바깥에 둔다
(기본 경로: ~/.config/private_train/license_signing_key.pem).
"""
import os
import sys
from pathlib import Path

# ---------------------------------------------------------------------------
# 발급자 연락처 (라이선스 요청 메일이 갈 주소)
#   공개 저장소에 그대로 노출되므로 스팸이 싫으면 별칭 주소나 GitHub Issue 로 바꾼다.
#   런타임에 LICENSE_OWNER_EMAIL 환경변수로 덮어쓸 수 있다.
# ---------------------------------------------------------------------------
OWNER_EMAIL = os.environ.get('LICENSE_OWNER_EMAIL', 'ohjn96@naver.com')

# 라이선스 인프라(요청 이슈·발급된 키·정책·철회 목록)는 공개 배포 저장소에 있다.
# 이 저장소(앱 소스)는 비공개라 앱이 접근할 수 없다.
DIST_REPO = os.environ.get('LICENSE_DIST_REPO', 'ohjn96/train-reservation')
DIST_BRANCH = os.environ.get('LICENSE_DIST_BRANCH', 'release')
DIST_RAW = f'https://raw.githubusercontent.com/{DIST_REPO}/{DIST_BRANCH}'

OWNER_ISSUE_URL = f'https://github.com/{DIST_REPO}/issues'

# ---------------------------------------------------------------------------
# 자동 발급 경로
#   사용자는 Issue 폼으로 요청하고, 발급자가 "/approve" 댓글을 달면 GitHub Actions 가
#   서명된 키를 licenses/<머신ID>.key 로 커밋한다. 앱은 그 파일을 스스로 받아 활성화한다.
#   발급된 키는 머신에 묶여 있어서 공개돼도 남이 쓸 수 없다.
# ---------------------------------------------------------------------------
REQUEST_FORM_URL = os.environ.get(
    'LICENSE_REQUEST_URL',
    f'https://github.com/{DIST_REPO}/issues/new'
    '?template=license-request.yml&title=%5B%EB%9D%BC%EC%9D%B4%EC%84%A0%EC%8A%A4%5D+{machine_id}'
    '&machine-id={machine_id}',
)
DELIVERY_URL = os.environ.get(
    'LICENSE_DELIVERY_URL',
    DIST_RAW + '/licenses/{machine_id}.key',
)
DELIVERY_TIMEOUT = 5.0

# ---------------------------------------------------------------------------
# 정책 파일 — 라이선스 검사를 켜고 끄는 원격 스위치
#   파일이 없으면 'open'(검사 안 함)이다. 켜려면:
#       python scripts/license_admin.py policy --mode licensed
#   raw.githubusercontent.com 은 CDN 캐시가 약 5분이라 반영에 그만큼 걸린다.
# ---------------------------------------------------------------------------
POLICY_URL = os.environ.get('LICENSE_POLICY_URL', f'{DIST_RAW}/license-policy.json')
POLICY_TIMEOUT = 3.0
POLICY_TTL = 6 * 3600     # 초. 이 주기로 스위치를 다시 확인한다

# ---------------------------------------------------------------------------
# 철회 목록 (revocation list)
#   서명된 정적 JSON. 저장소에 커밋해두면 raw URL 로 그냥 받아진다.
#   네트워크가 안 되면 조용히 넘어간다(fail-open) — 오프라인 사용을 막지 않기 위해서.
# ---------------------------------------------------------------------------
REVOCATION_URL = os.environ.get('LICENSE_REVOCATION_URL', f'{DIST_RAW}/revoked.json')
REVOCATION_TIMEOUT = 3.0      # 초. 앱 기동을 붙잡지 않도록 짧게
REVOCATION_TTL = 24 * 3600    # 초. 이 기간 동안은 캐시된 목록을 재사용

# ---------------------------------------------------------------------------
# 시계 되돌리기 허용 오차
#   NTP 보정·타임존 변경 정도는 봐주고, 그 이상 과거로 가면 거부한다.
# ---------------------------------------------------------------------------
CLOCK_ROLLBACK_TOLERANCE = 24 * 3600   # 초

# 만료 임박 경고를 띄우기 시작하는 시점
EXPIRY_WARNING_DAYS = 3


def app_data_dir() -> Path:
    """라이선스와 상태 파일을 두는 사용자별 디렉터리."""
    override = os.environ.get('TRAIN_LICENSE_DIR')
    if override:
        return Path(override).expanduser()

    if sys.platform == 'win32':
        base = os.environ.get('APPDATA') or (Path.home() / 'AppData' / 'Roaming')
        return Path(base) / 'TrainReservation'
    if sys.platform == 'darwin':
        return Path.home() / 'Library' / 'Application Support' / 'TrainReservation'
    base = os.environ.get('XDG_CONFIG_HOME') or (Path.home() / '.config')
    return Path(base) / 'train-reservation'


def shadow_state_path() -> Path:
    """시계 되돌리기 감지용 보조 기록 (주 상태 파일을 지워도 남도록 다른 위치에)."""
    override = os.environ.get('TRAIN_LICENSE_SHADOW')
    if override:
        return Path(override).expanduser()
    return Path.home() / '.train_reservation_state'
