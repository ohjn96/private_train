# -*- coding: utf-8 -*-
"""헤드리스 예약 러너.

화면도 브라우저도 없는 환경(클라우드 인스턴스, 라즈베리파이, 도커)에서 예약
매크로만 돌린다. 진행 상황은 표준출력과 (설정했다면) 텔레그램으로 나간다.
두 가지 모드가 있다.

**대기 모드** — 열차 정보 없이 텔레그램 토큰만 주고 띄워두면, 검색부터 예약까지
전부 텔레그램에서 시킨다. 서버에 상주시키는 경우 이쪽이 편하다.

    python main.py --headless --telegram-token <토큰>

**한 방 모드** — 노릴 열차를 인자로 주면 즉시 매크로를 시작한다. 예매 오픈처럼
시각이 정해진 경우에 쓴다.

    python main.py --headless --dep 서울 --arr 부산 --date 20261003 --from 08:00

매크로 본체는 웹 UI 가 쓰는 것과 같은 함수다. 예약 로직이 두 벌로 갈라지지
않도록 app.routes.reservation 의 루프를 그대로 가져다 쓴다.
"""
from __future__ import annotations

import argparse
import os
import signal
import sys
import threading
from dataclasses import dataclass, field
from datetime import datetime, timedelta

from app.services.base_service import SeatOption, TrainInfo
from app.services.korail_service import KorailService
from app.services.telegram_service import TelegramService
from app.version import get_version

#: 유일한 서비스 제공자 (SRT 도 코레일 API 로 함께 조회된다)
PROVIDER = 'korail'

#: --to 를 생략했을 때 --from 으로부터 몇 시간까지를 대상으로 볼지
DEFAULT_WINDOW_HOURS = 3

EXIT_OK = 0
EXIT_CONFIG = 1
EXIT_LOGIN = 2
EXIT_NO_TRAIN = 3

LOG_ICONS = {'success': '✅', 'error': '❌', 'warning': '⚠️', 'stopped': '⏹️'}


class ConfigError(Exception):
    """설정이 잘못됐다. 로그인 시도 전에 걸러진다."""


@dataclass
class Trip:
    """한 방 모드에서 노릴 구간. 대기 모드에서는 None 이다."""
    dep: str
    arr: str
    date: str
    since: str
    until: str
    numbers: list[str] = field(default_factory=list)


@dataclass
class Plan:
    """검증을 마친 실행 계획."""
    trip: Trip | None
    card: dict | None
    seat_option: SeatOption
    passengers: int
    sequential: bool


# ──────────────────────────────────────────────── 입력 파싱

def parse_date(value: str) -> str:
    """'20261003' / '2026-10-03' → 'YYYYMMDD'."""
    raw = str(value).strip().replace('-', '')
    # strptime 은 '2026103' 을 2026-01-03 으로 받아주므로 길이를 먼저 본다
    if len(raw) != 8:
        raise ValueError(f"날짜 형식은 YYYYMMDD 여야 합니다: {value!r}")
    try:
        datetime.strptime(raw, '%Y%m%d')
    except ValueError:
        raise ValueError(f"날짜 형식은 YYYYMMDD 여야 합니다: {value!r}")
    return raw


def parse_time(value: str, label: str) -> str:
    """'8' / '08:30' / '0830' / '083000' → 'HHMMSS'."""
    raw = str(value).strip().replace(':', '')
    if not raw.isdigit():
        raise ValueError(f"{label} 시각이 숫자가 아닙니다: {value!r}")

    if len(raw) in (1, 2):
        raw = raw.zfill(2) + '0000'
    elif len(raw) == 4:
        raw += '00'
    elif len(raw) != 6:
        raise ValueError(
            f"{label} 시각은 8 / 08:30 / 0830 / 083000 형식이어야 합니다: {value!r}"
        )

    if int(raw[:2]) > 23 or int(raw[2:4]) > 59 or int(raw[4:]) > 59:
        raise ValueError(f"{label} 시각이 올바르지 않습니다: {value!r}")
    return raw


def shift_time(hhmmss: str, hours: int) -> str:
    """시각을 몇 시간 뒤로 민다. 자정을 넘기면 그날 끝(235959)에서 멈춘다."""
    base = datetime.strptime(hhmmss, '%H%M%S')
    moved = base + timedelta(hours=hours)
    if moved.day != base.day:
        return '235959'
    return moved.strftime('%H%M%S')


def parse_numbers(value: str | None) -> list[str]:
    """'101, 103' → ['101', '103']."""
    if not value:
        return []
    return [n.strip() for n in value.split(',') if n.strip()]


def mask(user_id: str) -> str:
    """로그에 찍을 아이디를 가린다."""
    if len(user_id) <= 4:
        return '*' * len(user_id)
    return f"{user_id[:2]}{'*' * (len(user_id) - 4)}{user_id[-2:]}"


# ──────────────────────────────────────────────── 열차 선택

def select_trains(
    trains: list[TrainInfo], numbers: list[str], since: str, until: str
) -> list[TrainInfo]:
    """조회 결과에서 매크로가 노릴 열차를 고른다.

    열차번호를 지정했으면 그 번호만, 아니면 시간 범위 안의 열차를 전부 고른다.
    """
    if numbers:
        wanted = set(numbers)
        return [t for t in trains if t.train_number in wanted]
    return [t for t in trains if since <= t.dep_time <= until]


def format_train(train: TrainInfo) -> str:
    """한 줄짜리 열차 요약."""
    general = '🟢' if train.general_seat_available else '🔴'
    special = '🟢' if train.special_seat_available else '🔴'
    return (
        f"{train.train_name} {train.train_number:>5}  "
        f"{train.dep_time_formatted}→{train.arr_time_formatted}  "
        f"일반{general} 특실{special}"
    )


# ──────────────────────────────────────────────── 설정 조립

def build_card(args: argparse.Namespace) -> dict | None:
    """카드 자동결제 설정. 카드번호가 없으면 결제를 아예 하지 않는다."""
    if not args.card_number:
        return None

    missing = [
        name
        for name, value in (
            ('--card-password', args.card_password),
            ('--card-validation', args.card_validation),
            ('--card-expire', args.card_expire),
        )
        if not value
    ]
    if missing:
        raise ValueError(f"카드 자동결제에는 {', '.join(missing)} 도 필요합니다.")

    return {
        'card_number': args.card_number,
        'card_password': args.card_password,
        'validation_number': args.card_validation,
        'card_expire': args.card_expire,
        'installment': args.card_installment,
        'card_type': args.card_type,
        'auto_pay': not args.no_auto_pay,
    }


def build_parser() -> argparse.ArgumentParser:
    """명령행 파서. 모든 옵션은 환경변수로도 줄 수 있다."""
    env = os.environ.get
    parser = argparse.ArgumentParser(
        prog='main.py --headless',
        description='화면 없이 돌리는 예약 매크로 (환경변수로도 설정 가능)',
        epilog=(
            '환경변수: KORAIL_ID, KORAIL_PW, TRAIN_DEP, TRAIN_ARR, TRAIN_DATE, '
            'TRAIN_FROM, TRAIN_TO, TRAIN_NUMBERS, SEAT_OPTION, PASSENGERS, '
            'SEQUENTIAL, TELEGRAM_BOT_TOKEN, TELEGRAM_CHAT_ID, CARD_NUMBER, '
            'CARD_PASSWORD, CARD_VALIDATION, CARD_EXPIRE, CARD_INSTALLMENT, '
            'CARD_TYPE, KORAIL_MIN_API_INTERVAL'
        ),
        formatter_class=argparse.RawDescriptionHelpFormatter,
    )

    parser.add_argument('--id', dest='user_id', default=env('KORAIL_ID'),
                        help='코레일 아이디 (멤버십번호/이메일/전화번호)')
    parser.add_argument('--pw', dest='password', default=env('KORAIL_PW'),
                        help='코레일 비밀번호')

    parser.add_argument('--dep', default=env('TRAIN_DEP'), help='출발역 (예: 서울)')
    parser.add_argument('--arr', default=env('TRAIN_ARR'), help='도착역 (예: 부산)')
    parser.add_argument('--date', default=env('TRAIN_DATE'), help='출발일 YYYYMMDD')
    parser.add_argument('--from', dest='time_from', default=env('TRAIN_FROM', '000000'),
                        help='이 시각 이후 열차부터 (기본 00:00)')
    parser.add_argument('--to', dest='time_to', default=env('TRAIN_TO'),
                        help=f'이 시각까지의 열차를 대상으로 (기본: --from +{DEFAULT_WINDOW_HOURS}시간)')
    parser.add_argument('--trains', default=env('TRAIN_NUMBERS'),
                        help='특정 열차번호만 노린다. 쉼표로 구분 (예: 101,103)')

    parser.add_argument('--seat', default=env('SEAT_OPTION', 'GENERAL_FIRST'),
                        choices=[option.value for option in SeatOption],
                        help='좌석 선호 (기본 GENERAL_FIRST)')
    parser.add_argument('--passengers', type=int, default=int(env('PASSENGERS', '1')),
                        help='예약할 성인 좌석 수 (1 또는 2)')
    parser.add_argument('--sequential', action='store_true',
                        default=env('SEQUENTIAL', '').lower() in ('1', 'true', 'yes'),
                        help='2석일 때 한 석씩 순차로 잡는다 (같은 열차로 고정)')

    parser.add_argument('--telegram-token', default=env('TELEGRAM_BOT_TOKEN'),
                        help='텔레그램 봇 토큰 (알림 + 원격 /stop)')
    parser.add_argument('--telegram-chat-id', default=env('TELEGRAM_CHAT_ID'),
                        help='텔레그램 채팅 ID')

    parser.add_argument('--card-number', default=env('CARD_NUMBER'), help='자동결제 카드번호')
    parser.add_argument('--card-password', default=env('CARD_PASSWORD'), help='카드 비밀번호 앞 2자리')
    parser.add_argument('--card-validation', default=env('CARD_VALIDATION'),
                        help='생년월일 YYMMDD (법인카드면 사업자번호)')
    parser.add_argument('--card-expire', default=env('CARD_EXPIRE'), help='카드 유효기간 YYMM')
    parser.add_argument('--card-installment', type=int, default=int(env('CARD_INSTALLMENT', '0')),
                        help='할부 개월 (0 = 일시불)')
    parser.add_argument('--card-type', default=env('CARD_TYPE', 'J'), choices=['J', 'S'],
                        help='J = 개인, S = 법인')
    parser.add_argument('--no-auto-pay', action='store_true',
                        help='카드 정보를 줘도 자동결제는 하지 않는다')

    parser.add_argument('--dry-run', action='store_true',
                        help='조회해서 대상 열차만 보여주고 끝낸다 (설정 점검용)')
    parser.add_argument('--version', action='version', version=f'%(prog)s {get_version()}')
    return parser


# ──────────────────────────────────────────────── 실행

def log(message: str) -> None:
    print(f"[{datetime.now():%H:%M:%S}] {message}", flush=True)


def build_plan(args: argparse.Namespace) -> Plan:
    """인자를 검증해 실행 계획으로 바꾼다.

    열차 정보(--dep/--arr/--date)를 다 주면 한 방 모드, 하나도 안 주고 텔레그램
    토큰만 주면 대기 모드다. 어중간하게 주면 오타일 가능성이 높으니 막는다.
    """
    missing_account = [
        flag for flag, value in (
            ('--id (KORAIL_ID)', args.user_id),
            ('--pw (KORAIL_PW)', args.password),
        ) if not value
    ]
    if missing_account:
        raise ConfigError(f"다음 설정이 빠졌습니다: {', '.join(missing_account)}")

    card = build_card(args)
    seat_option = SeatOption(args.seat)
    passengers = max(1, min(2, args.passengers))
    sequential = args.sequential and passengers > 1

    given = [bool(args.dep), bool(args.arr), bool(args.date)]
    if not any(given):
        if not args.telegram_token:
            raise ConfigError(
                "노릴 열차(--dep/--arr/--date)를 주거나, 텔레그램으로 조종하려면 "
                "--telegram-token 을 주세요."
            )
        return Plan(None, card, seat_option, passengers, sequential)

    if not all(given):
        raise ConfigError("--dep, --arr, --date 는 셋 다 주거나 셋 다 빼야 합니다.")

    date = parse_date(args.date)
    since = parse_time(args.time_from, '시작')
    until = (
        parse_time(args.time_to, '종료') if args.time_to
        else shift_time(since, DEFAULT_WINDOW_HOURS)
    )
    if until < since:
        raise ConfigError(f"종료 시각({until})이 시작 시각({since})보다 빠릅니다.")

    trip = Trip(args.dep, args.arr, date, since, until, parse_numbers(args.trains))
    return Plan(trip, card, seat_option, passengers, sequential)


def connect_telegram(args: argparse.Namespace) -> TelegramService:
    """텔레그램을 붙인다. 한 방 모드에서는 실패해도 그대로 진행한다."""
    telegram = TelegramService.get_instance()
    telegram.set_log_sink(
        lambda kind, message: print(f"{LOG_ICONS.get(kind, '·')} {message}", flush=True)
    )

    if not args.telegram_token:
        return telegram

    result = telegram.configure(args.telegram_token, args.telegram_chat_id or '')
    if not result.get('success'):
        log(f"⚠️  텔레그램 연결 실패: {result.get('message')}")
        return telegram

    log(f"텔레그램 연결됨: {result.get('message')}")
    telegram.start_polling()
    return telegram


def wire_telegram(telegram: TelegramService, args: argparse.Namespace, card: dict | None):
    """원격 명령(/reserve, /stop, /status)이 동작하도록 배선한다.

    _setup_telegram_callbacks 는 웹 세션을 읽으려다 요청 컨텍스트가 없어 조용히
    넘어가므로, 자격증명과 카드 설정은 뒤에서 직접 채워 넣는다.
    """
    from app.routes import reservation

    reservation._setup_telegram_callbacks()
    telegram.store_web_session(
        PROVIDER, {'user_id': args.user_id, 'password': args.password}
    )
    telegram.store_card_settings(card)


def install_signal_handlers(stop: threading.Event) -> None:
    """Ctrl+C / SIGTERM 에 매크로와 대기 루프를 함께 세운다."""
    from app.routes import reservation

    def request_stop(signum, frame):
        log("중단 신호를 받았습니다. 정리 중...")
        reservation.STOP_MACRO = True
        stop.set()

    signal.signal(signal.SIGINT, request_stop)
    if hasattr(signal, 'SIGTERM'):
        signal.signal(signal.SIGTERM, request_stop)


def run_oneshot(
    service, telegram: TelegramService, plan: Plan, dry_run: bool = False
) -> int:
    """지정한 구간을 조회해 바로 매크로를 돌린다. dry_run 이면 조회까지만."""
    from app.routes import reservation

    trip = plan.trip
    log("열차 조회 중...")
    trains = service.search(
        dep=trip.dep, arr=trip.arr, date=trip.date, time=trip.since,
        include_no_seats=True, until_time=trip.until,
    )
    targets = select_trains(trains, trip.numbers, trip.since, trip.until)

    if not targets:
        print("!! 조건에 맞는 열차가 없습니다.", file=sys.stderr)
        if trains:
            print("   조회된 열차:", file=sys.stderr)
            for train in trains:
                print(f"     {format_train(train)}", file=sys.stderr)
        return EXIT_NO_TRAIN

    log(f"대상 열차 {len(targets)}편:")
    for train in targets:
        print(f"    {format_train(train)}", flush=True)

    if dry_run:
        log("--dry-run 이므로 여기서 끝냅니다.")
        return EXIT_OK

    if not telegram.try_start_macro():
        print("!! 이미 매크로가 실행 중입니다.", file=sys.stderr)
        return EXIT_CONFIG

    note = ''
    if plan.passengers > 1:
        note = ' (1인씩 순차)' if plan.sequential else ' (2인 동시)'
    log(f"예약 매크로 시작{note}. 중단하려면 Ctrl+C 또는 텔레그램 /stop")

    reservation.run_reservation_loop(
        service,
        PROVIDER,
        [train.to_dict(index) for index, train in enumerate(targets)],
        plan.seat_option,
        plan.card,
        passenger_count=plan.passengers,
        sequential=plan.sequential,
    )
    return EXIT_OK


def run_standby(telegram: TelegramService, stop: threading.Event) -> int:
    """아무것도 하지 않고 텔레그램 명령을 기다린다."""
    if not telegram.is_connected:
        print("!! 텔레그램에 연결하지 못해 대기 모드를 쓸 수 없습니다.", file=sys.stderr)
        print("   토큰을 확인하거나, 열차를 인자로 지정해 한 방 모드로 쓰세요.", file=sys.stderr)
        return EXIT_CONFIG

    if not telegram.chat_id:
        log("봇에게 /start 를 보내주세요. 채팅 ID 가 등록되면 조종할 수 있습니다.")

    print("", flush=True)
    log("대기 중입니다. 텔레그램에서 시키세요:")
    print("      /reserve 서울 부산 2026-10-03 08:00   검색 → 번호로 선택하면 매크로 시작", flush=True)
    print("      /trains   마지막 검색 결과      /status   상태 확인", flush=True)
    print("      /stop     매크로 중단           /restart  최신 데이터로 재시작", flush=True)
    print("      (이 프로세스를 끄려면 Ctrl+C)", flush=True)
    print("", flush=True)

    stop.wait()
    return EXIT_OK


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)

    try:
        plan = build_plan(args)
    except (ConfigError, ValueError) as error:
        print(f"!! {error}", file=sys.stderr)
        print("   자세한 사용법은 --help 를 보세요.", file=sys.stderr)
        return EXIT_CONFIG

    mode = '대기 모드' if plan.trip is None else '한 방 모드'
    print(f"🚄 헤드리스 예약 러너 v{get_version()} — {mode}")
    if plan.trip:
        trip = plan.trip
        log(
            f"{trip.dep} → {trip.arr}  {trip.date}  "
            f"{trip.since[:2]}:{trip.since[2:4]}~{trip.until[:2]}:{trip.until[2:4]}"
        )

    service = KorailService()
    log(f"코레일 로그인: {mask(args.user_id)}")
    if not service.login(args.user_id, args.password):
        print("!! 로그인 실패. 아이디/비밀번호를 확인하세요.", file=sys.stderr)
        return EXIT_LOGIN
    log("로그인 성공")

    if plan.trip and args.dry_run:
        # 조회 결과만 보여주고 끝낸다. 텔레그램도 붙이지 않는다.
        return run_oneshot(service, TelegramService.get_instance(), plan, dry_run=True)

    telegram = connect_telegram(args)
    wire_telegram(telegram, args, plan.card)

    stop = threading.Event()
    install_signal_handlers(stop)

    try:
        if plan.trip is None:
            return run_standby(telegram, stop)
        return run_oneshot(service, telegram, plan)
    finally:
        telegram.set_log_sink(None)
        if telegram.is_connected:
            telegram.stop_polling()
        log("종료합니다.")


if __name__ == '__main__':
    sys.exit(main())
