"""Scan a personal context directory and produce ctx-index.jsonl.

.. deprecated::
    Use the personal work profile (``profile``) command instead.
    This module is kept for backward compatibility but is no longer
    used by the setup flow.  Bundles should prefer the ``profile`` record.

Indexes .md, .txt, .docx files — filename + title + first-300-char summary.
Skips symlinks, junctions, hidden files, and files > 5 MB.
"""

from __future__ import annotations

import json
import sys
import zipfile
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any


_MAX_FILE_BYTES = 5 * 1024 * 1024
_SUMMARY_CHARS = 300
_SUPPORTED_EXTENSIONS = {".md", ".txt", ".docx"}
_DOCX_NS = "{http://schemas.openxmlformats.org/wordprocessingml/2006/main}"


def _is_hidden(p: Path) -> bool:
    """Check if any component of the path starts with a dot."""
    return any(part.startswith(".") for part in p.parts)


def _is_junction_or_symlink(p: Path) -> bool:
    """Detect symlinks and Windows junctions (NTFS reparse points)."""
    if p.is_symlink():
        return True
    # Python 3.12+ has Path.is_junction / os.path.isjunction
    if sys.version_info >= (3, 12):
        try:
            return p.is_junction()
        except (OSError, AttributeError):
            return False
    # Fallback: check lstat for reparse point attribute (Windows)
    try:
        import os
        st = os.lstat(p)
        # FILE_ATTRIBUTE_REPARSE_POINT = 0x400
        attrs = getattr(st, "st_file_attributes", 0)
        return bool(attrs & 0x400)
    except (OSError, AttributeError):
        return False


def _extract_title_md(text: str) -> str | None:
    """Extract the first ATX heading from Markdown text."""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped.startswith("#"):
            return stripped.lstrip("#").strip() or None
    return None


def _extract_title_txt(text: str) -> str | None:
    """Use the first non-empty line as title for plain text."""
    for line in text.split("\n"):
        stripped = line.strip()
        if stripped:
            return stripped[:120]
    return None


def _read_docx(path: Path) -> tuple[str | None, str]:
    """Extract title and body text from a .docx file.

    Uses stdlib zipfile + xml.etree — no external dependency.
    Falls back to filename-only if the file cannot be parsed.
    """
    try:
        with zipfile.ZipFile(path, "r") as zf:
            if "word/document.xml" not in zf.namelist():
                return None, ""
            with zf.open("word/document.xml") as f:
                tree = ET.parse(f)
    except (zipfile.BadZipFile, ET.ParseError, KeyError, OSError):
        return None, ""

    root = tree.getroot()
    paragraphs: list[str] = []
    title: str | None = None

    for para in root.iter(f"{_DOCX_NS}p"):
        texts: list[str] = []
        for run in para.iter(f"{_DOCX_NS}t"):
            if run.text:
                texts.append(run.text)
        line = "".join(texts).strip()
        if not line:
            continue
        # First paragraph with a heading style is the title
        if title is None:
            ppr = para.find(f"{_DOCX_NS}pPr")
            if ppr is not None:
                style = ppr.find(f"{_DOCX_NS}pStyle")
                if style is not None:
                    val = style.get(f"{_DOCX_NS}val", "")
                    if val.lower().startswith("heading") or val.lower().startswith("title"):
                        title = line
                        continue
        paragraphs.append(line)

    if title is None and paragraphs:
        title = paragraphs[0][:120]

    return title, "\n".join(paragraphs)


def scan_directory(ctx_dir: Path) -> list[dict[str, Any]]:
    """Scan *ctx_dir* and return a list of index entries.

    Each entry: ``{"path": <relative>, "title": ..., "summary": ..., "mtime": <iso>}``.
    """
    ctx_dir = Path(ctx_dir)
    if not ctx_dir.is_dir():
        return []

    entries: list[dict[str, Any]] = []

    for p in sorted(ctx_dir.rglob("*")):
        if not p.is_file():
            continue
        if p.suffix.lower() not in _SUPPORTED_EXTENSIONS:
            continue
        if _is_hidden(p.relative_to(ctx_dir)):
            continue
        if _is_junction_or_symlink(p):
            continue
        try:
            size = p.stat().st_size
        except OSError:
            continue
        if size > _MAX_FILE_BYTES or size == 0:
            continue

        title: str | None = None
        summary: str = ""
        rel = p.relative_to(ctx_dir)

        if p.suffix.lower() == ".docx":
            title, body = _read_docx(p)
            summary = body[:_SUMMARY_CHARS]
        else:
            try:
                text = p.read_text(encoding="utf-8", errors="replace")
            except OSError:
                continue
            if p.suffix.lower() == ".md":
                title = _extract_title_md(text)
            else:
                title = _extract_title_txt(text)
            summary = text[:_SUMMARY_CHARS]

        try:
            from datetime import datetime, timezone
            mtime = datetime.fromtimestamp(p.stat().st_mtime, tz=timezone.utc).isoformat()
        except OSError:
            mtime = ""

        entries.append({
            "path": str(rel),
            "title": title or p.stem,
            "summary": summary,
            "mtime": mtime,
        })

    return entries


def write_index(ctx_dir: Path, out_path: Path) -> int:
    """Scan *ctx_dir*, write ctx-index.jsonl to *out_path*, return entry count."""
    from .fsutil import atomic_write_text

    entries = scan_directory(ctx_dir)
    lines = [json.dumps(e, ensure_ascii=False) for e in entries]
    atomic_write_text(out_path, "\n".join(lines) + "\n" if lines else "")
    return len(entries)
