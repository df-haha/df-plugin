#!/usr/bin/env python3
"""collect_replies.py — 讀 Gmail 回信 JSON → 歸因 → 更新本週 rolling draft。

inbox JSON：list of {msg_id, thread_id, sender, subject, body_text}（由 skill 用 Gmail connector dump）。
回信一律當 untrusted（見 mt_core.replies）。
"""
from __future__ import annotations

import argparse
import json
import sys
from collections import defaultdict
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
    tracking_path = repo_root / cfg.paths.tracking_file
    tracked = parse_metrics(tracking_path.read_text(encoding="utf-8")) if tracking_path.exists() else []
    state = load_state_for(cfg, repo_root)
    today = date.fromisoformat(args.today) if args.today else today_tz(cfg.timezone)
    week = iso_week_str(today)

    raw = json.loads(Path(args.inbox_json).read_text(encoding="utf-8"))
    msgs = [GmailMsg(m["msg_id"], m["thread_id"], m["sender"], m["subject"], m["body_text"])
            for m in raw]
    all_attrs, summary = collect_replies(cfg, msgs, state)

    # 依 token 週分組，各週 draft 各自 regenerate（late reply 回到原 token 週，不誤記本週）。
    # 本週 draft 一定產生（即使零回報→列待回填）。
    by_week: dict[str, list] = defaultdict(list)
    for a in all_attrs:
        if a.week:
            by_week[a.week].append(a)
    by_week.setdefault(week, [])
    written = []
    for wk, reps in by_week.items():
        draft = render_draft(cfg, tracked, wk, reports=reps)
        dp = repo_root / cfg.paths.draft_dir / f"meeting_draft_week_{wk}.md"
        dp.parent.mkdir(parents=True, exist_ok=True)
        dp.write_text(draft, encoding="utf-8")
        written.append(str(dp.relative_to(repo_root)))

    save_state_for(cfg, repo_root, state)
    summary["drafts_written"] = written
    print(json.dumps(summary, ensure_ascii=False))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
