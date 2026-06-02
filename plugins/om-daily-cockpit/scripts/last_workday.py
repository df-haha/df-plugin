"""Compute the most recent workday in Taiwan time.

Used by /hi and team-daily-fetcher skill to determine which date's
daily reports to fetch.

Rules:
1. Reference time: Taiwan time (Asia/Taipei, UTC+8).
2. Skip Saturday and Sunday.
3. Skip holidays listed in config/tw_holidays.json (if present).
4. "Last workday" = the most recent workday strictly before today.
   - Tuesday -> Monday
   - Monday -> Friday (skip Sat/Sun)
   - Monday after a long weekend -> last workday before the holiday

CLI:
    python3 scripts/last_workday.py                  # YYYY-MM-DD
    python3 scripts/last_workday.py --format slash   # YYYY/MM/DD
    python3 scripts/last_workday.py --ref 2026-04-22 # override today
"""

from __future__ import annotations

import argparse
import json
from datetime import date, datetime, timedelta, timezone
from pathlib import Path

TAIPEI_TZ = timezone(timedelta(hours=8))
PROJECT_ROOT = Path(__file__).resolve().parent.parent
HOLIDAYS_FILE = PROJECT_ROOT / "config" / "tw_holidays.json"


def _load_holidays() -> set[date]:
    if not HOLIDAYS_FILE.exists():
        return set()
    try:
        data = json.loads(HOLIDAYS_FILE.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return set()
    return {date.fromisoformat(d) for d in data.get("holidays", [])}


def get_last_workday(ref: date | None = None) -> date:
    """Return the most recent workday strictly before ``ref``.

    ``ref`` defaults to today in Taipei time.
    """
    if ref is None:
        ref = datetime.now(TAIPEI_TZ).date()
    holidays = _load_holidays()
    candidate = ref - timedelta(days=1)
    while candidate.weekday() >= 5 or candidate in holidays:
        candidate -= timedelta(days=1)
    return candidate


def main() -> None:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--format",
        choices=("dash", "slash"),
        default="dash",
        help="dash=YYYY-MM-DD (default), slash=YYYY/MM/DD",
    )
    parser.add_argument(
        "--ref",
        type=date.fromisoformat,
        default=None,
        help="Reference date (YYYY-MM-DD); defaults to today in Taipei",
    )
    args = parser.parse_args()

    d = get_last_workday(args.ref)
    print(d.strftime("%Y/%m/%d") if args.format == "slash" else d.isoformat())


if __name__ == "__main__":
    main()
