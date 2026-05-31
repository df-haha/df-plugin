#!/usr/bin/env python3
"""collect_replies.py — 讀 Gmail 回信 JSON → 歸因 → 更新本週 rolling draft。

inbox JSON：list of {msg_id, thread_id, sender, subject, body_text}（由 skill 用 Gmail connector dump）。
回信一律當 untrusted（見 mt_core.replies）。
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
from mt_core.replies import GmailMsg
from mt_core.run import collect_replies
from mt_core.draft import render_draft
from mt_core.timeutil import today_tz, iso_week_str


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--repo-root")
    ap.add_argument("--inbox-json", required=True)
    ap.add_argument("--today")
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
    week = iso_week_str(today)

    raw = json.loads(Path(args.inbox_json).read_text(encoding="utf-8"))
    msgs = [GmailMsg(m["msg_id"], m["thread_id"], m["sender"], m["subject"], m["body_text"])
            for m in raw]
    # current_week 透傳：無 token + 無歷史的可信回信歸到本週（Codex WF2 #1），不致 week="" 被丟。
    all_attrs, summary = collect_replies(cfg, msgs, state, current_week=week)

    # FIX 1: only write the CURRENT week's draft.
    # Late replies (token week != current week) are surfaced in the summary but do NOT
    # clobber past-week drafts that the human may have already reviewed.
    current_reps = [a for a in all_attrs if a.week == week]
    late_reps = [a for a in all_attrs if a.week and a.week != week]

    draft = render_draft(cfg, tracked, week, reports=current_reps)
    dp = repo_root / cfg.paths.draft_dir / f"meeting_draft_week_{week}.md"
    dp.parent.mkdir(parents=True, exist_ok=True)
    dp.write_text(draft, encoding="utf-8")

    save_state_for(cfg, repo_root, state)
    summary["drafts_written"] = [str(dp.relative_to(repo_root))]
    summary["late_replies_recorded"] = len(late_reps)
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
