# scripts/qa — 검증 도구

앱 코드는 건드리지 않고 돌리는 QA 스크립트 모음. **코레일에는 붙지 않는다**
(데모 서버는 가짜 로그인·조회, 안드로이드는 디버그 빌드의 `/__debug/fake_job`, 단위 테스트는 외부 네트워크 차단).
서버판(`python -m server`)은 돌리지 않는다.

| 파일 | 하는 일 |
|---|---|
| `run_all.sh` | 아래 전부 (안드로이드는 긴 선택 시나리오 빼고) + 요약표 |
| `demo_server.py` | 가짜 코레일 데스크톱 웹 서버 (화면 캡처용) |
| `screenshots.py` | 폰 390x844@2x · 데스크톱 1280x900 캡처 + 가로 넘침·JS 오류 점검 |
| `desktop_smoke.sh` | `python -m desktop`, `python main.py`, `--headless --help`, PyInstaller 단일 파일 |
| `android_e2e.sh` / `.py` | 디버그 APK 빌드 → 에뮬레이터 설치 → 복구·보안 시나리오 |
| `nonet/sitecustomize.py` | `PYTHONPATH` 에 넣으면 127.x 외 접속을 막고 `[QA-NONET]` 으로 기록 |

## 준비

- 파이썬: `../private_train_mobile/venv/bin/python` (Flask 앱 의존성 + Playwright). 다르면 `QA_PY=`.
- 크롬: `/usr/bin/google-chrome` (다르면 `QA_CHROME=`).
- 안드로이드 도구: `~/.local/share/private_train-android` (jdk17, sdk + AVD `test34`, gradle-home,
  python 3.12). 다르면 `QA_TOOLS=`. 에뮬레이터는 `emulator-5560` (포트 5560), 안 떠 있으면
  헤드리스로 띄우고 끝나면 끈다. `adb root` 가 되는 이미지여야 한다.
- 결과는 `QA_OUT` (기본 `$TMPDIR/pt-qa-out`).

## 쓰는 법

```bash
scripts/qa/run_all.sh                       # 전체 (약 10분, PyInstaller 포함)
scripts/qa/run_all.sh --no-android --no-exe # 빠르게

# 데모 서버만 띄워 눈으로 보기 (HOME 은 꼭 임시 폴더로)
HOME=$(mktemp -d) python scripts/qa/demo_server.py desktop 5081
#   /demo  /demo_run  /demo_last/<success_paid|success_payfail|success_nopay|stopped|gave_up|crash>
#   /demo_success/<pay_running|pay_done|pay_failed|pay_pending> (도는 중 화면에서)  /demo_reset
python scripts/qa/screenshots.py --base http://127.0.0.1:5081 --out /tmp/shots

scripts/qa/android_e2e.sh                    # 빌드 + 기본 시나리오
scripts/qa/android_e2e.sh --no-build --stall # 워치독 재시작 (약 5분 더)
scripts/qa/android_e2e.sh --no-build --reboot
```

## 점검 항목

**screenshots.py** — 로그인, 검색+결과(2개 선택), 도는 중, 마지막 결과 카드 6가지
(자동결제 완료 / 자동결제 실패 / 자동결제 안 함 / 중단 / 로그인 포기 / 오류), 성공 배너 4가지
(자동결제 중 / 완료 / 실패 / 결제 남음). 실패 조건: `scrollWidth > 화면 폭`, 콘솔 JS 오류
(외부 글꼴 로드 실패는 경고). `report.json` 에 참고 정보: 44px 미만 터치 대상(폰), 이모지,
두 줄로 쪼개진 짧은 낱말(예: "미연/결"),
팔레트(#F6F4F0 / #1A1714 / #C8102E 계열) 밖 색.

**android_e2e.py** — 앱 실행·서버 기동, WebView 화면, 토큰 없는 요청 403, 틀린 토큰 403,
`/__auth?t=` 가 `app_token` 쿠키(HttpOnly) 심기, 가짜 매크로 시작, `shared_prefs/job.xml` 에
평문 없음, `kill -9` → 새 pid + 매크로 재개 + "다시 시작했어요" 알림, `STOP_MACRO` 서비스 인텐트
→ 매크로 멈춤 + 작업 삭제, WebView 렌더러 kill → 앱 생존 + 다시 로드, 비행기 모드 실행 →
Tailwind 스타일 유지. 선택: `--reboot` 재부팅 후 재개, `--stall` 조회 멈춤 → 워치독 재시작.
WebView 안의 값은 CDP(디버그 빌드 원격 디버깅)로 읽는다. 토큰은
`adb shell run-as com.ohjn96.trainreservation cat shared_prefs/app.xml`.

## 주의

- 프로세스는 PID 로만 끈다 (`pkill -f` 금지: 자기 셸까지 죽일 수 있다).
- 단위 테스트는 네트워크 차단 상태로 돈다. `[QA-NONET]` 이 찍히면 그 테스트가 실제 외부 접속을
  시도한 것이니 결함으로 보고한다.
