# -*- coding: utf-8 -*-
"""QA 용 가짜 코레일 데스크톱 웹 서버 (화면 캡처·점검 전용).

코레일에는 절대 붙지 않는다: KorailService.login/search 를 가짜로 바꾼다.
HOME 을 임시 폴더로 두고 돌린다 (설정 파일이 실제 홈에 생기지 않게).

    HOME=$(mktemp -d) python scripts/qa/demo_server.py [desktop|server] [port]

추가 주소
  /demo                      가짜 로그인 + 검색 결과 8개 채운 검색 화면
  /demo_run                  매크로가 도는 중인 흉내 (시도 128회, 자동 재시작 표시 포함)
  /demo_last/<state>         마지막 결과 카드
                             success_paid | success_payfail | success_nopay | stopped | gave_up | crash
  /demo_success/<state>      (도는 중 화면에서) 성공 배너 이벤트: pay_running | pay_done | pay_failed | pay_pending
  /demo_reset                매크로·마지막 결과·로그 초기화
"""
import datetime
import os
import sys

ROOT = os.path.abspath(os.path.join(os.path.dirname(__file__), '..', '..'))
sys.path.insert(0, ROOT)
os.chdir(ROOT)
os.environ.setdefault('FLASK_SECRET_KEY', 'qa-demo')

from flask import redirect  # noqa: E402

from core.base_service import TrainInfo, TrainProvider  # noqa: E402
from core.korail_service import KorailService  # noqa: E402


def _fake_login(self, uid, pw):
    self._user_id, self._password = uid, pw
    self._client = type('C', (), {'logined': True, 'logout': lambda s: None})()
    return True


def _fake_search(self, dep, arr, date, time, include_no_seats=True, until_time=None):
    out = []
    for i in range(8):
        h = 6 + i
        out.append(TrainInfo(TrainProvider.KORAIL, 'KTX' if i % 3 else 'KTX-산천', str(101 + i),
                             date, f'{h:02d}0000', date, f'{h + 2:02d}4000', dep, arr,
                             i % 4 == 1, i % 5 == 2))
    return out


def _no_network(*_a, **_k):
    raise RuntimeError('QA demo: 코레일 호출 금지')


KorailService.login = _fake_login
KorailService.is_logged_in = lambda self: self._client is not None
KorailService.search = _fake_search
for _name in ('reserve', 'pay_with_card'):
    if hasattr(KorailService, _name):
        setattr(KorailService, _name, _no_network)

mode = sys.argv[1] if len(sys.argv) > 1 else 'desktop'
port = int(sys.argv[2]) if len(sys.argv) > 2 else 5081
if mode == 'server':
    from server import create_server_app
    app = create_server_app()
else:
    from webui import create_app
    app = create_app(server_mode=False)


def _tg():
    from webui.services.telegram_service import TelegramService
    return TelegramService.get_instance()


@app.route('/demo')
def demo():
    from webui.services import ServiceManager
    from webui.utils.session_helper import get_search_state, set_current_provider, set_search_trains
    ServiceManager.login('korail', '010-1234-5678', 'pw')
    set_current_provider('korail')
    trains = _fake_search(None, '서울', '부산', '20261003', '060000')
    set_search_trains('korail', [dict(
        index=i, train_name=t.train_name, train_number=t.train_number,
        dep_date=t.dep_date, dep_time=t.dep_time, dep_time_formatted=t.dep_time_formatted,
        arr_date=t.arr_date, arr_time=t.arr_time, arr_time_formatted=t.arr_time_formatted,
        duration_formatted=t.duration_formatted, dep_station=t.dep_station, arr_station=t.arr_station,
        general_seat_available=t.general_seat_available,
        special_seat_available=t.special_seat_available) for i, t in enumerate(trains)])
    get_search_state('korail')['form_data'] = {'dep': '서울', 'arr': '부산', 'date': '2026-10-03', 'time': '06:00'}
    return redirect('/')


@app.route('/demo_run')
def demo_run():
    from webui.utils.session_helper import current_user_id
    tg = _tg()
    tg.try_start_macro(owner=current_user_id())
    tg._stored_provider = 'korail'
    tg.set_macro_state(True, {'trains': 'KTX 103(07:00), KTX 107(08:00)', 'current_train': 'KTX 103',
                              'current_time': '07:00', 'current_index': 1, 'current_total': 2})
    tg._macro_attempt = 128
    tg._macro_start_time = datetime.datetime.now() - datetime.timedelta(minutes=12)
    tg.resumed_at = '2026-10-03T14:02:00'
    return redirect('/')


@app.route('/demo_last/<state>')
def demo_last(state):
    tg = _tg()
    try:
        tg.set_macro_state(False)
    except Exception:  # noqa: BLE001
        pass
    tg.resumed_at = None
    tg._stored_provider = 'korail'
    r = {'reason': state, 'ended_at': '2026-10-03T14:14:%02d' % (hash(state) % 60),
         'reserved': None, 'paid': None, 'pay_message': None, 'detail': None,
         'pay_deadline': None, 'autopay': False}
    if state.startswith('success'):
        r.update(reason='success', reserved='KTX 103 서울 07:00 → 부산 09:40',
                 pay_deadline='10월 3일 14:34')
        if state == 'success_paid':
            r.update(paid=True, autopay=True, pay_message='결제 완료')
        elif state == 'success_payfail':
            r.update(paid=False, autopay=True, pay_message='카드 한도 초과')
    elif state == 'stopped':
        r['detail'] = '사용자가 중단했어요'
    elif state == 'crash':
        r['detail'] = 'ConnectionError: 연결이 끊겼어요'
    tg.last_result = r
    return redirect('/')


@app.route('/demo_success/<state>')
def demo_success(state):
    """도는 중(/demo_run)인 화면의 로그 스트림에 예약 성공·결제 이벤트를 흘려 성공 배너를 띄운다.

    state: pay_running | pay_done | pay_failed | pay_pending
    """
    tg = _tg()
    autopay = state != 'pay_pending'
    tg.push_log('success', '예약 성공! KTX 103 (07:00) 서울→부산', autopay=autopay,
                pay_deadline='10월 3일 14:34')
    if state == 'pay_done':
        tg.push_log('success', '결제 완료: 등록한 카드로 결제했어요')
    elif state == 'pay_failed':
        tg.push_log('error', '결제 실패: 카드 한도 초과')
    return {'pushed': state}


@app.route('/demo_reset')
def demo_reset():
    tg = _tg()
    try:
        tg.set_macro_state(False)
    except Exception:  # noqa: BLE001
        pass
    tg.last_result = None
    tg.resumed_at = None
    tg.clear_logs()
    return redirect('/')


if __name__ == '__main__':
    app.run(host='127.0.0.1', port=port, debug=False, threaded=True)
