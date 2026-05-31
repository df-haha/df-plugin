#!/usr/bin/env python3
"""ci_governance_check.py — CI 治理：PR 只能改 draft_dir / state / run-log。

擋：改正式追蹤檔（或任何 allowed 之外）、絕對路徑、含 '..'、symlink、submodule(.gitmodules)。
用法：python3 ci_governance_check.py --config <config.md> --repo-root <dir> --changed-from <file|->
"""
from __future__ import annotations

import argparse
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mt_core.config import load_config


def _allowed_prefixes(config) -> list[str]:
    def norm(d: str) -> str:
        d = d.strip()
        return d if d.endswith("/") else d + "/"
    state_dir = str(PurePosixPath(config.paths.state_file).parent)
    return [norm(config.paths.draft_dir), norm(state_dir), norm(config.paths.run_log_dir)]


def check_changed_paths(config, repo_root, changed: list[str]) -> list[str]:
    repo_root = Path(repo_root)
    allowed = _allowed_prefixes(config)
    violations: list[str] = []
    for raw in changed:
        p = raw.strip()
        if not p:
            continue
        if p == ".gitmodules" or p.endswith("/.gitmodules"):
            violations.append(f"禁止 submodule 變更：{p}")
            continue
        if p.startswith("/"):
            violations.append(f"禁止絕對路徑：{p}")
            continue
        if ".." in PurePosixPath(p).parts:
            violations.append(f"禁止含 '..' 的路徑：{p}")
            continue
        full = repo_root / p
        if full.is_symlink():
            violations.append(f"禁止 symlink：{p}")
            continue
        if not any(p.startswith(prefix) for prefix in allowed):
            violations.append(f"只能改 draft/state/run-log，越權路徑：{p}（allowed={allowed}）")
    return violations


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--changed-from", required=True, help="檔案路徑或 '-' 讀 stdin（每行一個路徑）")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config))
    text = sys.stdin.read() if args.changed_from == "-" else Path(args.changed_from).read_text(encoding="utf-8")
    changed = [ln for ln in text.splitlines() if ln.strip()]
    violations = check_changed_paths(cfg, args.repo_root, changed)
    if violations:
        print("[CI 治理] PR 變更違反治理規則：", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1
    print(f"[CI 治理] OK：{len(changed)} 個變更皆在 draft/state/run-log 範圍內")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
