from __future__ import annotations
from datetime import date
from mt_core.timeutil import iso_week_str, is_business_day, weekday_abbr, today_tz

def test_iso_week_year_boundary():
    # 2021-01-01 屬 ISO 2020-W53（用 ISO year 而非 calendar year）
    assert iso_week_str(date(2021, 1, 1)) == "2020-W53"

def test_iso_week_format():
    s = iso_week_str(date(2026, 5, 28))
    assert s.startswith("2026-W") and len(s) == len("2026-W22")

def test_business_day():
    d = date(2026, 5, 28)
    abbr = weekday_abbr(d)
    assert is_business_day(d, [abbr]) is True
    assert is_business_day(d, [x for x in ["mon","tue","wed","thu","fri","sat","sun"] if x != abbr]) is False

def test_today_tz_returns_date():
    assert isinstance(today_tz("Asia/Taipei"), date)
