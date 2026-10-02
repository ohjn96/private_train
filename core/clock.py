# -*- coding: utf-8 -*-
"""한국 시간(Asia/Seoul) 기준의 '지금'.

열차 시각·결제 기한은 모두 한국 시간이다. 폰이나 서버의 시간대가 달라도(해외 로밍,
UTC 로 도는 서버) 같은 답이 나오도록 시간 계산은 여기 것만 쓴다.

tzdata 가 없어 Asia/Seoul 을 못 읽으면(윈도우·모바일에서 tzdata 가 빠진 경우) 고정
UTC+9 로 대신한다. 한국은 서머타임이 없어서 결과가 같다.
"""
from __future__ import annotations

from datetime import datetime, timedelta, timezone

try:
    from zoneinfo import ZoneInfo

    KST = ZoneInfo("Asia/Seoul")
except Exception:  # noqa: BLE001 - ImportError, ZoneInfoNotFoundError(tzdata 없음) 등
    KST = timezone(timedelta(hours=9), "KST")


def now_kst() -> datetime:
    """지금 한국 시각 (tz 정보가 붙은 datetime)."""
    return datetime.now(KST)


def today_kst() -> str:
    """오늘 한국 날짜 YYYYMMDD."""
    return now_kst().strftime("%Y%m%d")


def train_departed(train: dict, now: datetime | None = None) -> bool:
    """이미 떠난 열차인가 (시각이 없으면 그 날짜가 지났을 때). 모르면 False.

    train 은 dep_date(YYYYMMDD) / dep_time(HHMMSS) 를 가진 dict.
    now 는 tz 가 붙어 있으면 한국 시각으로 바꿔 보고, 없으면 한국 시각으로 여긴다.
    """
    now = now or now_kst()
    if now.tzinfo is not None:
        now = now.astimezone(KST).replace(tzinfo=None)
    dep_date = str(train.get("dep_date") or "")
    dep_time = str(train.get("dep_time") or "")
    if not dep_date:
        return False
    try:
        if len(dep_time) >= 4:
            dep = datetime.strptime(dep_date + dep_time[:4], "%Y%m%d%H%M")
            return dep <= now
        return datetime.strptime(dep_date, "%Y%m%d").date() < now.date()
    except ValueError:
        return False


def all_departed(trains: list, now: datetime | None = None) -> bool:
    """고른 열차가 모두 이미 떠났나. 목록이 비어 있어도 True (더 기다릴 열차가 없다)."""
    now = now or now_kst()
    return not trains or all(train_departed(t, now) for t in trains)
