# -*- coding: utf-8 -*-
"""코레일 API 호출 간격 제한기.

코레일 API는 절대로 1초에 한 번을 넘겨 호출하지 않는다. 호출 지점이 여러 곳
(웹 요청, 예약 매크로 스레드, 텔레그램 매크로)이라 각자 sleep 을 넣는 방식으로는
간격을 보장할 수 없어서, 프로세스 전역에 하나뿐인 게이트를 두고 모든 호출이
이 게이트를 통과하도록 한다.

예외는 예약 호출 하나뿐이다. 조회에서 좌석을 발견한 순간 1.5초를 기다리면 그 사이
좌석이 없어지므로, 예약만은 기다리지 않고 곧바로 나간다(`note_call`). 대신 나간
시각은 게이트에 기록해서, 그 다음 호출부터는 다시 간격을 지킨다.
"""
import os
import threading
import time

#: 호출 사이 최소 간격(초). 1초 제한에 여유를 둔 기본값.
DEFAULT_MIN_INTERVAL = 1.5


def _configured_interval() -> float:
    """환경변수로 간격을 조정하되 1초 미만으로는 절대 내려가지 않는다."""
    try:
        value = float(os.environ.get('KORAIL_MIN_API_INTERVAL', DEFAULT_MIN_INTERVAL))
    except ValueError:
        value = DEFAULT_MIN_INTERVAL
    return max(1.0, value)


#: 사용자가 고를 수 있는 호출 간격 범위(초).
MIN_CALL_INTERVAL = 1.0
MAX_CALL_INTERVAL = 3.0


def clamp_call_interval(value, default: float = DEFAULT_MIN_INTERVAL) -> float:
    """사용자가 보낸 간격 값을 1~3초 안으로 맞춘다. 숫자가 아니면 default."""
    try:
        seconds = float(value)
    except (TypeError, ValueError):
        return default
    if seconds != seconds:  # NaN
        return default
    return min(MAX_CALL_INTERVAL, max(MIN_CALL_INTERVAL, seconds))


class RateLimiter:
    """마지막 호출로부터 min_interval 이 지날 때까지 막아 세우는 게이트.

    락을 쥔 채로 대기하기 때문에 여러 스레드가 동시에 들어와도 호출이 직렬화되고,
    간격이 겹쳐서 줄어드는 일이 없다.
    """

    def __init__(self, min_interval: float):
        self._min_interval = min_interval
        self._lock = threading.Lock()
        self._next_allowed = 0.0

    @property
    def min_interval(self) -> float:
        return self._min_interval

    def set_interval(self, seconds: float) -> None:
        """간격을 바꾼다. 1초 하한은 여기서도 지킨다."""
        with self._lock:
            self._min_interval = max(1.0, float(seconds))

    def wait(self) -> float:
        """호출이 허용될 때까지 대기하고, 실제로 기다린 시간(초)을 돌려준다."""
        with self._lock:
            now = time.monotonic()
            waited = self._next_allowed - now
            if waited > 0:
                time.sleep(waited)
                now = self._next_allowed
            else:
                waited = 0.0
            self._next_allowed = now + self._min_interval
        return waited

    def note_call(self) -> None:
        """기다리지 않고 '지금 호출했다'고만 기록한다.

        좌석을 잡는 예약 호출처럼 지연이 곧 실패로 이어지는 경우에만 쓴다.
        다음 호출은 이 시점으로부터 다시 min_interval 만큼 벌어진다.
        """
        with self._lock:
            self._next_allowed = time.monotonic() + self._min_interval

    def call(self, fn, *args, **kwargs):
        """간격을 지킨 뒤 fn 을 호출한다."""
        self.wait()
        return fn(*args, **kwargs)


#: 코레일 API 전용 전역 게이트. 모든 코레일 호출이 이것을 거친다.
korail_api = RateLimiter(_configured_interval())
