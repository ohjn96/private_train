# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 프로젝트 개요

KTX/SRT 통합 열차 예약 웹 애플리케이션. SRT와 코레일(KTX)의 열차 검색 및 자동 예약(매크로)을 하나의 웹 UI에서 제공한다. 개인 학습 목적 전용 프로젝트.

## 개발 환경 명령어

```bash
# 앱 실행 (http://localhost:5050)
python main.py

# Tailwind CSS 감시 모드 (CSS 변경 시 자동 빌드)
npm run dev

# Tailwind CSS 프로덕션 빌드
npm run build:css

# 실행파일 생성 (PyInstaller)
python build/build.py
```

- Python 3.12+, Node.js 18+ 필요
- 가상환경: `source venv/bin/activate`
- 포트 변경: `PORT=8080 python main.py`
- 테스트 프레임워크 미설정 (테스트 없음)

## 아키텍처

### 계층 구조

```
Routes (Flask Blueprints) → Services → 외부 API 모듈 (SRT/, korail2/)
```

- **Routes** (`app/routes/`): `auth`, `search`, `reservation` 3개의 Blueprint. HTTP 요청/응답 처리만 담당.
- **Services** (`app/services/`): 비즈니스 로직. `BaseTrainService` 추상 클래스를 `SRTService`, `KorailService`가 구현 (Strategy 패턴).
- **외부 API 모듈**: `SRT/`와 `korail2/`는 각각 SRT, 코레일 공식 API를 호출하는 서드파티 래퍼 모듈.

### 멀티 프로바이더 동시 로그인

SRT와 Korail에 동시에 로그인 상태를 유지할 수 있다. `ServiceManager`가 Flask `g` 객체(요청 범위)에 서비스 인스턴스를 캐싱하고, Flask `session`에 자격증명을 저장하여 요청마다 재인증한다. 현재 활성 프로바이더는 `session['current_provider']`에 저장된다.

세션 구조:
```
session['auth'][provider]         # logged_in, user_id
session['credentials'][provider]  # user_id, password (재로그인용)
session['search_state'][provider] # trains(dict list), selected_indices, seat_option, form_data
session['current_provider']       # 'srt' | 'korail'
```

### SSE 기반 자동 예약 매크로

`reservation.py`의 `/start_reservation` 엔드포인트가 Server-Sent Events로 예약 시도 상태를 실시간 스트리밍한다. 흐름:

1. 매 attempt마다 **단 1회** 열차 검색 (`include_no_seats=True`)
2. 검색 결과에서 선택한 열차를 `(train_number, dep_time)` 키로 매칭
3. 좌석이 있으면 즉시 `reserve()` 시도 → 성공 시 루프 종료
4. IP 차단 감지 시 지수 백오프 (최대 30초)

`STOP_MACRO`는 모듈 레벨 전역 변수 — **단일 사용자 전제**, 동시 접속자 여러 명이면 서로 간섭한다.

### 통합 데이터 모델과 raw_data 패턴

`TrainInfo`와 `ReservationResult` dataclass가 프로바이더에 관계없이 동일한 인터페이스를 제공한다.

`TrainInfo.raw_data['_original']`에는 서드파티 모듈의 원본 객체(SRTTrain 또는 KorailTrain)가 담긴다. `reserve()` 호출 시 이 원본 객체가 필요하다. 이 객체는 JSON 직렬화가 불가능하므로 **세션에 저장되지 않는다**. 예약 매크로는 이 때문에 매 attempt마다 fresh 검색을 수행해 원본 객체를 다시 확보한다.

### 세션 관리

`app/utils/session_helper.py`가 프로바이더별 세션 키를 관리한다. `search.py`의 `login_required`는 현재 프로바이더에 로그인되어 있지 않아도 다른 프로바이더에 로그인된 상태라면 해당 프로바이더로 리다이렉트한다.

### 프론트엔드

Jinja2 템플릿 + Tailwind CSS + 바닐라 JavaScript. `search.html`이 검색/결과 표시의 핵심 UI로, 무한 스크롤과 지연 로딩(`/api/search_more` 엔드포인트)을 포함한다. Tailwind 커스텀 색상: SRT(보라 `#7C3AED`), KTX(빨강 `#EF4444`).

기본 출발역: SRT → `수서`, Korail → `용산`.

## 주의사항

- `SRT/`와 `korail2/` 모듈은 외부 API 프로토콜에 의존하므로 수정 시 API 호환성을 반드시 확인할 것
- `ServiceManager`는 매 요청마다 자격증명으로 재로그인하는 구조이므로 성능에 영향을 줄 수 있음
- Flask `session`에 자격증명을 저장하므로 `FLASK_SECRET_KEY` 환경변수를 반드시 설정할 것
- `korail_service.py`에서 `NoResultsError`는 import 없이 참조된다 — korail2 모듈 내부에서 raise되는 예외를 except로만 잡으므로 런타임 오류는 없지만, korail2 API 변경 시 주의
