"""WSL wrapper for fetch_daily_reports.ps1 — config 驅動（om-daily-cockpit）。

讀 oc-config（--config / OM_DAILY_COCKPIT_CONFIG），算出目標工作日，
用 config 內的 tenant 值呼叫 PowerShell fetcher，把其 JSON 輸出到 stdout。
零 tenant hard-code：所有部門特定值都來自 config。

CLI:
    python3 scripts/fetch_daily_reports.py --config <config.md>
    python3 scripts/fetch_daily_reports.py --config <config.md> --date 2026-04-21
    python3 scripts/fetch_daily_reports.py --config <config.md> --dry-run
"""

from __future__ import annotations

import argparse
import json
import subprocess
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from last_workday import get_last_workday  # noqa: E402
from oc_core.config import Config, ConfigError, load_from_cli  # noqa: E402

PS_SCRIPT = Path(__file__).resolve().parent / "fetch_daily_reports.ps1"


def wsl_to_win(path: Path) -> str:
    result = subprocess.run(
        ["wslpath", "-w", str(path)],
        capture_output=True,
        text=True,
        check=True,
    )
    return result.stdout.strip()


def build_ps_command(cfg: Config, target_date: str, archive_dir_win: str, ps_script_win: str) -> list[str]:
    """純函式：把 config + 已轉換的 Windows 路徑組成 PowerShell 指令。

    抽成純函式以便在無 PowerShell/wslpath 的環境單元測試 config→args 映射。
    """
    team_members_arg = ",".join(m.name for m in cfg.members)
    return [
        "powershell.exe", "-NoProfile", "-ExecutionPolicy", "Bypass",
        "-File", ps_script_win,
        "-TargetDate", target_date,
        "-OutlookAccount", cfg.email.account,
        "-FolderName", cfg.email.daily_report_folder,
        "-InboxName", cfg.email.inbox_name,
        "-TeamMembers", team_members_arg,
        "-ArchiveDir", archive_dir_win,
        "-Category", cfg.email.processed_category,
        "-AttachmentPattern", cfg.email.attachment_pattern,
        "-SubjectPattern", cfg.email.report_subject_pattern,
    ]


def run(cfg: Config, target_date: str, base_dir: Path, dry_run: bool = False) -> dict:
    archive_dir_abs = (base_dir / cfg.paths.archive_dir).resolve()
    archive_dir_abs.mkdir(parents=True, exist_ok=True)

    ps_script_win = wsl_to_win(PS_SCRIPT)
    archive_dir_win = wsl_to_win(archive_dir_abs)
    cmd = build_ps_command(cfg, target_date, archive_dir_win, ps_script_win)

    if dry_run:
        print("[dry-run] would execute:", file=sys.stderr)
        print(" ".join(f'"{c}"' for c in cmd), file=sys.stderr)
        return {
            "dry_run": True,
            "target_date": target_date,
            "cmd": cmd,
            "archive_dir": str(archive_dir_abs),
        }

    print(f"[fetch_daily_reports] target_date={target_date}", file=sys.stderr)
    print(f"[fetch_daily_reports] archive_dir={archive_dir_abs}", file=sys.stderr)

    proc = subprocess.run(
        cmd, capture_output=True, text=True, encoding="utf-8", errors="replace"
    )
    if proc.stderr:
        print(f"[powershell stderr]\n{proc.stderr}", file=sys.stderr)

    if proc.returncode != 0:
        return {
            "target_date": target_date,
            "results": [],
            "errors": [{
                "stage": "powershell_exit",
                "error": f"PowerShell exited with code {proc.returncode}",
                "stderr": proc.stderr,
            }],
        }

    try:
        return json.loads(proc.stdout) if proc.stdout.strip() else {}
    except json.JSONDecodeError as exc:
        return {
            "target_date": target_date,
            "results": [],
            "errors": [{
                "stage": "parse_json",
                "error": str(exc),
                "raw_stdout": proc.stdout[:2000],
            }],
        }


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--config", help="oc-config.md 路徑（或設 OM_DAILY_COCKPIT_CONFIG）")
    parser.add_argument("--date", help="目標日期 YYYY-MM-DD；預設＝台北時區上一個工作日")
    parser.add_argument("--base-dir", help="config 內相對路徑的解析基準（預設＝當前目錄）")
    parser.add_argument("--dry-run", action="store_true",
                        help="只印出將執行的 powershell 指令，不實際執行")
    args = parser.parse_args()

    try:
        cfg = load_from_cli(args.config)
    except ConfigError as e:
        print(f"[config 錯誤] {e}", file=sys.stderr)
        return 1

    base_dir = Path(args.base_dir).resolve() if args.base_dir else Path.cwd()
    target_date = args.date or get_last_workday().isoformat()
    output = run(cfg, target_date, base_dir, dry_run=args.dry_run)
    print(json.dumps(output, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
