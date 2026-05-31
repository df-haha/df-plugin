from __future__ import annotations

from datetime import date, datetime
from zoneinfo import ZoneInfo

_WD = ["mon", "tue", "wed", "thu", "fri", "sat", "sun"]


def now_tz(tz: str) -> datetime:
    return datetime.now(ZoneInfo(tz))


def today_tz(tz: str) -> date:
    return now_tz(tz).date()


def iso_week_str(d: date) -> str:
    c = d.isocalendar()
    return f"{c.year}-W{c.week:02d}"


def weekday_abbr(d: date) -> str:
    return _WD[d.weekday()]


def is_business_day(d: date, business_days: list[str]) -> bool:
    return weekday_abbr(d) in set(business_days)
