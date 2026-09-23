# server/ — 여러 명이 같이 쓰는 서버 버전 (설계)

데스크톱 앱(`app/`, exe)은 1인용 그대로 두고, 서버 버전은 `server/` 에 따로 만든다.
둘 다 `core/`(코레일 API, 예약 루프, 속도 제한)를 공유한다.

대상: 가족·친구 2~5명, Tailscale 로만 접속, 웹 모드(헤드리스 안 씀), 폰은 PWA.

## 지금 1인용인 곳 → 서버에서 바뀌는 것

| 지금 (`app/`) | 문제 | 서버 (`server/`) |
|---|---|---|
| `ServiceManager._services` (전역 1개) | 두 번째 사람이 로그인하면 첫 사람 세션을 밀어냄 | `UserRegistry`: 코레일 ID별 `UserSession` |
| `reservation.STOP_MACRO` (전역) | 한 명이 멈추면 전부 멈춤 | 사용자별 `threading.Event` |
| `TelegramService` 싱글톤 | 로그·상태·봇·자격증명을 한 사람 것만 가짐 | 역할별로 쪼갬 (아래) |
| `core.rate_limit.korail_api` (전역 1.5초) | N명이면 각자 N배 느려짐 | 계정별 1.5초 + 서버 전체 상한 |
| Flask 개발 서버 | 운영용 아님 | waitress (단일 프로세스, 스레드 여러 개) |

## 모듈

```
server/
  __main__.py      python -m server  → waitress 로 실행
  app.py           create_server_app(): 블루프린트 등록, 관문, 템플릿은 app/templates 재사용
  users.py         UserSession, UserRegistry
  macro.py         MacroRunner  (core.reservation.run_reservation 을 사용자별 스레드로)
  bot.py           ServerBot    (봇 1개, chat_id ↔ 사용자 연결, 명령 분배)
  routes/          auth, search, reservation, telegram_link  (응답 JSON 은 app 과 같은 모양)
```

### UserSession (사용자 1명 = 코레일 계정 1개)
- `service`: 자기 전용 `KorailService` (자기 전용 속도 제한기)
- `credentials`, `card`: 메모리에만 둠 (재시작하면 다시 로그인)
- `macro`: `MacroRunner` 하나 (동시에 1개만)
- `chat_id`: 연결된 텔레그램 채팅 (없으면 알림 안 감)
- 같은 계정으로 폰 앱·Safari 여러 곳에서 로그인하면 같은 UserSession 을 공유한다
  (브라우저 세션 ID → 코레일 ID 매핑)

### MacroRunner (core.reservation 의 Reporter 구현)
- 로그 버퍼·진행 상태·시도 횟수를 사용자별로 보관 → `/macro_stream`, `/api/.../status` 가 이걸 읽음
- 알림(`send_*`)은 `Notifier` 로 넘김 → 지금은 텔레그램, 나중에 웹 푸시를 같은 자리에 추가
- `stop()` = 자기 Event 만 set

### ServerBot (텔레그램 봇 1개)
- 토큰은 `/etc/train.env` 의 `TELEGRAM_BOT_TOKEN` (관리자가 한 번 설정)
- 연결: 웹에서 [텔레그램 연결] → 6자리 코드 발급(10분 유효) → 봇에 `/link 123456`
- chat_id ↔ 코레일 ID 매핑은 파일(600)에 저장 → 재시작 후에도 유지
- 명령(`/reserve`, `/trains`, `/stop`, `/status`)은 보낸 chat_id 의 UserSession 으로 분배
- 봇 통신(getUpdates·sendMessage)과 명령 파싱은 `core/telegram_api.py` 로 떼어내 공유
  (데스크톱 TelegramService 는 당분간 그대로 두고, 나중에 이 위로 옮길 수 있음)

### 속도 제한 (IP 차단 방지)
- `KorailService(limiter=...)` 로 제한기를 주입할 수 있게 `core` 수정 (기본값은 지금 전역 게이트 → 데스크톱 동작 불변)
- 사용자별: 1.5초 (`KORAIL_MIN_API_INTERVAL`)
- 서버 전체: `SERVER_MAX_CALLS_PER_SEC` (기본 2) — 모든 계정이 같은 IP 로 나가므로
- 예약 호출은 지금처럼 기다리지 않고 바로 (좌석을 놓치지 않게), 대신 기록은 남김

## 화면

- `app/templates` 를 그대로 쓰고, 엔드포인트 이름·JSON 모양을 맞춘다.
- 다른 점만 `server_mode` 플래그로 분기:
  - 텔레그램 카드: 봇 토큰 입력 대신 [연결 코드 받기]
  - 상단에 로그인한 사람 표시(이미 있음)
- 선택: `ALLOWED_USERS` (허용할 코레일 ID 목록). 비우면 관문 비밀번호를 아는 누구나.

## 설치

- `setup-server.sh` 가 `python main.py` 대신 `python -m server` 를 띄우도록 변경
- `/etc/train.env` 에 `TELEGRAM_BOT_TOKEN`, `SERVER_MAX_CALLS_PER_SEC` 추가

## 작업 순서 (각 단계마다 테스트)

1. `core`: KorailService 에 limiter 주입 + 서버 전체 상한 제한기 (`CompositeLimiter`)
2. `server/users.py`, `server/macro.py` + 단위 테스트 (2명 동시 매크로, 한 명 중단, 로그 분리)
3. `server/app.py` + routes (기존 템플릿으로 로그인→검색→예약→스트림 동작)
4. `core/telegram_api.py` 추출 + `server/bot.py` (연결 코드, 명령 분배)
5. `server/__main__.py` (waitress), requirements, setup-server.sh, README
6. 폰(PWA)으로 2계정 동시 수동 테스트

## 보류 (결정 필요)

- **알림 방식 (4번)**: 텔레그램 유지 vs 웹 푸시 추가. Notifier 인터페이스로 만들어 두므로
  나중에 웹 푸시를 붙여도 구조는 안 바뀐다. 웹 푸시는 VAPID 키, 구독 저장, `pywebpush` 의존성이 필요.
- 서버 전체 호출 상한 기본값 (2회/초 가정 — 코레일 기준을 모르므로 보수적으로)
