#!/usr/bin/env python3
"""在 repo 建立裁決帳本：索引、詳情目錄、CLAUDE.md 三行接點。冪等。"""
from __future__ import annotations

import argparse
import json
import shutil
import sys
from datetime import date
from pathlib import Path

HERE = Path(__file__).resolve().parent
TEMPLATES = HERE.parent / "templates"
INDEX_REL = Path("docs") / "裁決帳本.md"
DETAIL_DIR_REL = Path("docs") / "裁決"
BLOCK_MARK = "## 立場來源"


def _detect_newline(text: str) -> str:
    """偵測檔案主要換行 style：看第一個 \\r\\n 或 \\n；沒有換行就當 \\n。"""
    for i, ch in enumerate(text):
        if ch == "\n":
            return "\r\n" if i > 0 and text[i - 1] == "\r" else "\n"
    return "\n"


def _insert_block(claude_md: Path, block: str) -> None:
    # newline="" 關掉 universal-newline 轉譯，讀寫都保留原始位元組，不靜默正規化既有內容的換行。
    with open(claude_md, "r", encoding="utf-8", newline="") as f:
        text = f.read()
    nl = _detect_newline(text)
    lines = text.splitlines(keepends=True)
    # 插在第一個 H1 之後的第一個空行後；沒有 H1 就放最前
    insert_at = 0
    for i, line in enumerate(lines):
        if line.startswith("# "):
            insert_at = i + 1
            while insert_at < len(lines) and lines[insert_at].strip():
                insert_at += 1
            insert_at = min(insert_at + 1, len(lines))
            break
    # 插入的區塊本身換行改用原檔的 style，其餘既有內容原封不動
    block_body = block.replace("\r\n", "\n").rstrip("\n")
    if nl == "\r\n":
        block_body = block_body.replace("\n", "\r\n")
    block_text = block_body + nl + nl
    new = lines[:insert_at] + [block_text] + lines[insert_at:]
    with open(claude_md, "w", encoding="utf-8", newline="") as f:
        f.write("".join(new))


def _next_backup_path(claude_md: Path) -> Path:
    """同日備份碰撞就往後加 -2、-3……絕不覆蓋既有備份。"""
    base_name = f"CLAUDE.md.bak-{date.today():%Y%m%d}"
    candidate = claude_md.with_name(base_name)
    n = 2
    while candidate.exists():
        candidate = claude_md.with_name(f"{base_name}-{n}")
        n += 1
    return candidate


def init(repo_root: Path, dry_run: bool = False) -> dict:
    repo_root = Path(repo_root).resolve()
    result = {"index_created": False, "detail_dir_created": False,
              "claude_md": "missing", "backup": None}
    index_path = repo_root / INDEX_REL
    detail_dir = repo_root / DETAIL_DIR_REL
    claude_md = repo_root / "CLAUDE.md"

    if not index_path.exists():
        result["index_created"] = True
        if not dry_run:
            index_path.parent.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(TEMPLATES / "裁決帳本.md", index_path)
    if not detail_dir.exists():
        result["detail_dir_created"] = True
        if not dry_run:
            detail_dir.mkdir(parents=True, exist_ok=True)
    if claude_md.exists():
        if BLOCK_MARK in claude_md.read_text(encoding="utf-8"):
            result["claude_md"] = "already"
        else:
            result["claude_md"] = "inserted"
            if not dry_run:
                backup = _next_backup_path(claude_md)
                shutil.copyfile(claude_md, backup)
                result["backup"] = str(backup)
                _insert_block(claude_md, (TEMPLATES / "claude-md-block.md").read_text(encoding="utf-8"))
    return result


def main(argv: list = None) -> int:
    sys.stdout.reconfigure(encoding="utf-8")
    ap = argparse.ArgumentParser()
    ap.add_argument("repo_root")
    ap.add_argument("--dry-run", action="store_true")
    a = ap.parse_args(argv)
    print(json.dumps(init(Path(a.repo_root), a.dry_run), ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    sys.exit(main())
