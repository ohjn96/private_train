# KTX/SRT 열차 예약 시스템

> **주의: 상업용, 영리행위 등 불법행위 절대 금지**
>
> 이 프로젝트는 개인 학습 목적으로만 사용하세요.

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

## 요구사항

- **Python**: 3.12+
- **Node.js**: 18+ (Tailwind CSS 빌드용, 선택사항)

---

## 설치

### 1. 저장소 클론

```bash
git clone <repository-url>
cd private_train
```

### 2. 가상환경 생성

```bash
# Linux/macOS
python3.12 -m venv venv
source venv/bin/activate

# Windows (PowerShell) - 권장
.\run.ps1  # 가상환경 생성 후 바로 실행 가능

# Windows (수동 가상환경 생성)
python -m venv venv
.\scripts\activate.ps1
```

**⚠️ 중요: 가상환경에서는 `python` 명령어를 사용하세요** (python3 아님!)

### 3. 의존성 설치

```bash
# Linux/macOS
pip install -r requirements.txt

# Windows
pip install -r requirements-windows.txt
```

---

## 실행

### 방법 1: 실행 파일 (가장 간단)

**Windows 사용자:**
```powershell
.\TrainReservationApp.exe
```
별도 설치 없이 바로 실행 가능합니다.

### 방법 2: 스크립트 실행 (개발자)

**빠른 실행:**
```powershell
.\run.ps1  # Windows
```

이 스크립트는 자동으로:
- 가상환경 활성화
- Python 캐시 삭제
- 애플리케이션 실행

**수동 실행:**
```bash
# 1. 가상환경 활성화
.\scripts\activate.ps1  # Windows
# source venv/bin/activate  # Linux/macOS

# 2. 실행 (python3 아님!)
python main.py
```

브라우저에서 `http://localhost:5050` 접속

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
- 2인 동시 예약 / 2인 순차 예약
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

# 또는 run.ps1 사용
.\run.ps1
```

### 코드 변경사항이 반영되지 않음

**원인**: Python 캐시 파일(.pyc)이 이전 코드를 캐시

**해결**:
```powershell
# 캐시 삭제
Get-ChildItem -Recurse -Include *.pyc,__pycache__ | Remove-Item -Recurse -Force

# 또는 run.ps1 사용 (자동 캐시 삭제)
.\run.ps1
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
│   ├── routes/                 # 라우트 (auth, search, reservation, telegram)
│   ├── services/               # 서비스 레이어 (Korail, Telegram)
│   ├── templates/              # Jinja2 템플릿
│   └── static/                 # 정적 파일
├── korail2/                    # Korail API 모듈
├── tests/                      # 회귀 테스트 (네트워크 불필요)
├── build/                      # 빌드 스크립트
├── main.py                     # 진입점
└── requirements.txt            # 의존성
```

---

## 빌드 (실행파일 생성)

```bash
python build/build.py
```

**수동 빌드:**
```bash
# Windows
pyinstaller --onefile --add-data "app/templates;app/templates" --add-data "app/static;app/static" --name=TrainReservationApp main.py

# macOS / Linux
pyinstaller --onefile --add-data "app/templates:app/templates" --add-data "app/static:app/static" --name=TrainReservationApp main.py
```

---

## 라이선스

이 프로젝트는 개인 학습 목적으로만 사용하세요.
