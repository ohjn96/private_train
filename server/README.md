# server/ — 여러 명이 같이 쓰는 서버 버전

```bash
python -m server          # 보통은 scripts/setup-server.sh 가 systemd 로 띄운다
```

데스크톱 앱(`desktop/`, exe)은 1인용 그대로 두고, 서버에만 필요한 것만 여기 둔다.
화면·라우트는 `webui/` 를, 코레일 API·예약 루프·호출 간격은 `core/` 를 같이 쓴다.
대상: 가족·친구 몇 명, Tailscale 로만 접속, 폰은 홈 화면 앱(PWA).

## 동작 방식

| | 데스크톱 (`python main.py`, exe) | 서버 (`python -m server`) |
|---|---|---|
| 실행 | Flask 개발 서버 | waitress (프로세스 1개, 스레드 여러 개) |
| 로그인 | 1명 | 여러 명 동시에. 코레일 ID별로 세션 따로 |
| 예약 매크로 | 1개 | **서버 전체에 1개**. 쓰는 중이면 다른 사람에겐 "010*** 님이 사용 중" |
| 매크로 로그·상태·중단 | 누구나 | **시작한 사람만** |
| 알림 | 텔레그램 (화면에서 봇 설정) | **웹 푸시** (텔레그램 봇 설정은 막힘) |
| 호출 간격 | 화면에서 1~3초 선택 | 같음. 게이트가 서버 전체에 하나라 IP 단위 상한도 된다 |

### 왜 매크로를 한 번에 한 명만?

모든 사람의 호출이 서버 IP 하나로 나간다. 여러 계정이 동시에 매크로를 돌리면 그 IP 가
코레일에 차단될 위험이 커지고, 막히면 전원이 못 쓴다. 한 명씩 쓰면 호출량은 1인용과 같다.
(한 명이 매크로를 돌리는 동안에도 다른 사람은 로그인·조회는 할 수 있다. 조회도 같은
호출 간격 게이트를 지난다.)

## 파일

```
server/
  __init__.py   create_server_app(): app 을 서버 모드로 만들고 웹 푸시를 붙인다
  __main__.py   waitress 로 실행 (HOST, PORT, SERVER_THREADS)
  push.py       웹 푸시: VAPID 키, 구독 저장, 전송, /api/push/* 라우트
```

서버 모드에서 달라지는 공통 코드 (`app.config['SERVER_MODE']`):
- `webui/routes/reservation.py`: 매크로 주인 기록 (`owns_macro`), 알림 리스너 (`add_macro_listener`)
- `webui/routes/telegram.py`: 봇 설정 API 403, 상태 API 는 남의 매크로를 가림
- `webui/services/service_manager.py`: 로그인 인스턴스를 (provider, 코레일 ID) 로 캐시
- `webui/templates/search.html`: 텔레그램 카드 대신 "폰 알림" 카드

## 웹 푸시

- 키: `~/.train_reservation/vapid_private.pem` (처음 실행 때 생성, 600)
- 구독: `~/.train_reservation/push_subscriptions.json` (코레일 ID별, 600, 재시작해도 유지)
- 보내는 순간: 예약 성공, 결제 완료/실패, 매크로 중단 (1000회 진행 알림은 안 보냄)
- 폰 쪽 조건: iPhone 은 iOS 16.4+ 이고 홈 화면에 추가한 앱에서 켜야 함. HTTPS 필수
  (`setup-server.sh --tailscale` 이 `tailscale serve` 로 붙여 준다)
- 앱을 지웠거나 알림을 끈 기기(404/410)는 자동으로 목록에서 빠진다

## 서버에 있는 비밀 정보

| 무엇 | 어디 | 재시작하면 |
|---|---|---|
| 코레일 비밀번호, 카드 정보 | 프로세스 메모리 | 사라짐 → 다시 로그인 |
| 세션 서명 키 | `~/.train_reservation/secret_key` | 유지 |
| VAPID 키, 푸시 구독 | `~/.train_reservation/` | 유지 |
| 접근 비밀번호 등 설정 | `/etc/train.env` | 유지 |

## 나중에

- 동시 매크로를 2개 이상 허용하려면 `TelegramService` 의 매크로 상태·로그 버퍼를 사용자별로
  나눠야 한다 (`core.reservation.run_reservation` 은 이미 사용자별로 돌 수 있다).
- 텔레그램을 서버에서도 쓰려면: 봇 1개 + 웹에서 받은 코드로 `/link` 해서 chat_id ↔ 코레일 ID 연결.
