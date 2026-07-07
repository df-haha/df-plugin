"""missing_report_check.py — 團隊成員連續缺報偵測（含休假豁免）。

背景：team-daily-fetcher 每天把成員日報歸檔到
`{archive_root}/{date}/{name}_daily_work_log_{date}.md`。本腳本提供機械規則，
從最近工作日往回數每位成員的「連續缺報工作日數」，並用 config 的 `on_leave_until`
把「休假中」與「失聯」分開——休假日不計缺報。輸出 JSON 供 cockpit skill 消費，
對達到門檻的成員升級為 P1 並自動生催辦提案。

工作日邏輯沿用 last_workday.py（週末/holidays 跳過），department-agnostic、零 hard-code。

CLI:
    python3 scripts/missing_report_check.py --config <cfg>
    python3 scripts/missing_report_check.py --config <cfg> --as-of 2026-07-10 --threshold 2
    python3 scripts/missing_report_check.py --config <cfg> --archive-root data/daily_reports
"""

from __future__ import annotations

import argparse
import json
import sys
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(_SCRIPTS_DIR))

from last_workday import get_last_workday  # noqa: E402
from oc_core.config import Config, Member, load_from_cli  # noqa: E402

WINDOW = 5  # 最多往回檢視的工作日數


def recent_workdays(as_of: date, count: int, tz=None) -> list[date]:
    """as_of 之前最近的 count 個工作日，最新在前（strictly before as_of）。"""
    days: list[date] = []
    ref = as_of
    for _ in range(count):
        wd = get_last_workday(ref, tz)
        days.append(wd)
        ref = wd
    return days


def _report_path(archive_root: Path, name: str, d: date) -> Path:
    ds = d.isoformat()
    return archive_root / ds / f"{name}_daily_work_log_{ds}.md"


def check_member(
    member: Member,
    archive_root: Path,
    workdays: list[date],
    as_of: date,
    threshold: int,
) -> dict[str, object]:
    """從最近工作日往回數連續缺報；休假日透明跳過（不計缺報也不中斷）。"""
    missing_streak = 0
    missing_dates: list[str] = []
    for d in workdays:  # 最新在前
        if member.is_on_leave(d):
            continue
        if _report_path(archive_root, member.name, d).is_file():
            break
        missing_streak += 1
        missing_dates.append(d.isoformat())
    on_leave = member.is_on_leave(as_of)
    escalate = missing_streak >= threshold and not on_leave
    return {
        "member_id": member.member_id,
        "name": member.name,
        "missing_streak": missing_streak,
        "on_leave": on_leave,
        "on_leave_until": member.on_leave_until,
        "escalate": escalate,
        "missing_dates": missing_dates,
    }


def check_all(
    cfg: Config,
    archive_root: Path,
    as_of: date,
    threshold: int,
    tz=None,
) -> dict[str, object]:
    workdays = recent_workdays(as_of, WINDOW, tz)
    members = [
        check_member(m, archive_root, workdays, as_of, threshold)
        for m in cfg.members
    ]
    return {
        "as_of": as_of.isoformat(),
        "threshold": threshold,
        "members": members,
    }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", required=True, help="oc-config.md 路徑")
    parser.add_argument(
        "--archive-root", default=None,
        help="歸檔根目錄（相對 cwd）；預設用 config 的 paths.archive_dir",
    )
    parser.add_argument(
        "--as-of", type=date.fromisoformat, default=None,
        help="檢查基準日 YYYY-MM-DD；預設今天（config 時區）",
    )
    parser.add_argument(
        "--threshold", type=int, default=2,
        help="連續缺報工作日數達此值即升級（預設 2）",
    )
    args = parser.parse_args()

    if args.threshold < 1:
        parser.error("--threshold 必須 >= 1")

    cfg = load_from_cli(args.config)
    tz = ZoneInfo(cfg.timezone)
    as_of = args.as_of or datetime.now(tz).date()
    archive_root = (
        Path(args.archive_root) if args.archive_root else Path(cfg.paths.archive_dir)
    )

    result = check_all(cfg, archive_root, as_of, args.threshold, tz)
    print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
