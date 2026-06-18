# -*- coding: utf-8 -*-
"""Reservation routes with SSE support and multi-provider session."""
import json
import time
import threading
from datetime import datetime
from functools import wraps
from flask import Blueprint, request, session, redirect, url_for, Response, jsonify

from app.services import ServiceManager, SeatOption
from app.utils.session_helper import (
    get_current_provider, is_logged_in,
    get_search_state, set_selected_indices
)

bp = Blueprint('reservation', __name__)

# 매크로 중단 신호 및 중복 실행 방지 락
# 단일 사용자 전제이지만 threaded=True 환경에서 안전하게 처리
_macro_stop_event = threading.Event()
_macro_lock = threading.Lock()


def login_required(f):
    """Decorator to require login."""
    @wraps(f)
    def decorated_function(*args, **kwargs):
        if not is_logged_in():
            return redirect(url_for('auth.login'))
        return f(*args, **kwargs)
    return decorated_function


@bp.route('/reserve_select', methods=['POST'])
@login_required
def reserve_select():
    """Store selected trains for reservation."""
    provider = get_current_provider()
    selected_indices = request.form.getlist('train_indices[]')
    seat_option = request.form.get('seat_option', 'GENERAL_FIRST')

    # Store for this provider
    set_selected_indices(
        provider,
        [int(i) for i in selected_indices],
        seat_option
    )

    return jsonify({'success': True, 'count': len(selected_indices)})


@bp.route('/start_reservation')
@login_required
def start_reservation():
    """SSE endpoint for reservation attempts."""
    # 중복 실행 방지: 이미 매크로가 돌고 있으면 즉시 에러 반환
    if not _macro_lock.acquire(blocking=False):
        def _already_running():
            yield f"data: {json.dumps({'type': 'error', 'message': '이미 예약 매크로가 실행 중입니다. 먼저 중단해주세요.'})}\n\n"
        return Response(
            _already_running(),
            mimetype='text/event-stream',
            headers={'Cache-Control': 'no-cache', 'X-Accel-Buffering': 'no'}
        )

    _macro_stop_event.clear()

    provider = get_current_provider()
    service = ServiceManager.get_service(provider)

    # Get provider-specific search state
    search_state = get_search_state(provider)
    selected_indices = search_state.get('selected_indices', [])
    seat_option_str = search_state.get('seat_option', 'GENERAL_FIRST')
    trains_data = search_state.get('trains', [])

    # Convert seat option
    seat_option_map = {
        'GENERAL_FIRST': SeatOption.GENERAL_FIRST,
        'GENERAL_ONLY': SeatOption.GENERAL_ONLY,
        'SPECIAL_FIRST': SeatOption.SPECIAL_FIRST,
        'SPECIAL_ONLY': SeatOption.SPECIAL_ONLY,
    }
    seat_option = seat_option_map.get(seat_option_str, SeatOption.GENERAL_FIRST)

    def generate():
        try:
            attempt = 0

            # Collect valid selected trains
            selected_trains = []
            for idx in selected_indices:
                if idx < len(trains_data):
                    selected_trains.append(trains_data[idx])

            if not selected_trains:
                yield f"data: {json.dumps({'type': 'error', 'message': '선택된 열차가 없습니다.'})}\n\n"
                return

            # Use earliest departure time for search
            earliest_train = min(selected_trains, key=lambda t: t['dep_time'])

            consecutive_errors = 0
            MAX_BACKOFF = 30

            while not _macro_stop_event.is_set():
                attempt += 1
                timestamp = datetime.now().strftime('%H:%M:%S')

                # ONE search per attempt cycle (not per train)
                try:
                    fresh_trains = service.search(
                        dep=earliest_train['dep_station'],
                        arr=earliest_train['arr_station'],
                        date=earliest_train['dep_date'],
                        time=earliest_train['dep_time'],
                        include_no_seats=True
                    )
                    consecutive_errors = 0
                except Exception as e:
                    consecutive_errors += 1
                    backoff = min(2 ** consecutive_errors, MAX_BACKOFF)
                    is_blocked = 'Blocked' in str(e) or 'abnormal' in str(e)
                    if is_blocked:
                        backoff = MAX_BACKOFF
                        yield f"data: {json.dumps({'type': 'error', 'message': f'[{timestamp}] IP 차단 감지 — {backoff}초 대기 후 재시도...'})}\n\n"
                    else:
                        yield f"data: {json.dumps({'type': 'error', 'message': f'[{timestamp}] 검색 오류: {str(e)} — {backoff}초 대기'})}\n\n"
                    time.sleep(backoff)
                    continue

                if not fresh_trains:
                    yield f"data: {json.dumps({'type': 'log', 'message': f'[{timestamp}] 시도 #{attempt}: 검색 결과 없음'})}\n\n"
                    time.sleep(3)
                    continue

                # Build lookup by (train_number, dep_time)
                fresh_lookup = {}
                for t in fresh_trains:
                    fresh_lookup[(t.train_number, t.dep_time)] = t

                for train_info in selected_trains:
                    if _macro_stop_event.is_set():
                        yield f"data: {json.dumps({'type': 'stopped', 'message': '예약이 중단되었습니다.'})}\n\n"
                        return

                    train_name = train_info['train_name']
                    dep_time = f"{train_info['dep_time'][:2]}:{train_info['dep_time'][2:4]}"

                    yield f"data: {json.dumps({'type': 'log', 'message': f'[{timestamp}] 시도 #{attempt}: {train_name} ({dep_time}) 예약 시도 중...'})}\n\n"

                    matching_train = fresh_lookup.get(
                        (train_info['train_number'], train_info['dep_time'])
                    )

                    if not matching_train:
                        yield f"data: {json.dumps({'type': 'log', 'message': f'[{timestamp}] {train_name} ({dep_time}): 열차를 찾을 수 없음'})}\n\n"
                        continue

                    if not matching_train.has_seat():
                        yield f"data: {json.dumps({'type': 'log', 'message': f'[{timestamp}] {train_name} ({dep_time}): 좌석 없음'})}\n\n"
                        continue

                    # Attempt reservation
                    try:
                        result = service.reserve(matching_train, seat_option)
                    except Exception as e:
                        yield f"data: {json.dumps({'type': 'error', 'message': f'[{timestamp}] 예약 오류: {str(e)}'})}\n\n"
                        continue

                    if result.success:
                        yield f"data: {json.dumps({'type': 'success', 'message': f'예약 성공! {train_name} ({dep_time})', 'reservation_id': result.reservation_id})}\n\n"
                        _macro_stop_event.set()
                        return
                    else:
                        yield f"data: {json.dumps({'type': 'log', 'message': f'[{timestamp}] {train_name} ({dep_time}): {result.message}'})}\n\n"

                # Wait before next attempt
                time.sleep(3)

            yield f"data: {json.dumps({'type': 'stopped', 'message': '예약이 중단되었습니다.'})}\n\n"

        finally:
            _macro_lock.release()

    return Response(
        generate(),
        mimetype='text/event-stream',
        headers={
            'Cache-Control': 'no-cache',
            'X-Accel-Buffering': 'no'
        }
    )


@bp.route('/stop_macro', methods=['POST'])
@login_required
def stop_macro():
    """Stop the reservation macro."""
    _macro_stop_event.set()
    return jsonify({'success': True})
