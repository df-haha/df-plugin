#!/usr/bin/env python3
"""compose_digest.py — 算今天該催的 owner，寄出每 owner 一封彙整信（idempotent）。

用法：
  python3 compose_digest.py --config <config.md> [--repo-root <dir>] [--today YYYY-MM-DD] [--dry-run]
send adapter 由 config.send.adapter 決定；憑證從 env 讀（MT_N8N_WEBHOOK_URL / MT_GMAIL_*）。
"""
from __future__ import annotations

import argparse
import json
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mt_core.config import load_config
from mt_core.tracking import parse_metrics
from mt_core.state_store import load_state_for, save_state_for
from mt_core.reminders import compute_reminders
from mt_core.timeutil import today_tz
from mt_core.run import send_digests


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--repo-root")
    ap.add_argument("--today")
    ap.add_argument("--dry-run", action="store_true")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config))
    repo_root = (Path(args.repo_root).resolve() if args.repo_root
                 else Path(args.config).resolve().parent.parent)
    tracking_path = (repo_root / cfg.paths.tracking_file).resolve()
    if not str(tracking_path).startswith(str(repo_root)):
        print(f"[error] tracking_file escapes repo-root: {tracking_path}", file=sys.stderr)
        return 2
    tracked = parse_metrics(tracking_path.read_text(encoding="utf-8")) if tracking_path.exists() else []
    state = load_state_for(cfg, repo_root)
    today = date.fromisoformat(args.today) if args.today else today_tz(cfg.timezone)

    if not args.dry_run:
        import subprocess
        from mt_core.freshness import git_head_sha, git_is_ancestor, check_freshness
        try:
            head = git_head_sha(repo_root)
            ok, msg = check_freshness(state, head, git_is_ancestor(repo_root))
            if not ok:
                print(json.dumps({"skipped_stale": True, "reason": msg}, ensure_ascii=False))
                return 0
        except (subprocess.CalledProcessError, FileNotFoundError):
            print("[warn] freshness check skipped: not a git repo or git error", file=sys.stderr)

    reminders = compute_reminders(cfg, tracked, today, state)
    adapter = None
    if not args.dry_run:
        from mt_core.send_adapters import get_adapter
        adapter = get_adapter(cfg.send)
    persist = (lambda: save_state_for(cfg, repo_root, state)) if not args.dry_run else None
    summary = send_digests(cfg, reminders, state, adapter, today,
                           dry_run=args.dry_run, persist=persist)
    if not args.dry_run:
        save_state_for(cfg, repo_root, state)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
