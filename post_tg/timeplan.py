from dataclasses import dataclass
from datetime import datetime, timedelta
from typing import Iterable, List
import os
from zoneinfo import ZoneInfo

def _tz():
    tzname = os.getenv("TZ", "Europe/Moscow")
    try:
        return ZoneInfo(tzname)
    except Exception:
        return ZoneInfo("UTC")

@dataclass
class Slot:
    when: datetime
    weekday: int
    seconds_from_start: int

def _next_weekday(base: datetime, wd_db: int) -> datetime:
    # БД: 0..6 => iso: 1..7
    target_iso = ((wd_db % 7) + 1)
    days_ahead = (target_iso - base.isoweekday()) % 7
    return base + timedelta(days=days_ahead)

def build_slots(now: datetime, items: Iterable[tuple[int, int]], horizon_days: int = 14) -> List[Slot]:
    """
    items: последовательность (weekday_db, seconds_from_start)
    возвращает все будущие слоты в пределах горизонта.
    """
    tz = now.tzinfo or _tz()
    now = now.astimezone(tz)
    end = now + timedelta(days=horizon_days)
    out: List[Slot] = []

    for wd, sec in items:
        d0 = _next_weekday(now, wd)
        hh, rem = divmod(int(sec), 3600)
        mm, ss = divmod(rem, 60)
        send_dt = datetime(d0.year, d0.month, d0.day, hh, mm, ss, tzinfo=tz)
        if send_dt < now:
            send_dt += timedelta(days=7)
        while send_dt <= end:
            out.append(Slot(when=send_dt, weekday=wd, seconds_from_start=sec))
            send_dt += timedelta(days=7)

    out.sort(key=lambda s: s.when)
    return out

