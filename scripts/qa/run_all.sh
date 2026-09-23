#!/usr/bin/env bash
# 전체 회귀: 단위 테스트 + 데스크톱 스모크 + 화면 캡처 + 안드로이드 E2E → 요약표
#
#   scripts/qa/run_all.sh [--no-android] [--no-exe]
#
#   QA_OUT  결과 폴더 (기본 $TMPDIR/pt-qa-out)
#   QA_PY   파이썬 (기본 ../private_train_mobile/venv/bin/python)
# 긴 선택 시나리오(--reboot, --stall)는 android_e2e.sh 로 따로 돌린다.
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
QA="$ROOT/scripts/qa"
export QA_PY="${QA_PY:-$ROOT/../private_train_mobile/venv/bin/python}"
export QA_OUT="${QA_OUT:-${TMPDIR:-/tmp}/pt-qa-out}"
ANDROID=1 EXE_FLAG=""
for a in "$@"; do
  case "$a" in
    --no-android) ANDROID=0 ;;
    --no-exe) EXE_FLAG="--no-exe" ;;
  esac
done
mkdir -p "$QA_OUT"
declare -a NAMES RESULTS NOTES

step() {  # step 이름 로그파일 명령...
  local name="$1" log="$2"; shift 2
  echo "==> $name"
  local start=$SECONDS
  "$@" > "$log" 2>&1
  local rc=$?
  local passed failed
  passed=$(grep -c '^PASS' "$log" || true)
  failed=$(grep -c '^FAIL' "$log" || true)
  NAMES+=("$name")
  RESULTS+=("$([[ $rc -eq 0 ]] && echo PASS || echo FAIL)")
  NOTES+=("${passed} pass / ${failed} fail, $((SECONDS - start))s, $log")
  grep -E '^(PASS|FAIL)' "$log" | sed 's/^/    /'
}

# 1) 단위 테스트: 외부 네트워크를 막고 돌린다 (막힌 시도는 [QA-NONET] 으로 기록)
UNIT_HOME="$(mktemp -d)"
echo "==> 단위 테스트"
start=$SECONDS
(cd "$ROOT" && PYTHONPATH="$QA/nonet" HOME="$UNIT_HOME" "$QA_PY" -m unittest discover -s tests -v) > "$QA_OUT/unit.log" 2>&1
rc=$?
rm -rf "$UNIT_HOME"
summary="$(grep -E '^Ran [0-9]+ tests' "$QA_OUT/unit.log") $(grep -E '^(OK|FAILED)' "$QA_OUT/unit.log" | tail -1)"
blocked=$(grep -c '^\[QA-NONET\]' "$QA_OUT/unit.log" || true)
NAMES+=("단위 테스트"); RESULTS+=("$([[ $rc -eq 0 ]] && echo PASS || echo FAIL)")
NOTES+=("$summary, 외부 접속 시도 차단 ${blocked}건, $((SECONDS - start))s")
echo "    $summary (외부 접속 시도 차단 ${blocked}건)"
[[ $blocked -gt 0 ]] && grep -A0 -B1 '^\[QA-NONET\]' "$QA_OUT/unit.log" | grep -E '^test_|QA-NONET' | sed 's/^/    ! /'

# 2) 데스크톱 스모크
step "데스크톱 스모크" "$QA_OUT/desktop.log" "$QA/desktop_smoke.sh" $EXE_FLAG

# 3) 화면 캡처 (데모 서버)
DEMO_HOME="$(mktemp -d)"
PORT="$("$QA_PY" -c 'import socket; s=socket.socket(); s.bind(("127.0.0.1",0)); print(s.getsockname()[1])')"
(cd "$ROOT" && PYTHONPATH="$QA/nonet" HOME="$DEMO_HOME" "$QA_PY" "$QA/demo_server.py" desktop "$PORT" > "$QA_OUT/demo_server.log" 2>&1) &
DEMO_PID=$!
for _ in $(seq 1 40); do curl -s -o /dev/null "http://127.0.0.1:$PORT/login" && break; sleep 0.5; done
step "화면 캡처·점검" "$QA_OUT/screenshots.log" "$QA_PY" "$QA/screenshots.py" --base "http://127.0.0.1:$PORT" --out "$QA_OUT/web"
LP="$(ss -ltnp 2>/dev/null | grep ":$PORT " | grep -o 'pid=[0-9]*' | cut -d= -f2 | head -1)"
kill "$DEMO_PID" ${LP:+"$LP"} 2>/dev/null
rm -rf "$DEMO_HOME"

# 4) 안드로이드 E2E
if [[ $ANDROID -eq 1 ]]; then
  step "안드로이드 E2E" "$QA_OUT/android.log" "$QA/android_e2e.sh"
fi

echo
echo "──────── 요약"
FAIL=0
for i in "${!NAMES[@]}"; do
  echo "  ${RESULTS[$i]}  ${NAMES[$i]}  —  ${NOTES[$i]}"
  [[ "${RESULTS[$i]}" == FAIL ]] && FAIL=1
done
echo "결과 폴더: $QA_OUT"
exit $FAIL
