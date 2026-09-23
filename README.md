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
- **텔레그램 봇 연동** (알림 + 원격 제어)
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

> 🔒 기본으로는 **이 컴퓨터에서만** 열립니다 (`127.0.0.1`). 같은 와이파이의 폰·다른 PC 에서
> 열려면 `HOST=0.0.0.0` 을 주고 `http://<이 PC 의 IP>:5050` 으로 접속하세요. IP 가 아니라
> 이름(예: `mypc.local`)으로 열 거라면 `ALLOWED_HOSTS=mypc.local` 도 함께 줍니다.
> 디버그 모드는 `FLASK_DEBUG=true` 를 줄 때만 켜집니다 (절대 LAN 에 연 채로 켜지 마세요).

| 하고 싶은 것 | 명령 (Windows) | 명령 (Linux/macOS) |
|---|---|---|
| 그냥 실행 | `.\scripts\run.ps1` | `./scripts/run.sh` |
| 의존성 다시 설치 | `.\scripts\run.ps1 -Reinstall` | `./scripts/run.sh --reinstall` |
| 다른 포트로 실행 | `.\scripts\run.ps1 -Port 8080` | `PORT=8080 ./scripts/run.sh` |

스크립트가 알아서 하는 일: 가상환경(`venv/`) 없으면 생성 → `requirements` 변경 시에만 재설치 →
Python 캐시 삭제 → `venv` 의 python 으로 실행. **`python` / `python3` 헷갈릴 일 없습니다.**

### 실행 파일 (설치 없이)

[Releases](https://github.com/ohjn96/private_train/releases) 에 Windows / macOS 실행 파일이
올라갑니다. Python 설치가 필요 없고, 실행하면 **브라우저가 자동으로 열립니다.**
(콘솔 창은 서버라 켜둬야 하고, 종료는 그 창에서 Ctrl+C)

**Windows**: `TrainReservationApp-v<버전>.exe` 를 받아 더블클릭.

**macOS**: `TrainReservationApp-v<버전>-macos-arm64.tar.gz` (Apple Silicon 전용).
받아서 더블클릭해 압축을 풀면 폴더 안에 **`실행.command`** 가 있습니다. **그걸 더블클릭하면 끝**입니다.
(서명·공증을 안 한 빌드라 macOS 가 거는 격리를 이 스크립트가 알아서 풀어줍니다.)

> ⚠️ **첫 실행 때 한 번**은 macOS 가 "확인되지 않은 개발자" 라며 막을 수 있습니다.
> 그럴 땐 **시스템 설정 → 개인정보 보호 및 보안** 으로 가면 하단에 **"확인 없이 열기"** 버튼이
> 뜹니다. 한 번 눌러주면 그 다음부터는 더블클릭만으로 실행됩니다.

터미널이 편하다면 아래처럼 해도 됩니다. `curl` 로 받으면 격리 속성 자체가 안 붙어서 더 간단합니다.

```bash
curl -LO https://github.com/ohjn96/private_train/releases/latest/download/TrainReservationApp-v<버전>-macos-arm64.tar.gz
tar -xzf TrainReservationApp-v<버전>-macos-arm64.tar.gz
cd TrainReservationApp-v<버전>-macos-arm64 && ./TrainReservationApp-v<버전>-macos-arm64
```

> 💡 Mac 에 Python 3.12 가 있다면 바이너리 없이 `./scripts/run.sh` 로 실행하는 게 제일 깔끔합니다.
> 보안 확인 절차를 아예 안 거칩니다.

> 브라우저 자동 실행을 끄려면 `NO_BROWSER=1`, 개발 중(스크립트 실행)에 켜려면 `OPEN_BROWSER=1`.

---

## 헤드리스 실행 (화면 없이)

웹 UI 없이 예약 매크로만 돌립니다. 클라우드 인스턴스, 라즈베리파이, 도커처럼
브라우저를 띄울 수 없는 곳에 올려두는 용도입니다. 진입점은 웹과 같은 `main.py`
이고, **예약 로직도 웹과 완전히 같은 루프**를 씁니다.

### 대기 모드 (권장) — 텔레그램으로 조종

열차 정보를 미리 정하지 않고 띄워둡니다. 검색부터 예약까지 전부 텔레그램에서 시킵니다.

```bash
python main.py --headless --id 1234567890 --pw 비밀번호 --telegram-token <봇토큰>
```

띄운 뒤 텔레그램에서:

```
/reserve 서울 부산 2026-10-03 08:00   → 검색 결과에서 번호를 고르면 매크로 시작
/trains    마지막 검색 결과       /status   상태 확인
/stop      매크로 중단            /restart  최신 데이터로 재시작
```

봇 토큰은 텔레그램 [@BotFather](https://t.me/BotFather) 에서 받습니다. 채팅 ID 는
따로 안 넣어도 되고, 실행하면 콘솔에 찍히는 `/start <코드>` 를 봇에게 보내면 등록됩니다.
(아무나 먼저 `/start` 를 보내 봇을 가로채지 못하게, 코드가 맞는 채팅만 받습니다)

> 💡 배포된 실행 파일도 같은 방식으로 됩니다:
> `TrainReservationApp-v2.3.2.exe --headless --telegram-token <봇토큰> ...`

### 한 방 모드 — 노릴 열차를 미리 지정

예매 오픈처럼 시각이 정해져 있을 때 씁니다. 띄우자마자 매크로가 돕니다.

```bash
python main.py --headless --id 1234567890 --pw 비밀번호 \
  --dep 서울 --arr 부산 --date 2026-10-03 --from 08:00 --to 12:00
```

먼저 `--dry-run` 으로 설정을 점검하세요. 조회만 하고 매크로는 돌리지 않습니다.

```bash
python main.py --headless ... --dry-run
```
```
🚄 헤드리스 예약 러너 v2.3.2
[08:00:01] 서울 → 부산  20261003  08:00~12:00
[08:00:02] 로그인 성공
[08:00:03] 대상 열차 2편:
    KTX   101  08:30→10:40  일반🟢 특실🔴
    KTX   103  09:30→11:50  일반🔴 특실🔴
```

### 주요 옵션

| 옵션 | 환경변수 | 설명 |
|---|---|---|
| `--id` / `--pw` | `KORAIL_ID` / `KORAIL_PW` | 코레일 계정 (필수) |
| `--dep` / `--arr` | `TRAIN_DEP` / `TRAIN_ARR` | 출발·도착역 (한 방 모드에서 필수) |
| `--date` | `TRAIN_DATE` | 출발일 `YYYYMMDD` (한 방 모드에서 필수) |
| `--from` / `--to` | `TRAIN_FROM` / `TRAIN_TO` | 노릴 시간대. `--to` 생략 시 `--from` +3시간 |
| `--trains` | `TRAIN_NUMBERS` | 특정 열차번호만 (`101,103`). 주면 시간대는 무시 |
| `--passengers` | `PASSENGERS` | 좌석 수 1 또는 2 |
| `--interval` | `CALL_INTERVAL` | 코레일 호출 간격(초, 1~3). 기본 2 |
| `--sequential` | `SEQUENTIAL` | 2석을 한 석씩 순차로 (같은 열차 고정) |
| `--telegram-token` | `TELEGRAM_BOT_TOKEN` | 알림 + 원격 조종 (대기 모드에서 필수) |
| `--card-number` 외 | `CARD_*` | 예약 성공 시 자동결제 |
| — | `KORAIL_MIN_API_INTERVAL` | API 호출 최소 간격(초). 1 미만으로는 안 내려감 |

전체 목록은 `python main.py --headless --help`. 모든 옵션은 환경변수로도 줄 수 있어서,
서버에서는 인자 없이 환경변수만으로 띄우는 편이 낫습니다 (`ps` 에 비밀번호가 안 보입니다).

### 서버에 상주시키기

`Ctrl+C` 와 `SIGTERM` 을 받으면 진행 중인 시도를 마치고 정리 후 종료합니다.
systemd 라면 이 정도면 충분합니다.

```ini
# /etc/systemd/system/train.service
[Service]
WorkingDirectory=/opt/private_train
EnvironmentFile=/etc/train.env      # KORAIL_ID, KORAIL_PW, TELEGRAM_BOT_TOKEN … (chmod 600)
ExecStart=/opt/private_train/venv/bin/python main.py --headless
Restart=on-failure
```

### 올리기 전에 알아둘 것

- **무료 티어 대부분은 이 용도에 안 맞습니다.** Render·Railway 무료 등급은 트래픽이
  없으면 슬립에 들어가는데, 표가 날 때까지 계속 조회해야 하는 매크로와는 상극입니다.
  24시간 상주가 진짜 무료인 건 Oracle Cloud Always Free 정도입니다.
- **GitHub Actions 크론으로 돌리지 마세요.** 공짜지만 ToS 위반이라 계정이 정지될 수 있습니다.
- **데이터센터 IP 가 막힐 수 있습니다.** 코레일이 클라우드 대역을 차단하면 코드와 무관하게
  실패합니다. 집에 있는 기기(라즈베리파이, 안 쓰는 노트북)가 이 점에서 안전합니다.
- **계정 정보가 그 서버에 남습니다.** 환경변수 파일 권한(`chmod 600`)을 꼭 확인하세요.

### 안드로이드 앱 (APK)

안드로이드폰이면 앱을 깔아서 **폰에서 직접** 돌릴 수 있습니다. 서버가 필요 없고, 코레일 호출도
각자 폰의 IP 로 나갑니다. 앱을 내리거나 화면을 꺼도 매크로가 계속 돌고, 예약 성공은 폰 알림으로 옵니다.
[Releases](https://github.com/ohjn96/private_train/releases) 에서 `TrainReservationApp-v<버전>.apk` 를 받아 설치하세요.
설치·빌드·서명 방법은 [mobile/android/README.md](mobile/android/README.md).

### iPhone 앱 (.ipa)

아이폰도 앱을 깔아 **폰에서 직접** 돌릴 수 있습니다. 단, **앱을 화면에 띄워 둔 동안에만** 매크로가 돕니다
(iOS 제약. 도는 동안엔 자동 잠금이 꺼집니다). Mac·유료 개발자 계정 없이, Windows PC 의
[Sideloadly](https://sideloadly.io/) 와 무료 Apple ID 로 `TrainReservationApp-v<버전>.ipa` 를 설치합니다 (7일마다 다시 서명).
설치·빌드 방법은 [mobile/ios/README.md](mobile/ios/README.md).

### Oracle Cloud 에 올리기 (무료, 폰에서 접속, 여러 명)

PC 를 계속 켜 둘 수 없을 때. Oracle Cloud Always Free VM 에 **서버 버전**(`python -m server`)을
올리고 폰에서 Tailscale 로 접속합니다. 포트를 인터넷에 열지 않아도 됩니다.
가족·친구가 같이 쓸 수 있고, 예약 매크로는 한 번에 한 명만 돌립니다. 자세한 건 [server/README.md](server/README.md).

1. Oracle Cloud 가입. **홈 리전을 Seoul 또는 Chuncheon** 으로 (나중에 못 바꿈, 해외 IP 는 코레일이 막을 수 있음)
2. 인스턴스 생성: 이미지 **Ubuntu 24.04**, Shape 는 Always Free 표시된 것
3. SSH 로 접속해서:

```bash
git clone https://github.com/ohjn96/private_train.git && cd private_train
./server/setup-server.sh --tailscale              # 서버 버전 (웹 + 웹 푸시 알림)
./server/setup-server.sh --headless --tailscale   # 혼자, 텔레그램으로만 쓸 때
```

4. 폰(과 같이 쓸 가족·친구 폰)에 Tailscale 앱 설치 → 같은 tailnet 에 초대(Share) →
   스크립트가 알려준 `https://<서버이름>.ts.net` 접속 → **접근 비밀번호** 입력 → 코레일 로그인
   (처음 한 번 Tailscale 관리 화면에서 HTTPS 를 켜라는 안내가 나오면 따라 하면 됩니다)

#### 폰에서 앱처럼 쓰기

- **iPhone**: Safari 로 접속 → 공유 버튼 → **홈 화면에 추가**. **Android**: Chrome 메뉴 → **앱 설치**
- 홈 화면 아이콘으로 열면 주소창 없이 앱처럼 뜹니다. iPhone 은 홈 화면 앱과 Safari 가
  로그인 정보를 따로 가지므로, 앱으로 처음 열 때 한 번 더 로그인하면 됩니다.
- **예약 매크로는 서버에서 돕니다.** Safari·앱을 내리거나 닫아도, 폰을 잠가도 계속 시도합니다.
  다시 열면 그동안의 로그를 이어서 보여줍니다.
- 앱이 꺼져 있어도 **"폰 알림" 카드에서 알림을 켜 두면** 예약 성공·결제·중단을 푸시로 받습니다.
  (iPhone 은 홈 화면 앱에서 켜야 하고 iOS 16.4 이상)

설정은 `/etc/train.env` 에 있고, 바꾼 뒤엔 `sudo systemctl restart train`. 업데이트는 `git pull` 후
스크립트를 다시 돌리면 됩니다.

| 환경변수 | 설명 |
|---|---|
| `APP_PASSWORD` | 설정하면 모든 페이지 앞에 접근 비밀번호를 묻습니다. 외부에 열 땐 필수 |
| `FLASK_DEBUG` | 서버에선 반드시 `false`. 켜져 있으면 웹 디버거로 원격 코드 실행이 가능해집니다 |
| `HOST` / `PORT` | 바인딩 주소/포트 (기본 `127.0.0.1` / `5050`). LAN 에 열려면 `HOST=0.0.0.0` |
| `ALLOWED_HOSTS` | `localhost`·IP 주소 말고 **이름**으로 접속할 때 그 이름 (쉼표 구분, `.ts.net` 처럼 앞에 점을 붙이면 하위 도메인 전부). DNS 리바인딩 공격을 막으려고 모르는 이름으로 온 요청은 400 으로 거절합니다. `--tailscale` 설치 시 Tailscale 주소가 자동으로 들어갑니다 |
| `COOKIE_SECURE` | `1` 이면 세션 쿠키를 HTTPS 로만 보냅니다 (`--tailscale` 설치 시 자동) |
| `FLASK_SECRET_KEY` | 세션 서명 키. 비우면 `~/.train_reservation/secret_key` 에 무작위로 만들어 둡니다 |

> 🔒 코레일 비밀번호와 카드 정보는 브라우저 쿠키가 아니라 서버 메모리에만 둡니다.
> 그래서 서버를 재시작하면 웹에서 다시 로그인해야 합니다.

> ℹ️ 여러 명이 각자 코레일 계정으로 로그인할 수 있지만, **예약 매크로는 서버 전체에 한 번에 하나**입니다.
> 누가 쓰는 중이면 "010*** 님이 사용 중" 이라고 뜨고, 남의 로그를 보거나 멈출 수는 없습니다.
> 모든 호출이 서버 IP 하나로 나가므로 코레일 차단을 피하려는 제한입니다.

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
- API 호출 간격은 예약 화면에서 1~3초 중 선택 (기본 2초, 1초 미만 불가), 단 좌석을 찾은 직후의 예약만 즉시 실행
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
3. 화면에 나온 `/start <코드>` 를 봇 대화창에 전송 → **Chat ID 자동 등록**
   (코드는 10분 동안 한 번만 쓸 수 있습니다. 코드 없는 `/start` 는 무시합니다)
4. **"자동 연결"** 체크 시 다음 접속부터 자동 연결

> 💡 Bot Token은 브라우저 localStorage에 저장되어 새로고침 후에도 유지됩니다.

### Chat ID란?

**Chat ID**는 텔레그램이 각 대화(채팅)에 부여하는 **고유 숫자 식별자**입니다.  
봇이 메시지를 보내려면 "어디로 보낼지" 알아야 하는데, 그 목적지가 Chat ID입니다.

| 질문 | 답변 |
|------|------|
| 직접 입력해야 하나요? | **아니요.** 봇에게 `/start <코드>` 만 보내면 자동 등록됩니다 |
| 어디서 확인하나요? | 봇에게 `/chatid` 명령어를 보내면 표시됩니다 |
| 왜 필요한가요? | 봇이 예약 성공 알림을 보낼 대상을 지정하기 위함 |
| 예시 | `123456789` (숫자) |

```
사용자 → 봇: /start ABCD2345   (앱이 보여준 일회용 코드)
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
| `/start` | 봇 연결 및 Chat ID 등록 (앱이 보여준 코드 필요) | `/start ABCD2345` |
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
│   ── 공통 (세 앱이 같이 쓴다) ──
├── core/                       # 코레일 API 래퍼, 예약 루프, 호출 간격
├── korail2/                    # 코레일 통신 라이브러리
├── webui/                      # 화면(templates·static)과 라우트. 세 앱 모두 이 화면을 쓴다
│
│   ── 앱 3개 ──
├── desktop/                    # ① PC 앱: python -m desktop (= python main.py), exe 빌드(build/), 헤드리스
├── server/                     # ② 서버: python -m server, 여러 명·웹 푸시, setup-server.sh
├── mobile/                     # ③ 폰 앱: 안드로이드(APK)·iPhone(.ipa), 폰 안에서 webui 를 띄운다
│
├── tests/                      # 회귀 테스트 (네트워크 불필요)
├── scripts/                    # 실행/릴리스 스크립트 (run.sh, run.ps1, run.bat, release.sh)
├── .github/workflows/          # 태그 푸시 시 exe(Windows/macOS)·APK·IPA 자동 빌드
├── main.py                     # 하위 호환 진입점 (= python -m desktop)
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

PyInstaller 는 크로스 컴파일이 안 되므로, 플랫폼별 러너에서 각각 빌드합니다
(Windows exe + macOS arm64).

```bash
./scripts/release.sh 2.3.1
```

VERSION·package.json 을 올리고 커밋 → `v2.3.1` 태그 푸시 → GitHub Actions
(`.github/workflows/release.yml`)가 Windows·macOS 러너에서 실행 파일을 빌드해
**Release asset 으로 자동 첨부**합니다. 태그 없이 Actions 탭에서 수동 실행(Run workflow)하면
Release 없이 아티팩트로만 받을 수 있습니다.

> 💡 GitHub Actions 는 무료 플랜에서도 씁니다. 이 저장소는 퍼블릭이라 무제한입니다.
> (프라이빗으로 바꾸면 월 2,000분 한도에 **Windows 2배, macOS 10배**로 차감되니 주의.)
> 두 잡이 병렬로 돌아 한 번에 3분 안팎입니다.

### 2. Windows PC 에서 직접 빌드

```powershell
.\desktop\build\build.ps1          # venv 준비 + 의존성 설치 + 빌드
.\desktop\build\build.ps1 -SkipInstall   # 이미 설치돼 있으면
```
더블클릭으로 하려면 `desktop\build\build.bat`.

### 3. 파이썬으로 직접

```bash
python desktop/build/build.py unified
```
PyInstaller 옵션은 `desktop/build/build.py` 한 곳에만 있고, `build.ps1` 은 이를 호출만 합니다.

---

## 라이선스

**개인 사용 라이선스 (Personal Use License)** — 전문은 [LICENSE](LICENSE).

| | |
|---|---|
| ✅ 허용 | 개인적·비상업적 목적의 학습, 실행, 수정 |
| ❌ 금지 | 재배포 (소스·수정본·exe 모두), 상업적 이용, 제3자 대상 서비스 제공 |

### 제3자 구성요소

`korail2/` 는 BSD 라이선스(© 2014 Taehoon Kim)이며 위 조건이 적용되지 않습니다.
실행 파일에 번들되는 오픈소스 목록은 [THIRD-PARTY-NOTICES.md](THIRD-PARTY-NOTICES.md) 참조.

### 면책

이 프로젝트는 한국철도공사(코레일) 및 에스알(SR)과 무관하며 승인받지 않았습니다.
이용자는 각 서비스의 이용약관과 관계 법령을 준수할 책임이 있고, 사용으로 발생한
모든 결과에 대한 책임은 이용자 본인에게 있습니다.
