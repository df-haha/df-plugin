"""Tests for ctx_index.py — directory scanning and JSONL output."""

from __future__ import annotations

import json
import os
import zipfile
from pathlib import Path

import pytest

from daily_agenda.ctx_index import scan_directory, write_index


@pytest.fixture
def ctx_dir(tmp_path: Path) -> Path:
    d = tmp_path / "ctx"
    d.mkdir()
    return d


class TestScanDirectory:
    def test_empty_directory(self, ctx_dir: Path) -> None:
        assert scan_directory(ctx_dir) == []

    def test_nonexistent_directory(self, tmp_path: Path) -> None:
        assert scan_directory(tmp_path / "nonexistent") == []

    def test_md_file(self, ctx_dir: Path) -> None:
        f = ctx_dir / "notes.md"
        f.write_text("# My Notes\nSome content here.", encoding="utf-8")
        entries = scan_directory(ctx_dir)
        assert len(entries) == 1
        assert entries[0]["path"] == "notes.md"
        assert entries[0]["title"] == "My Notes"
        assert "Some content" in entries[0]["summary"]

    def test_txt_file(self, ctx_dir: Path) -> None:
        f = ctx_dir / "readme.txt"
        f.write_text("Important Info\nLine two.", encoding="utf-8")
        entries = scan_directory(ctx_dir)
        assert len(entries) == 1
        assert entries[0]["title"] == "Important Info"

    def test_docx_file(self, ctx_dir: Path) -> None:
        """Create a minimal .docx and verify extraction."""
        docx = ctx_dir / "report.docx"
        with zipfile.ZipFile(docx, "w") as zf:
            zf.writestr("word/document.xml", """<?xml version="1.0" encoding="UTF-8"?>
<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">
  <w:body>
    <w:p><w:pPr><w:pStyle w:val="Heading1"/></w:pPr>
      <w:r><w:t>Report Title</w:t></w:r>
    </w:p>
    <w:p><w:r><w:t>Paragraph one content.</w:t></w:r></w:p>
  </w:body>
</w:document>""")
        entries = scan_directory(ctx_dir)
        assert len(entries) == 1
        assert entries[0]["title"] == "Report Title"
        assert "Paragraph one" in entries[0]["summary"]

    def test_skips_hidden_files(self, ctx_dir: Path) -> None:
        (ctx_dir / ".hidden.md").write_text("# Secret", encoding="utf-8")
        assert scan_directory(ctx_dir) == []

    def test_skips_symlinks(self, ctx_dir: Path) -> None:
        real = ctx_dir / "real.md"
        real.write_text("# Real", encoding="utf-8")
        link = ctx_dir / "link.md"
        link.symlink_to(real)
        entries = scan_directory(ctx_dir)
        # Only the real file, not the symlink
        assert len(entries) == 1
        assert entries[0]["path"] == "real.md"

    def test_skips_large_files(self, ctx_dir: Path) -> None:
        big = ctx_dir / "huge.txt"
        big.write_bytes(b"x" * (5 * 1024 * 1024 + 1))
        assert scan_directory(ctx_dir) == []

    def test_skips_unsupported_extensions(self, ctx_dir: Path) -> None:
        (ctx_dir / "image.png").write_bytes(b"\x89PNG")
        assert scan_directory(ctx_dir) == []

    def test_subdirectory(self, ctx_dir: Path) -> None:
        sub = ctx_dir / "subdir"
        sub.mkdir()
        (sub / "deep.md").write_text("# Deep\nContent.", encoding="utf-8")
        entries = scan_directory(ctx_dir)
        assert len(entries) == 1
        assert entries[0]["path"] == os.path.join("subdir", "deep.md")

    def test_summary_truncation(self, ctx_dir: Path) -> None:
        (ctx_dir / "long.txt").write_text("A" * 500, encoding="utf-8")
        entries = scan_directory(ctx_dir)
        assert len(entries[0]["summary"]) == 300


class TestWriteIndex:
    def test_writes_jsonl(self, ctx_dir: Path, tmp_path: Path) -> None:
        (ctx_dir / "a.md").write_text("# Title A\nBody.", encoding="utf-8")
        (ctx_dir / "b.txt").write_text("Title B\nMore.", encoding="utf-8")
        out = tmp_path / "ctx-index.jsonl"
        count = write_index(ctx_dir, out)
        assert count == 2
        lines = out.read_text(encoding="utf-8").strip().split("\n")
        assert len(lines) == 2
        for line in lines:
            parsed = json.loads(line)
            assert "path" in parsed
            assert "title" in parsed
            assert "summary" in parsed
            assert "mtime" in parsed
