"""Tests for the docs convert module."""

from __future__ import annotations

import zipfile
from pathlib import Path
from typing import Any

import pytest

from daily_agenda.docs_convert import (
    convert_paths, SUPPORTED_EXTENSIONS, MAX_FILE_BYTES,
    _check_ooxml_safe, _is_windows_reserved_stem,
    OOXML_MAX_UNCOMPRESSED, OOXML_MAX_MEMBER_SIZE,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------


def _make_txt(tmp_path: Path, name: str = "notes.txt", content: str = "Hello world") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _make_md(tmp_path: Path, name: str = "readme.md", content: str = "# Title\nBody") -> Path:
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / name
    p.write_text(content, encoding="utf-8")
    return p


def _make_docx(tmp_path: Path, name: str = "doc.docx") -> Path:
    """Create a minimal .docx file (zip with required XML)."""
    tmp_path.mkdir(parents=True, exist_ok=True)
    p = tmp_path / name
    with zipfile.ZipFile(p, "w") as zf:
        zf.writestr("[Content_Types].xml", '<?xml version="1.0"?>'
                     '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types">'
                     '<Default Extension="rels" ContentType="application/vnd.openxmlformats-package.relationships+xml"/>'
                     '<Default Extension="xml" ContentType="application/xml"/>'
                     '<Override PartName="/word/document.xml" '
                     'ContentType="application/vnd.openxmlformats-officedocument.wordprocessingml.document.main+xml"/>'
                     '</Types>')
        zf.writestr("_rels/.rels", '<?xml version="1.0"?>'
                     '<Relationships xmlns="http://schemas.openxmlformats.org/package/2006/relationships">'
                     '<Relationship Id="rId1" Type="http://schemas.openxmlformats.org/officeDocument/2006/relationships/officeDocument" Target="word/document.xml"/>'
                     '</Relationships>')
        zf.writestr("word/document.xml", '<?xml version="1.0"?>'
                     '<w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main">'
                     '<w:body><w:p><w:r><w:t>Test content</w:t></w:r></w:p></w:body>'
                     '</w:document>')
    return p


# ---------------------------------------------------------------------------
# Tests
# ---------------------------------------------------------------------------


class TestConvertPaths:
    def test_txt_file(self, tmp_path: Path) -> None:
        src = _make_txt(tmp_path / "src")
        out = tmp_path / "out"
        result = convert_paths([src], out)
        assert result["ok"] is True
        assert len(result["files"]) == 1
        assert result["files"][0]["original_name"] == "notes.txt"
        assert result["files"][0]["chars"] > 0
        assert result["skipped_count"] == 0

    def test_md_file(self, tmp_path: Path) -> None:
        src = _make_md(tmp_path / "src")
        out = tmp_path / "out"
        result = convert_paths([src], out)
        assert len(result["files"]) == 1
        assert "Title" in Path(result["files"][0]["path"]).read_text(encoding="utf-8")

    def test_docx_file(self, tmp_path: Path) -> None:
        src = _make_docx(tmp_path / "src")
        out = tmp_path / "out"
        result = convert_paths([src], out)
        assert len(result["files"]) == 1
        content = Path(result["files"][0]["path"]).read_text(encoding="utf-8")
        assert "Test content" in content

    def test_unsupported_skipped(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        (src_dir / "image.png").write_bytes(b"\x89PNG\r\n")
        out = tmp_path / "out"
        result = convert_paths([src_dir / "image.png"], out)
        assert len(result["files"]) == 0
        assert result["skipped_count"] == 1
        assert result["skipped"][0]["reason"] == "unsupported_type"

    def test_directory_scan(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        _make_txt(src_dir, "a.txt", "File A")
        _make_txt(src_dir, "b.txt", "File B")
        (src_dir / "skip.jpg").write_bytes(b"\xff\xd8")
        out = tmp_path / "out"
        result = convert_paths([src_dir], out)
        assert len(result["files"]) == 2
        assert result["skipped_count"] == 1

    def test_total_chars_cap(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        # Create files that exceed the cap
        big_text = "x" * 300_000
        _make_txt(src_dir, "big1.txt", big_text)
        _make_txt(src_dir, "big2.txt", big_text)
        out = tmp_path / "out"
        result = convert_paths([src_dir], out)
        total = sum(f["chars"] for f in result["files"])
        assert total <= 500_000

    def test_file_too_large(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        big = src_dir / "huge.txt"
        big.write_bytes(b"x" * (MAX_FILE_BYTES + 1))
        out = tmp_path / "out"
        result = convert_paths([big], out)
        assert len(result["files"]) == 0
        assert result["skipped_count"] == 1
        assert result["skipped"][0]["reason"] == "too_large"

    def test_path_with_spaces_and_chinese(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "我的 文件"
        src_dir.mkdir()
        _make_txt(src_dir, "筆記 一.txt", "中文內容")
        out = tmp_path / "out"
        result = convert_paths([src_dir], out)
        assert len(result["files"]) == 1
        content = Path(result["files"][0]["path"]).read_text(encoding="utf-8")
        assert "中文內容" in content

    def test_hidden_files_skipped(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        hidden = src_dir / ".hidden"
        hidden.mkdir()
        (hidden / "secret.txt").write_text("secret", encoding="utf-8")
        _make_txt(src_dir, "visible.txt", "visible")
        out = tmp_path / "out"
        result = convert_paths([src_dir], out)
        assert len(result["files"]) == 1
        assert result["files"][0]["original_name"] == "visible.txt"

    def test_multiple_paths(self, tmp_path: Path) -> None:
        f1 = _make_txt(tmp_path / "a", "one.txt", "one")
        f2 = _make_txt(tmp_path / "b", "two.txt", "two")
        out = tmp_path / "out"
        result = convert_paths([f1, f2], out)
        assert len(result["files"]) == 2

    def test_pptx_file(self, tmp_path: Path) -> None:
        """Convert a minimal .pptx file generated with python-pptx."""
        from pptx import Presentation  # type: ignore[import-untyped]
        from pptx.util import Inches  # type: ignore[import-untyped]

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        pptx_path = src_dir / "slides.pptx"
        prs = Presentation()
        slide = prs.slides.add_slide(prs.slide_layouts[0])
        slide.shapes.title.text = "Test Slide Title"
        slide.placeholders[1].text = "Slide body content"
        prs.save(str(pptx_path))

        out = tmp_path / "out"
        result = convert_paths([pptx_path], out)
        assert len(result["files"]) == 1
        content = Path(result["files"][0]["path"]).read_text(encoding="utf-8")
        assert "Test Slide Title" in content

    def test_xlsx_file(self, tmp_path: Path) -> None:
        """Convert a minimal .xlsx file generated with openpyxl."""
        from openpyxl import Workbook  # type: ignore[import-untyped]

        src_dir = tmp_path / "src"
        src_dir.mkdir()
        xlsx_path = src_dir / "data.xlsx"
        wb = Workbook()
        ws = wb.active
        ws["A1"] = "Column A"
        ws["B1"] = "Column B"
        ws["A2"] = 42
        ws["B2"] = "test value"
        wb.save(str(xlsx_path))

        out = tmp_path / "out"
        result = convert_paths([xlsx_path], out)
        assert len(result["files"]) == 1
        content = Path(result["files"][0]["path"]).read_text(encoding="utf-8")
        assert "Column A" in content or "42" in content

    def test_pdf_file(self, tmp_path: Path) -> None:
        """Convert a minimal valid PDF file."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        pdf_path = src_dir / "doc.pdf"
        # Minimal valid PDF with one page containing text
        pdf_bytes = (
            b"%PDF-1.4\n"
            b"1 0 obj<</Type/Catalog/Pages 2 0 R>>endobj\n"
            b"2 0 obj<</Type/Pages/Kids[3 0 R]/Count 1>>endobj\n"
            b"3 0 obj<</Type/Page/Parent 2 0 R/MediaBox[0 0 612 792]"
            b"/Contents 4 0 R/Resources<</Font<</F1 5 0 R>>>>>>endobj\n"
            b"4 0 obj<</Length 44>>stream\n"
            b"BT /F1 12 Tf 100 700 Td (Hello PDF) Tj ET\n"
            b"endstream endobj\n"
            b"5 0 obj<</Type/Font/Subtype/Type1/BaseFont/Helvetica>>endobj\n"
            b"xref\n0 6\n"
            b"0000000000 65535 f \n"
            b"0000000009 00000 n \n"
            b"0000000058 00000 n \n"
            b"0000000115 00000 n \n"
            b"0000000266 00000 n \n"
            b"0000000360 00000 n \n"
            b"trailer<</Size 6/Root 1 0 R>>\n"
            b"startxref\n431\n%%EOF"
        )
        pdf_path.write_bytes(pdf_bytes)

        out = tmp_path / "out"
        result = convert_paths([pdf_path], out)
        assert len(result["files"]) == 1
        content = Path(result["files"][0]["path"]).read_text(encoding="utf-8")
        # PDF extraction may or may not get the exact text depending on pdfminer
        assert result["files"][0]["chars"] > 0

    def test_duplicate_names_deduplicated(self, tmp_path: Path) -> None:
        d1 = tmp_path / "a"
        d2 = tmp_path / "b"
        d1.mkdir()
        d2.mkdir()
        (d1 / "notes.txt").write_text("from a", encoding="utf-8")
        (d2 / "notes.txt").write_text("from b", encoding="utf-8")
        out = tmp_path / "out"
        result = convert_paths([d1 / "notes.txt", d2 / "notes.txt"], out)
        assert len(result["files"]) == 2
        paths = {f["path"] for f in result["files"]}
        assert len(paths) == 2  # different output paths

    def test_ooxml_bomb_uncompressed_too_large(self, tmp_path: Path) -> None:
        """A .docx with huge uncompressed XML is skipped, not parsed."""
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        bomb = src_dir / "bomb.docx"
        # Build a docx where word/document.xml has a very large file_size
        # We can't actually store 100MB+ in a test, but we can create a zip
        # with repetitive XML that compresses well but declares a large
        # uncompressed size. Use a real big uncompressed content:
        # 60 MB of repetitive XML paragraphs compresses to ~100 KB
        big_xml = '<?xml version="1.0"?><w:document xmlns:w="http://schemas.openxmlformats.org/wordprocessingml/2006/main"><w:body>'
        para = "<w:p><w:r><w:t>Repeated text for bomb test paragraph.</w:t></w:r></w:p>"
        # Each para is ~75 bytes; need > 100 MB total uncompressed
        # 100 MB / 75 bytes ≈ 1.4M paras → too slow. Instead, use fewer but
        # larger members to trip the member-size or total-size check.
        # Strategy: create one member that's large enough to trip OOXML_MAX_MEMBER_SIZE (50 MB).
        # A 51 MB string of 'x' compresses to ~50 KB. That's fast to write.
        big_content = "x" * (OOXML_MAX_MEMBER_SIZE + 1)
        with zipfile.ZipFile(bomb, "w", compression=zipfile.ZIP_DEFLATED) as zf:
            zf.writestr("[Content_Types].xml", '<Types xmlns="http://schemas.openxmlformats.org/package/2006/content-types"/>')
            zf.writestr("word/document.xml", big_content)

        out = tmp_path / "out"
        result = convert_paths([bomb], out)
        assert len(result["files"]) == 0
        assert result["skipped_count"] == 1
        assert "ooxml" in result["skipped"][0]["reason"]

    def test_ooxml_normal_passes_check(self, tmp_path: Path) -> None:
        """A normal small docx passes the OOXML bomb check."""
        src = _make_docx(tmp_path / "src")
        reason = _check_ooxml_safe(src)
        assert reason is None

    def test_forbidden_prefix_rejected(self, tmp_path: Path) -> None:
        forbidden_dir = tmp_path / "runs"
        forbidden_dir.mkdir()
        src = forbidden_dir / "secret.txt"
        src.write_text("raw data", encoding="utf-8")
        out = tmp_path / "out"
        result = convert_paths([src], out, forbidden_prefixes=[forbidden_dir])
        assert len(result["files"]) == 0
        assert result["skipped"][0]["reason"] == "forbidden_source"

    def test_symlink_skipped(self, tmp_path: Path) -> None:
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        real = src_dir / "real.txt"
        real.write_text("real", encoding="utf-8")
        link = src_dir / "link.txt"
        try:
            link.symlink_to(real)
        except OSError:
            pytest.skip("symlinks not supported")
        out = tmp_path / "out"
        # Direct file: only the real file should be converted
        result = convert_paths([src_dir], out)
        names = [f["original_name"] for f in result["files"]]
        assert "link.txt" not in names

    def test_windows_reserved_stem_skipped(self) -> None:
        assert _is_windows_reserved_stem("CON.txt") is True
        assert _is_windows_reserved_stem("NUL.docx") is True
        assert _is_windows_reserved_stem("COM1.xlsx") is True
        assert _is_windows_reserved_stem("normal.txt") is False

    def test_file_count_cap(self, tmp_path: Path) -> None:
        """Iteration stops after MAX_TOTAL_FILES."""
        from daily_agenda.docs_convert import MAX_TOTAL_FILES
        src_dir = tmp_path / "src"
        src_dir.mkdir()
        # Create more files than the cap (use a small number for speed)
        # We test by setting a smaller cap via monkeypatch
        import daily_agenda.docs_convert as dc
        old_cap = dc.MAX_TOTAL_FILES
        dc.MAX_TOTAL_FILES = 3
        try:
            for i in range(10):
                (src_dir / f"f{i}.txt").write_text(f"file {i}", encoding="utf-8")
            out = tmp_path / "out"
            result = convert_paths([src_dir], out)
            assert len(result["files"]) <= 3
        finally:
            dc.MAX_TOTAL_FILES = old_cap

    def test_write_error_skipped(self, tmp_path: Path) -> None:
        """A write error on output is caught and skipped."""
        src = _make_txt(tmp_path / "src", "notes.txt", "content")
        out = tmp_path / "out"
        out.mkdir()
        # Create a dir at the expected output name so write_text fails
        (out / "notes.txt").mkdir()
        # Also create the deduplication name as a dir
        import hashlib
        h = hashlib.md5(str(src).encode()).hexdigest()[:6]
        (out / f"notes-{h}.txt").mkdir()
        result = convert_paths([src], out)
        assert result["ok"] is True
        assert result["skipped_count"] == 1
        assert result["skipped"][0]["reason"] == "write_failed"
