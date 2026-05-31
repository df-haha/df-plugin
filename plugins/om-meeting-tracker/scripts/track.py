#!/usr/bin/env python3
"""track.py — 本機 /track orchestrator。

【M2.7 版本（本 task）】：
  --dry-run：算今天該催的 owner + 預覽 digest 主旨 + 預覽 draft（reports=[]），不寄信、不讀 Gmail。
  非 --dry-run：僅「算提醒 + 寫一份空 reports 的 draft」（此時 send/讀信元件尚未存在）。
【M5.3 會把非 dry-run 升級為真 orchestrator】：串 compose_digest（寄）→（skill dump Gmail）→
  collect_replies（讀+草擬）→ rolling_pr（開 PR）。在那之前 docstring 不誇稱會寄信/讀信。
"""
from __future__ import annotations

import argparse
import sys
from datetime import date
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mt_core.config import load_config
from mt_core.tracking import parse_metrics
from mt_core.state import load_state   # M2.8 會 rewire 成 state_store.load_state_for
from mt_core.reminders import compute_reminders
from mt_core.digest import compose_digest
from mt_core.draft import render_draft
from mt_core.timeutil import today_tz, iso_week_str


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description="meeting-tracker 本機 orchestrator")
    ap.add_argument("--config", required=True)
    ap.add_argument("--repo-root", help="tenant repo 根（預設 = config 的 parent.parent）")
    ap.add_argument("--dry-run", action="store_true")
    ap.add_argument("--today", help="覆寫今天 YYYY-MM-DD（測試用）")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config))
    repo_root = (Path(args.repo_root).resolve() if args.repo_root
                 else Path(args.config).resolve().parent.parent)
    tracking_path = repo_root / cfg.paths.tracking_file
    tracked = parse_metrics(tracking_path.read_text(encoding="utf-8")) if tracking_path.exists() else []
    state = load_state(repo_root / cfg.paths.state_file, cfg.tenant_id)  # M2.8 rewire → load_state_for(cfg, repo_root)

    today = date.fromisoformat(args.today) if args.today else today_tz(cfg.timezone)
    week = iso_week_str(today)

    reminders = compute_reminders(cfg, tracked, today, state)
    print(f"[track] today={today} week={week} 該催 owner 數={len(reminders)}")
    for r in reminders:
        mids = ",".join(m.metric_id for m, _ in r.metrics)
        print(f"  - {r.owner.owner_id} ({r.owner.email}): {mids}")
        if args.dry_run:
            d = compose_digest(r, cfg.tenant_id, week)
            print(f"    digest subject: {d.subject}")

    draft = render_draft(cfg, tracked, week, reports=[])
    if args.dry_run:
        print("---- 準會議版 draft preview ----")
        print(draft)
    else:
        draft_path = repo_root / cfg.paths.draft_dir / f"meeting_draft_week_{week}.md"
        draft_path.parent.mkdir(parents=True, exist_ok=True)
        draft_path.write_text(draft, encoding="utf-8")
        print(f"[track] draft 寫入 {draft_path}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
