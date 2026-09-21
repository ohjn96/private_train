# KTX/SRT 열차 예약 시스템

> **주의: 개인 사용 전용. 재배포·상업적 이용 금지**
>
> 이 프로젝트는 개인 학습·개인 사용 목적으로만 쓸 수 있습니다.
> 소스/실행 파일의 재배포와 영리 목적 이용은 저작권자의 사전 서면 허가가 필요합니다.
> 자세한 조건은 [LICENSE](LICENSE) 를 보세요.

## 개요

코레일 계정 하나로 KTX와 SRT 열차를 함께 조회/예약하는 웹 애플리케이션입니다.
코레일 API가 SRT 노선(수서·동탄·평택지제 등)까지 함께 조회해 주므로 SRT 전용 로그인은 필요하지 않습니다.

### 주요 기능

- KTX + SRT 통합 조회/예약 (코레일 계정 하나)
- 실시간 좌석 검색
- 자동 예약 시도 (매크로)
- 예약 성공 시 알림음
- **텔레그램 봇 연동** (알림 + 원격 제어, 재시작해도 연결 유지)
- 반응형 UI (Tailwind CSS)
- 크로스 플랫폼 지원 (Windows, macOS, Linux)

---

## 빠른 시작

Python 3.12+ 만 설치돼 있으면 됩니다. **명령어 하나로 가상환경 생성 → 의존성 설치 → 실행까지 끝납니다.**

### Windows

```powershell
.\scripts\run.ps1
```
> 더블클릭으로 실행하려면 `scripts\run.bat` 을 두 번 클릭하세요.
> PowerShell 실행 정책 오류가 나면: `powershell -ExecutionPolicy Bypass -File scripts\run.ps1`

### Linux / macOS

```bash
./scripts/run.sh
```

실행 후 브라우저에서 **http://localhost:5050** 접속.

| 하고 싶은 것 | 명령 (Windows) | 명령 (Linux/macOS) |
|---|---|---|
| 그냥 실행 | `.\scripts\run.ps1` | `./scripts/run.sh` |
| 의존성 다시 설치 | `.\scripts\run.ps1 -Reinstall` | `./scripts/run.sh --reinstall` |
| 다른 포트로 실행 | `.\scripts\run.ps1 -Port 8080` | `PORT=8080 ./scripts/run.sh` |

스크립트가 알아서 하는 일: 가상환경(`venv/`) 없으면 생성 → `requirements` 변경 시에만 재설치 →
Python 캐시 삭제 → `venv` 의 python 으로 실행. **`python` / `python3` 헷갈릴 일 없습니다.**

### 실행 파일 (설치 없이)

Windows 사용자는 [Releases](https://github.com/ohjn96/private_train/releases) 에서
`TrainReservationApp-v<버전>.exe` 를 받아 더블클릭하면 끝입니다. Python 설치도 필요 없고,
**브라우저가 자동으로 열립니다.** (검은 콘솔 창은 서버라 켜둬야 하고, 종료는 그 창에서 Ctrl+C)

> 브라우저 자동 실행을 끄려면 `NO_BROWSER=1`, 개발 중(스크립트 실행)에 켜려면 `OPEN_BROWSER=1`.

---

## 요구사항

- **Python**: 3.12+ (실행 파일만 쓸 거면 불필요)
- **Node.js**: 18+ (Tailwind CSS 재빌드용, 선택사항)

---

## 테스트

코레일 서버에 붙지 않는 회귀 테스트입니다. 가짜 클라이언트를 꽂아 예약 플로우의
호출 순서·횟수·간격만 검사하므로 계정 없이 언제든 돌릴 수 있습니다.

```bash
python -m unittest discover -s tests -v
```

검사하는 것:

- 조회 페이징 (선택한 열차가 전부 조회 범위에 들어오는지)
- API 호출 간격 1.5초 유지, 단 좌석을 찾은 직후의 예약만 즉시 실행
- 여러 열차를 선택해도 시도당 조회는 한 번, 좌석 있는 열차만 예약
- 2인 동시 예약 / 2인 순차 예약 (순차 예약은 첫 좌석을 잡은 열차로 고정)
- 매크로 이중 실행 차단, 예외로 죽어도 실행 슬롯 반납
- 같은 열차 중복 선택 제거

---

## 트러블슈팅

### "ModuleNotFoundError: No module named 'flask'"

**원인**: `python3` 명령어가 시스템 Python을 가리킵니다.

**해결**:
```powershell
# python3 대신 python 사용
python main.py

# 또는 실행 스크립트 사용 (venv 의 python 을 직접 호출)
.\scripts\run.ps1
```

### 코드 변경사항이 반영되지 않음

**원인**: Python 캐시 파일(.pyc)이 이전 코드를 캐시

**해결**:
```powershell
# 캐시 삭제
Get-ChildItem -Recurse -Include *.pyc,__pycache__ | Remove-Item -Recurse -Force

# 또는 실행 스크립트 사용 (자동 캐시 삭제)
.\scripts\run.ps1
```

---

## 텔레그램 봇 연동

예약 성공 시 텔레그램으로 푸시 알림을 받고, 텔레그램에서 직접 원격으로 예약을 제어할 수 있습니다.

### 1단계: 봇 생성

1. 텔레그램에서 **@BotFather** 검색
2. `/newbot` 전송 → 봇 이름과 username 설정
3. 발급된 **Bot Token** 복사 (예: `123456:ABC-DEF1234ghIkl-zyx57W2v1u123ew11`)

### 2단계: 웹에서 연결

1. 검색 페이지 하단의 **텔레그램 알림** 카드 클릭
2. Bot Token 입력 후 **"연결"** 클릭
3. 생성한 봇의 대화창에서 `/start` 전송 → **Chat ID 자동 등록**
4. **"자동 연결"** 체크 시 다음 접속부터 자동 연결

> 💡 Bot Token은 브라우저 localStorage에 저장되어 새로고침 후에도 유지됩니다.

### Chat ID란?

**Chat ID**는 텔레그램이 각 대화(채팅)에 부여하는 **고유 숫자 식별자**입니다.  
봇이 메시지를 보내려면 "어디로 보낼지" 알아야 하는데, 그 목적지가 Chat ID입니다.

| 질문 | 답변 |
|------|------|
| 직접 입력해야 하나요? | **아니요.** 봇에게 `/start`만 보내면 자동 등록됩니다 |
| 어디서 확인하나요? | 봇에게 `/chatid` 명령어를 보내면 표시됩니다 |
| 왜 필요한가요? | 봇이 예약 성공 알림을 보낼 대상을 지정하기 위함 |
| 예시 | `123456789` (숫자) |

```
사용자 → 봇: /start
봇 → 사용자: ✅ 연결 완료! Chat ID가 자동 등록되었습니다.

이후 예약 성공 시 → 봇이 이 Chat ID로 알림 전송
```

### 3단계: 사용

#### 알림 기능 (자동)
- 예약 매크로 시작/중단 시 텔레그램 알림
- **예약 성공 시** 열차명, 시간, 구간, 예약번호 푸시 알림

#### 봇 명령어 (원격 제어)

| 명령어 | 설명 | 예시 |
|--------|------|------|
| `/start` | 봇 연결 및 Chat ID 자동 등록 | `/start` |
| `/reserve` | 열차 검색 후 예약 시작 | `/reserve 수서 부산 2026-03-01 06:00` |
| `/trains` | 마지막 검색된 열차 목록 확인 | `/trains` |
| `/stop` | 실행 중인 매크로 원격 중단 | `/stop` |
| `/restart` | 최신 데이터로 매크로 재시작 | `/restart` |
| `/status` | 현재 매크로 상태 상세 확인 | `/status` |
| `/chatid` | Chat ID 확인 | `/chatid` |
| `/help` | 명령어 도움말 | `/help` |

#### `/reserve` 사용 흐름

```
사용자: /reserve 수서 부산 2026-03-01 06:00
  봇: 🔍 열차 검색 중...
  봇: 🚄 검색 결과 (10건)
      1. KTX 101 06:00→08:40 🔴일반 🔴특실
      2. KTX-산천 303 06:30→09:10 🟢일반 🔴특실
      3. KTX 105 07:00→09:40 🔴일반 🔴특실
      📌 예약할 열차 번호를 입력하세요

사용자: 1,2,3
  봇: ▶️ 예약 매크로를 시작합니다!
      중단하려면 /stop 입력

  ... (자동 반복 시도) ...

  봇: 🎉 예약 성공!
      🚄 KTX-산천 303 / 🕐 06:30
      📍 수서 → 부산
```

---

## 사용 흐름

```
이 앱에서 예약 → 공식 앱에서 확인/결제
```

### 1. 이 앱에서 할 수 있는 것
- 로그인 (코레일 계정)
- 열차 검색
- 자동 예약 시도 (매크로)

### 2. 예약 성공 후
이 앱은 **예약만** 진행합니다. 예약 확인 및 결제는 **공식 앱**에서 해야 합니다.

| 서비스 | 확인 방법 |
|--------|----------|
| **KTX / SRT** | 코레일톡 앱 → 마이 → 예약내역 |

### 3. 주의사항
- 예약 후 **결제 기한**(20분~1시간) 내 결제 필수
- 기한 내 결제 안 하면 **자동 취소**

---

## 프로젝트 구조

```
private_train/
├── app/                        # Flask 앱
│   ├── routes/                 # 라우트 (auth, search, reservation, telegram, license)
│   ├── services/               # 서비스 레이어 (Korail, Telegram)
│   ├── licensing/              # 라이선스 게이트 (머신 바인딩 + 서명 검증)
│   ├── templates/              # Jinja2 템플릿
│   └── static/                 # 정적 파일
├── korail2/                    # Korail API 모듈
├── tests/                      # 회귀 테스트 (네트워크 불필요)
├── scripts/                    # 실행/릴리스 스크립트 (run.sh, run.ps1, run.bat, release.sh)
│   ├── license_admin.py        # 라이선스 발급/철회 (발급자 전용)
│   ├── ci_approve.py           # 승인 댓글 해석 (Actions 용)
│   ├── ci_expiry.py            # 만료 임박 스캔 (Actions 용)
│   └── ci_renew.py             # 자동 갱신 대상 스캔 (Actions 용)
├── licenses/                   # 승인된 라이선스 (앱이 여기서 자동 수령)
├── license-policy.json         # 라이선스 검사 ON/OFF (없으면 OFF)
├── docs/                       # 라이선스 운영 가이드 + 현황 대시보드(GitHub Pages)
├── build/                      # 빌드 스크립트 (build.py, build.ps1, build.bat)
├── .github/workflows/          # exe 자동 빌드 + 라이선스 승인 자동화
├── main.py                     # 진입점
├── VERSION                     # 버전 단일 출처
├── LICENSE                     # 개인 사용 라이선스 (재배포·상업이용 금지)
├── THIRD-PARTY-NOTICES.md      # 번들 오픈소스 고지
└── requirements.txt            # 의존성
```

---

## 빌드 / 릴리스

버전은 루트의 **`VERSION`** 파일 하나가 단일 출처이고, 결과물 이름에 그대로 박힙니다
(`TrainReservationApp-v2.3.0.exe`). 빌드된 실행 파일은 자동으로 프로젝트 루트로 옮겨지고,
같은 플랫폼의 이전 버전 파일은 삭제됩니다. 중간 산출물(spec, work 폴더)은 `build/.pyi/` 에만 생겼다가 지워집니다.

### 1. GitHub Actions 로 릴리스 (권장)

Linux/macOS 에서는 Windows exe 를 만들 수 없으므로, Windows 러너에서 빌드합니다.

```bash
./scripts/release.sh 2.3.1
```

VERSION·package.json 을 올리고 커밋 → `v2.3.1` 태그 푸시 → GitHub Actions
(`.github/workflows/release.yml`)가 Windows 러너에서 exe 를 빌드해
**Release asset 으로 자동 첨부**합니다. 태그 없이 Actions 탭에서 수동 실행(Run workflow)하면
Release 없이 아티팩트로만 받을 수 있습니다.

> 💡 GitHub Actions 는 무료 플랜에서도 씁니다. 퍼블릭 저장소는 무제한, 프라이빗 저장소는
> 월 2,000분 무료이며 **Windows 러너는 분당 2배**로 차감됩니다(≈ 월 1,000분).
> 이 빌드는 한 번에 3~5분 정도라 넉넉합니다.

### 2. Windows PC 에서 직접 빌드

```powershell
.\build\build.ps1          # venv 준비 + 의존성 설치 + 빌드
.\build\build.ps1 -SkipInstall   # 이미 설치돼 있으면
```
더블클릭으로 하려면 `build\build.bat`.

### 3. 파이썬으로 직접

```bash
python build/build.py unified
```
PyInstaller 옵션은 `build/build.py` 한 곳에만 있고, `build.ps1` 은 이를 호출만 합니다.

---

## 라이선스 인증

**현재는 꺼져 있습니다.** 별도 인증 없이 그냥 쓰시면 됩니다.

인증 기능 자체는 구현돼 있고, 배포자가 원격 스위치로 켤 수 있습니다. 켜지면 이렇게 됩니다:

```
1. 앱 실행 → [라이선스 요청하기] 클릭 (머신 ID 가 자동으로 채워진 요청 폼이 열립니다)
2. 발급자가 승인
3. 라이선스 화면을 켜둔 채 기다리면 자동으로 활성화됩니다 (최대 5분)
```

복붙할 게 없습니다. 이메일로 키를 직접 받았다면 화면 아래쪽에 붙여넣는 칸도 있습니다.

- 라이선스는 **그 PC 에서만** 유효합니다. 다른 PC 로 옮기거나 남에게 넘겨줄 수 없습니다.
- 정해진 기간이 지나면 만료되며, 연장은 발급자에게 다시 요청하면 됩니다.
- OS 를 재설치하면 머신 ID 가 바뀌므로 재발급이 필요합니다.
- 활성화된 뒤에는 인터넷 없이도 동작합니다.

> 발급자라면 스위치·키 생성·발급·철회·자동 승인 설정은
> [docs/LICENSING.md](docs/LICENSING.md) 를 보세요.
> 검사를 켜려면 `python scripts/license_admin.py policy --mode licensed`,
> 승인은 GitHub 알림 메일에 `/approve 30` 이라고 답장하면 끝입니다.

---

## 라이선스

**개인 사용 라이선스 (Personal Use License)** — 전문은 [LICENSE](LICENSE).

| | |
|---|---|
| ✅ 허용 | 개인적·비상업적 목적의 학습, 실행, 수정 |
| ❌ 금지 | 재배포 (소스·수정본·exe 모두), 상업적 이용, 제3자 대상 서비스 제공, 저작권 표시 제거 |

Fork 를 포함한 모든 사본에 같은 조건이 적용됩니다. 위 금지 행위가 필요하면
[Issue](https://github.com/ohjn96/private_train/issues) 로 사전 허가를 요청해 주세요.

### 제3자 구성요소

`korail2/` 는 BSD 라이선스(© 2014 Taehoon Kim)이며 위 조건이 적용되지 않습니다.
실행 파일에 번들되는 오픈소스 목록은 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) 참조.

### 면책

이 프로젝트는 한국철도공사(코레일) 및 에스알(SR)과 무관하며 승인받지 않았습니다.
이용자는 각 서비스의 이용약관과 관계 법령을 준수할 책임이 있고, 사용으로 발생한
모든 결과에 대한 책임은 이용자 본인에게 있습니다.
