"""Lógica del horario de toque de queda (maneja el cruce de medianoche)."""
from __future__ import annotations

import datetime as dt


def parse_hhmm(s: str) -> dt.time:
    h, m = s.split(":")
    return dt.time(int(h), int(m))


def within_curfew(now: dt.datetime, start: dt.time, end: dt.time) -> bool:
    t = now.time()
    if start <= end:  # mismo día (ej. 08:00 a 18:00)
        return start <= t < end
    return t >= start or t < end  # cruza medianoche (ej. 22:00 a 04:00)


def seconds_until_curfew(now: dt.datetime, start: dt.time) -> float:
    today_start = now.replace(hour=start.hour, minute=start.minute, second=0, microsecond=0)
    if today_start <= now:
        today_start += dt.timedelta(days=1)
    return (today_start - now).total_seconds()
