#!/usr/bin/env bash
# 데스크톱판 스모크: 소스 실행 두 가지 + 헤드리스 도움말 + PyInstaller 단일 실행 파일
#
#   scripts/qa/desktop_smoke.sh [--no-exe]
#
# 서버판(python -m server)은 돌리지 않는다. 코레일에도 붙지 않는다 (/login 화면만 받는다).
# HOME 은 임시 폴더로 바꿔 실제 설정 파일을 건드리지 않는다.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
PY="${QA_PY:-$ROOT/../private_train_mobile/venv/bin/python}"
OUT="${QA_OUT:-${TMPDIR:-/tmp}/pt-qa-out}"
EXE=1
[[ "${1:-}" == "--no-exe" ]] && EXE=0
mkdir -p "$OUT"
TMPHOME="$(mktemp -d)"
FAIL=0
PIDS=()
cleanup() {
  for p in "${PIDS[@]:-}"; do [[ -n "$p" ]] && kill "$p" 2>/dev/null; done
  rm -rf "$TMPHOME"
}
trap cleanup EXIT

pass() { echo "PASS  $*"; }
fail() { echo "FAIL  $*"; FAIL=1; }

free_port() { "$PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])'; }

# $1 이름, $2.. 명령. 서버가 /login 200 을 주는지 (최대 40초)
check_server() {
  local name="$1"; shift
  local port; port="$(free_port)"
  local log="$OUT/desktop_${name}.log"
  (cd "$ROOT" && HOME="$TMPHOME" FLASK_DEBUG=false NO_BROWSER=1 HOST=127.0.0.1 PORT="$port" \
     FLASK_SECRET_KEY=qa-smoke "$@" > "$log" 2>&1) &
  local pid=$!
  PIDS+=("$pid")
  local code=000
  for _ in $(seq 1 80); do
    code="$(curl -s -o /dev/null -w '%{http_code}' "http://127.0.0.1:$port/login" || true)"
    [[ "$code" == 200 ]] && break
    kill -0 "$pid" 2>/dev/null || break
    sleep 0.5
  done
  if [[ "$code" == 200 ]]; then pass "$name: /login 200 (port $port)"; else fail "$name: /login $code (로그: $log)"; tail -5 "$log"; fi
  # 0.0.0.0 으로 새지 않는지 (HOST=127.0.0.1 을 지키는지)
  if ss -ltn 2>/dev/null | grep -q "0.0.0.0:$port "; then fail "$name: HOST=127.0.0.1 인데 0.0.0.0 에 떴음"; fi
  # 서버 프로세스와 자식(werkzeug) 정리
  pkill -P "$pid" 2>/dev/null
  kill "$pid" 2>/dev/null; wait "$pid" 2>/dev/null
  local lp; lp="$(ss -ltnp 2>/dev/null | grep ":$port " | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1)"
  [[ -n "$lp" ]] && kill "$lp" 2>/dev/null
}

echo "==> 데스크톱 스모크 ($("$PY" --version))"
check_server "python_-m_desktop" "$PY" -m desktop
check_server "python_main.py" "$PY" main.py

out="$(cd "$ROOT" && HOME="$TMPHOME" timeout 60 "$PY" -m desktop --headless --help 2>&1)"; rc=$?
if [[ $rc -eq 0 && "$out" == *"usage"* ]]; then pass "python -m desktop --headless --help"; else fail "--headless --help (rc=$rc)"; echo "$out" | tail -5; fi
out="$(cd "$ROOT" && HOME="$TMPHOME" timeout 60 "$PY" main.py --headless --help 2>&1)"; rc=$?
if [[ $rc -eq 0 && "$out" == *"usage"* ]]; then pass "python main.py --headless --help"; else fail "main.py --headless --help (rc=$rc)"; fi

if [[ $EXE -eq 1 ]]; then
  VERSION="$(tr -d '[:space:]' < "$ROOT/VERSION")"
  BIN="$ROOT/TrainReservationApp-v$VERSION"
  echo "==> PyInstaller 빌드 (desktop/build/build.py unified, 몇 분 걸림)"
  if (cd "$ROOT" && "$PY" desktop/build/build.py unified > "$OUT/desktop_pyinstaller.log" 2>&1) && [[ -x "$BIN" ]]; then
    pass "PyInstaller 빌드 ($(du -h "$BIN" | cut -f1))"
    check_server "binary" "$BIN"
    out="$(HOME="$TMPHOME" timeout 60 "$BIN" --headless --help 2>&1)"; rc=$?
    if [[ $rc -eq 0 && "$out" == *"usage"* ]]; then pass "binary --headless --help"; else fail "binary --headless --help (rc=$rc)"; fi
    out="$(HOME="$TMPHOME" timeout 60 "$BIN" --headless --help 2>&1 | cat)"
    [[ "$out" == *"usage"* ]] && pass "binary 출력을 파이프로 보내도 한글 출력 OK" || fail "binary 파이프 출력"
  else
    fail "PyInstaller 빌드 (로그: $OUT/desktop_pyinstaller.log)"; tail -15 "$OUT/desktop_pyinstaller.log"
  fi
  rm -f "$BIN"
  rm -rf "$ROOT/desktop/build/.pyi"
fi

[[ $FAIL -eq 0 ]] && echo "==> 데스크톱 스모크 통과" || echo "==> 데스크톱 스모크 실패"
exit $FAIL
