#!/usr/bin/env bash
# 한 방 실행 스크립트 (Linux / macOS)
#   ./run.sh              가상환경 준비 + 앱 실행
#   ./run.sh --reinstall  의존성 다시 설치 후 실행
#   PORT=8080 ./run.sh    다른 포트로 실행
set -euo pipefail

cd "$(dirname "$0")/.."

VENV="venv"
PY="$VENV/bin/python"
STAMP="$VENV/.deps-installed"
REINSTALL=0
[[ "${1:-}" == "--reinstall" ]] && REINSTALL=1

echo "========================================"
echo " Train Reservation App - Quick Start"
echo "========================================"

# 1) 가상환경 준비
if [[ ! -x "$PY" ]]; then
    echo "==> 가상환경이 없어 새로 만듭니다 ($VENV)"
    BASE_PY="$(command -v python3.12 || command -v python3 || command -v python || true)"
    if [[ -z "$BASE_PY" ]]; then
        echo "!! Python 을 찾을 수 없습니다. Python 3.12+ 설치 후 다시 실행하세요." >&2
        exit 1
    fi
    "$BASE_PY" -m venv "$VENV"
    REINSTALL=1
fi
echo "==> $("$PY" --version) ($PY)"

# 2) 의존성 설치 (최초 1회 또는 requirements.txt 내용 변경 시)
#    수정 시각이 아니라 내용 해시로 비교한다. git checkout 만 해도 시각은 바뀌기 때문.
REQ_HASH="$(cksum < requirements.txt)"
if [[ $REINSTALL -eq 1 || ! -f "$STAMP" || "$(cat "$STAMP")" != "$REQ_HASH" ]]; then
    echo "==> 의존성 설치 중... (requirements.txt)"
    "$PY" -m pip install --upgrade pip -q
    "$PY" -m pip install -r requirements.txt
    echo "$REQ_HASH" > "$STAMP"
    echo "==> 의존성 설치 완료"
fi

# 3) 캐시 정리 (최신 코드 보장)
find desktop webui core server korail2 -name '__pycache__' -type d -prune -exec rm -rf {} + 2>/dev/null || true

# 4) 실행
echo ""
echo "🚄 http://localhost:${PORT:-5050} 에서 접속하세요 (종료: Ctrl+C)"
echo ""
exec "$PY" main.py
