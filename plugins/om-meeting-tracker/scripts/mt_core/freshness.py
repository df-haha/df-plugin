from __future__ import annotations

import subprocess
from typing import Callable


def git_blob_sha(repo_root, rel_path) -> str:
    out = subprocess.run(["git", "-C", str(repo_root), "hash-object", str(rel_path)],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def git_committed_blob_sha(repo_root, rel_path) -> str:
    """Return the blob SHA that is actually committed in HEAD for rel_path.

    Uses `git rev-parse HEAD:<rel_path>` so the recorded SHA matches what is
    in the commit tree — not the potentially-dirty working-tree file.
    """
    out = subprocess.run(["git", "-C", str(repo_root), "rev-parse", f"HEAD:{rel_path}"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def git_head_sha(repo_root) -> str:
    out = subprocess.run(["git", "-C", str(repo_root), "rev-parse", "HEAD"],
                         capture_output=True, text=True, check=True)
    return out.stdout.strip()


def git_path_is_clean(repo_root, rel_path) -> bool:
    """True 若 rel_path 在 index 與 worktree 皆無未提交變更（git status --porcelain 空）。

    /done checkpoint 用：若追蹤檔有未 commit 的 review 變更就拒絕記 checkpoint，
    避免把「未提交」的人工 review 當成已落 HEAD（否則鮮度檢查會誤判 fresh、漏抓沒 push）。
    """
    r = subprocess.run(
        ["git", "-C", str(repo_root), "status", "--porcelain", "--", str(rel_path)],
        capture_output=True, text=True, check=False)
    return r.stdout.strip() == ""


def git_is_ancestor(repo_root) -> Callable[[str, str], bool]:
    def _f(maybe_ancestor: str, descendant: str) -> bool:
        r = subprocess.run(["git", "-C", str(repo_root), "merge-base", "--is-ancestor",
                            maybe_ancestor, descendant], capture_output=True)
        if r.returncode == 0:
            return True
        if r.returncode == 1:
            return False
        # returncode >= 2 (incl. 128) means bad object / git error
        sha = maybe_ancestor
        raise ValueError(f"freshness: 無法判斷 commit 祖先關係（bad object?）: {sha}")
    return _f


def check_freshness(state: dict, head_commit_sha: str,
                    is_ancestor_fn: Callable[[str, str], bool]) -> tuple[bool, str]:
    """鮮度：上次人工 review 的 commit 必須是雲端 HEAD 的祖先；否則疑似漏 push → stale。"""
    rec = state.get("last_human_reviewed") or {}
    rec_commit = rec.get("tracking_file_commit_sha")
    if not rec_commit:
        return True, "無 checkpoint（首次跑）"
    if not is_ancestor_fn(rec_commit, head_commit_sha):
        return False, "雲端 checkout 落後於上次人工 review（疑漏 push）——跳過、告警"
    return True, "fresh"
