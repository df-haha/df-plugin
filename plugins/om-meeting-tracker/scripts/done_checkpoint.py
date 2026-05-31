#!/usr/bin/env python3
"""done_checkpoint.py — /done：主管本機把 draft 併入正式追蹤檔後，記錄 last_human_reviewed checkpoint。

記錄 tracking_file 的 git blob SHA + 當前 commit SHA + 時戳，供下次 Routine 鮮度檢查比對。
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from mt_core.config import load_config
from mt_core.state import set_human_reviewed
from mt_core.state_store import load_state_for, save_state_for
from mt_core.freshness import git_committed_blob_sha, git_head_sha, git_path_is_clean
from mt_core.timeutil import now_tz


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--repo-root")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config))
    repo_root = (Path(args.repo_root).resolve() if args.repo_root
                 else Path(args.config).resolve().parent.parent)
    # clean-tree gate（codex WF3 #2）：追蹤檔有未 commit 變更就拒絕 checkpoint，
    # 避免把未提交的 review 當成已落 HEAD（否則鮮度誤判 fresh、漏抓沒 push）。
    if not git_path_is_clean(repo_root, cfg.paths.tracking_file):
        print(f"[/done] 追蹤檔有未 commit 變更：{cfg.paths.tracking_file}　"
              "請先 git commit 再跑 /done（避免 checkpoint 記到未提交的 review）",
              file=sys.stderr)
        return 2
    state = load_state_for(cfg, repo_root)
    blob = git_committed_blob_sha(repo_root, cfg.paths.tracking_file)
    head = git_head_sha(repo_root)
    set_human_reviewed(state, blob, head, now_tz(cfg.timezone).isoformat())
    save_state_for(cfg, repo_root, state)
    print(f"[/done] checkpoint：blob={blob[:10]} head={head[:10]}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
