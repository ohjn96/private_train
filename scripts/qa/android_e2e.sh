#!/usr/bin/env bash
# 안드로이드 앱 E2E: 디버그 APK 빌드 → 에뮬레이터 설치 → 시나리오 PASS/FAIL
#
#   scripts/qa/android_e2e.sh [--no-build] [--reboot] [--stall] [--keep-emulator]
#
# 환경변수
#   QA_TOOLS   안드로이드 도구 폴더 (기본 ~/.local/share/private_train-android)
#   QA_PY      playwright 가 깔린 파이썬 (기본 ../private_train_mobile/venv/bin/python)
#   QA_OUT     스크린샷·logcat 저장 폴더
#   QA_SERIAL  에뮬레이터 (기본 emulator-5560, 포트 5560). 안 떠 있으면 AVD test34 를 헤드리스로 띄우고
#              끝나면 끈다 (--keep-emulator 면 둔다).
# 코레일에는 붙지 않는다 (디버그 빌드의 /__debug/fake_job 가짜 매크로만).
set -uo pipefail
ROOT="$(cd "$(dirname "$0")/../.." && pwd)"
T="${QA_TOOLS:-$HOME/.local/share/private_train-android}"
PY="${QA_PY:-$ROOT/../private_train_mobile/venv/bin/python}"
SERIAL="${QA_SERIAL:-emulator-5560}"
OUT="${QA_OUT:-${TMPDIR:-/tmp}/pt-qa-out}"
export JAVA_HOME="$T/jdk17" ANDROID_HOME="$T/sdk" ANDROID_AVD_HOME="$T/avd" GRADLE_USER_HOME="$T/gradle-home"
export CHAQUOPY_BUILD_PYTHON="$T/python/cpython-3.12.14-linux-x86_64-gnu/bin/python3.12"
export ADB="$T/sdk/platform-tools/adb"
BUILD=1 KEEP=0 EXTRA=()
for a in "$@"; do
  case "$a" in
    --no-build) BUILD=0 ;;
    --keep-emulator) KEEP=1 ;;
    --reboot|--stall) EXTRA+=("$a") ;;
    *) echo "알 수 없는 옵션: $a" >&2; exit 2 ;;
  esac
done
mkdir -p "$OUT"
APK="$ROOT/mobile/android/app/build/outputs/apk/debug/app-debug.apk"

if [[ $BUILD -eq 1 ]]; then
  echo "==> 디버그 APK 빌드"
  echo "sdk.dir=$ANDROID_HOME" > "$ROOT/mobile/android/local.properties"
  if ! (cd "$ROOT/mobile/android" && ./gradlew assembleDebug --console=plain -q > "$OUT/gradle.log" 2>&1); then
    echo "FAIL  APK 빌드 (로그: $OUT/gradle.log)"; tail -20 "$OUT/gradle.log"; exit 1
  fi
  echo "PASS  APK 빌드 ($(du -h "$APK" | cut -f1))"
fi

STARTED=0
if ! "$ADB" devices | grep -q "^$SERIAL\s*device"; then
  PORT="${SERIAL#emulator-}"
  echo "==> 에뮬레이터 시작 (test34, 포트 $PORT, 헤드리스)"
  nohup "$T/sdk/emulator/emulator" -avd test34 -port "$PORT" -no-window -gpu swiftshader_indirect \
    -no-audio -no-boot-anim -no-snapshot-save > "$OUT/emulator.log" 2>&1 &
  STARTED=1
  "$ADB" -s "$SERIAL" wait-for-device
  for _ in $(seq 1 120); do
    [[ "$("$ADB" -s "$SERIAL" shell getprop sys.boot_completed 2>/dev/null | tr -d '\r')" == 1 ]] && break
    sleep 2
  done
fi

"$PY" "$ROOT/scripts/qa/android_e2e.py" --apk "$APK" --serial "$SERIAL" --out "$OUT" "${EXTRA[@]}"
RC=$?

if [[ $STARTED -eq 1 && $KEEP -eq 0 ]]; then
  echo "==> 에뮬레이터 끄기"
  "$ADB" -s "$SERIAL" emu kill >/dev/null 2>&1 || true
fi
exit $RC
