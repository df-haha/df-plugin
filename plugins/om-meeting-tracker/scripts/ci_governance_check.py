#!/usr/bin/env python3
"""ci_governance_check.py — CI 治理：PR 只能改 draft_dir / state / run-log。

擋：改正式追蹤檔（或任何 allowed 之外）、絕對路徑、含 '..'、symlink、submodule(.gitmodules)、gitlink(mode 160000)。
用法：python3 ci_governance_check.py --config <config.md> [--base-config <base.md>] --repo-root <dir> --changed-from <file|->
"""
from __future__ import annotations

import argparse
import subprocess
import sys
from pathlib import Path, PurePosixPath

sys.path.insert(0, str(Path(__file__).resolve().parent))
from mt_core.config import load_config


def _is_gitlink(repo_root: Path, p: str) -> bool:
    """Return True if path `p` is a gitlink (submodule, mode 160000) in the git index.

    Uses `git ls-files -s -- <path>` to inspect the index.  Returns False if git
    is unavailable, the repo has no index, or the path is not tracked.
    """
    try:
        result = subprocess.run(
            ["git", "-C", str(repo_root), "ls-files", "-s", "--", p],
            capture_output=True,
            text=True,
            timeout=10,
        )
        if result.returncode != 0 or not result.stdout.strip():
            return False
        # Output format: <mode> <object> <stage>\t<path>
        # gitlink mode is 160000
        first_line = result.stdout.splitlines()[0]
        mode = first_line.split()[0]
        return mode == "160000"
    except Exception:
        return False


def _allowed_prefixes(config) -> list[str]:
    def norm(d: str) -> str:
        d = d.strip()
        return d if d.endswith("/") else d + "/"
    state_dir = str(PurePosixPath(config.paths.state_file).parent)
    return [norm(config.paths.draft_dir), norm(state_dir), norm(config.paths.run_log_dir)]


def check_changed_paths(config, repo_root, changed: list[str], base_config=None) -> list[str]:
    repo_root = Path(repo_root)
    rr = repo_root.resolve()
    allowed = _allowed_prefixes(config)
    violations: list[str] = []
    for raw in changed:
        p = raw.strip()
        if not p:
            continue
        if p == ".gitmodules" or p.endswith("/.gitmodules"):
            violations.append(f"禁止 submodule 變更：{p}")
            continue
        if _is_gitlink(repo_root, p):
            violations.append(f"禁止 gitlink/submodule（mode 160000）：{p}")
            continue
        if p.startswith("/"):
            violations.append(f"禁止絕對路徑：{p}")
            continue
        if ".." in PurePosixPath(p).parts:
            violations.append(f"禁止含 '..' 的路徑：{p}")
            continue
        # FIX B: reject backslash paths (literal filename char on Linux CI)
        if "\\" in p:
            violations.append(f"禁止反斜線路徑：{p}")
            continue
        full = repo_root / p
        if full.is_symlink():
            violations.append(f"禁止 symlink：{p}")
            continue
        # FIX A: check for symlinked ancestor directories within repo_root
        _symlink_found = False
        for ancestor in [full] + list(full.parents):
            # only check ancestors that are at or below rr (inside the repo)
            try:
                ancestor.relative_to(rr)
            except ValueError:
                break  # we've walked above rr, stop
            if ancestor.is_symlink():
                violations.append(f"禁止 symlink（含上層目錄）：{p}")
                _symlink_found = True
                break
        if _symlink_found:
            continue
        # FIX A: containment check — resolved path must stay within repo_root
        if full.exists() and not str(full.resolve()).startswith(str(rr)):
            violations.append(f"路徑逃出 repo-root（疑 symlink/逃逸）：{p}")
            continue
        if not any(p.startswith(prefix) for prefix in allowed):
            violations.append(f"只能改 draft/state/run-log，越權路徑：{p}（allowed={allowed}）")
    return violations


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--config", required=True)
    ap.add_argument("--base-config", default=None,
                    help="PR 基準分支的受信任 config（用於防 config self-widening，可選）")
    ap.add_argument("--repo-root", default=".")
    ap.add_argument("--changed-from", required=True, help="檔案路徑或 '-' 讀 stdin（每行一個路徑）")
    args = ap.parse_args(argv)

    cfg = load_config(Path(args.config))
    base_cfg = load_config(Path(args.base_config)) if args.base_config else None
    text = sys.stdin.read() if args.changed_from == "-" else Path(args.changed_from).read_text(encoding="utf-8")
    changed = [ln for ln in text.splitlines() if ln.strip()]
    violations = check_changed_paths(cfg, args.repo_root, changed, base_config=base_cfg)
    if violations:
        print("[CI 治理] PR 變更違反治理規則：", file=sys.stderr)
        for v in violations:
            print(f"  - {v}", file=sys.stderr)
        return 1
    print(f"[CI 治理] OK：{len(changed)} 個變更皆在 draft/state/run-log 範圍內")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
