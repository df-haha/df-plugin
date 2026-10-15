"""Convert user documents (pptx/pdf/xlsx/docx/md/txt) to text for profile building.

Uses lightweight direct libraries:
- python-docx for .docx
- python-pptx for .pptx (slide text + notes)
- openpyxl for .xlsx (read_only, data_only)
- pdfminer.six for .pdf

Outputs to ``<home>/docs-cache/<batch-id>/``.
Files that are too large, unsupported, or fail conversion are skipped with counts.

Memory safety: OOXML files (docx/pptx/xlsx) are pre-screened with zipfile to
reject bombs (uncompressed size > 100 MB, member count > 2000, single member
> 50 MB).  PDF extraction is capped at 200 pages.
"""

from __future__ import annotations

import hashlib
import os
import re
import sys
import zipfile
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------

MAX_FILE_BYTES = 10 * 1024 * 1024       # 10 MB on-disk per input file
MAX_TOTAL_CHARS = 500_000                # total chars across all output files
MAX_TOTAL_FILES = 500                    # max files iterated
MAX_TOTAL_FILE_BYTES = 200 * 1024 * 1024 # max total on-disk bytes iterated
XLSX_MAX_ROWS = 5_000                    # max rows per sheet
XLSX_MAX_CHARS = 100_000                 # max chars per xlsx file
PDF_MAX_PAGES = 200                      # max pages extracted from a PDF
OOXML_MAX_UNCOMPRESSED = 100 * 1024 * 1024  # 100 MB uncompressed
OOXML_MAX_MEMBERS = 2_000
OOXML_MAX_MEMBER_SIZE = 50 * 1024 * 1024    # 50 MB per member
SUPPORTED_EXTENSIONS = frozenset({
    ".md", ".txt", ".docx", ".pptx", ".pdf", ".xlsx",
})
_OOXML_EXTENSIONS = frozenset({".docx", ".pptx", ".xlsx"})

# Windows reserved names (stem, case-insensitive)
_WINDOWS_RESERVED = frozenset({
    "CON", "PRN", "AUX", "NUL",
    *(f"COM{i}" for i in range(1, 10)),
    *(f"LPT{i}" for i in range(1, 10)),
})


# ---------------------------------------------------------------------------
# OOXML bomb check
# ---------------------------------------------------------------------------

def _check_ooxml_safe(path: Path) -> str | None:
    """Pre-screen an OOXML file. Returns a skip reason or None if safe."""
    try:
        with zipfile.ZipFile(path, "r") as zf:
            infos = zf.infolist()
            if len(infos) > OOXML_MAX_MEMBERS:
                return "ooxml_too_many_members"
            total_uncompressed = 0
            for info in infos:
                if info.file_size > OOXML_MAX_MEMBER_SIZE:
                    return "ooxml_member_too_large"
                total_uncompressed += info.file_size
            if total_uncompressed > OOXML_MAX_UNCOMPRESSED:
                return "ooxml_uncompressed_too_large"
    except (zipfile.BadZipFile, OSError):
        return "ooxml_invalid"
    return None


# ---------------------------------------------------------------------------
# Filesystem safety
# ---------------------------------------------------------------------------

def _is_junction_or_symlink(p: Path) -> bool:
    """Detect symlinks and Windows junctions (NTFS reparse points)."""
    if p.is_symlink():
        return True
    if sys.version_info >= (3, 12):
        try:
            return p.is_junction()  # type: ignore[attr-defined]
        except (OSError, AttributeError):
            return False
    try:
        st = os.lstat(p)
        attrs = getattr(st, "st_file_attributes", 0)
        return bool(attrs & 0x400)  # FILE_ATTRIBUTE_REPARSE_POINT
    except (OSError, AttributeError):
        return False


def _is_hidden_or_system(p: Path) -> bool:
    """Check hidden/system: dot-prefix on any platform, system attr on Windows."""
    if any(part.startswith(".") for part in p.parts):
        return True
    try:
        st = os.lstat(p)
        attrs = getattr(st, "st_file_attributes", 0)
        # FILE_ATTRIBUTE_HIDDEN=0x2, FILE_ATTRIBUTE_SYSTEM=0x4
        if attrs & (0x2 | 0x4):
            return True
    except (OSError, AttributeError):
        pass
    return False


def _is_windows_reserved_stem(name: str) -> bool:
    """Check if stem is a Windows reserved name (CON, NUL, COM1, etc.)."""
    stem = Path(name).stem.upper()
    return stem in _WINDOWS_RESERVED


# ---------------------------------------------------------------------------
# Per-format converters
# ---------------------------------------------------------------------------

def _convert_md_txt(path: Path) -> str:
    return path.read_text(encoding="utf-8", errors="replace")


def _convert_docx(path: Path) -> str:
    from docx import Document  # type: ignore[import-untyped]

    doc = Document(str(path))
    paragraphs: list[str] = []
    for para in doc.paragraphs:
        text = para.text.strip()
        if text:
            paragraphs.append(text)
    return "\n".join(paragraphs)


def _convert_pptx(path: Path) -> str:
    from pptx import Presentation  # type: ignore[import-untyped]

    prs = Presentation(str(path))
    parts: list[str] = []
    for slide_num, slide in enumerate(prs.slides, 1):
        slide_parts: list[str] = [f"--- Slide {slide_num} ---"]
        for shape in slide.shapes:
            if shape.has_text_frame:
                for para in shape.text_frame.paragraphs:
                    text = para.text.strip()
                    if text:
                        slide_parts.append(text)
        if slide.has_notes_slide and slide.notes_slide.notes_text_frame:
            notes = slide.notes_slide.notes_text_frame.text.strip()
            if notes:
                slide_parts.append(f"[Notes] {notes}")
        parts.append("\n".join(slide_parts))
    return "\n\n".join(parts)


def _convert_xlsx(path: Path) -> str:
    from openpyxl import load_workbook  # type: ignore[import-untyped]

    wb = load_workbook(str(path), read_only=True, data_only=True)
    parts: list[str] = []
    total_chars = 0

    try:
        for sheet_name in wb.sheetnames:
            ws = wb[sheet_name]
            sheet_parts: list[str] = [f"--- Sheet: {sheet_name} ---"]
            row_count = 0
            for row in ws.iter_rows(values_only=True):
                if row_count >= XLSX_MAX_ROWS:
                    sheet_parts.append(f"... (truncated at {XLSX_MAX_ROWS} rows)")
                    break
                cells = [str(c) if c is not None else "" for c in row]
                line = "\t".join(cells)
                sheet_parts.append(line)
                row_count += 1
                total_chars += len(line)
                if total_chars >= XLSX_MAX_CHARS:
                    sheet_parts.append(f"... (truncated at {XLSX_MAX_CHARS} chars)")
                    break
            parts.append("\n".join(sheet_parts))
            if total_chars >= XLSX_MAX_CHARS:
                break
    finally:
        wb.close()

    return "\n\n".join(parts)


def _convert_pdf(path: Path) -> str:
    from pdfminer.high_level import extract_text  # type: ignore[import-untyped]

    return extract_text(str(path), maxpages=PDF_MAX_PAGES)


# ---------------------------------------------------------------------------
# Dispatcher
# ---------------------------------------------------------------------------

_CONVERTERS = {
    ".md": _convert_md_txt,
    ".txt": _convert_md_txt,
    ".docx": _convert_docx,
    ".pptx": _convert_pptx,
    ".xlsx": _convert_xlsx,
    ".pdf": _convert_pdf,
}


# ---------------------------------------------------------------------------
# Conversion
# ---------------------------------------------------------------------------

def _safe_stem(name: str) -> str:
    """Create a filesystem-safe stem from a filename (no path separators)."""
    stem = Path(name).stem
    safe = re.sub(r"[/\\:*?\"<>|]", "_", stem)
    safe = safe[:80] if safe else "unnamed"
    # Guard Windows reserved stems
    if safe.upper() in _WINDOWS_RESERVED:
        safe = f"_{safe}"
    return safe


def convert_paths(
    paths: list[Path],
    output_dir: Path,
    *,
    forbidden_prefixes: list[Path] | None = None,
) -> dict[str, Any]:
    """Convert documents at *paths* into text files under *output_dir*.

    Parameters
    ----------
    forbidden_prefixes : paths that sources must NOT resolve under
        (e.g. runs/, gen/, output/, configured output.dir — those contain
        raw third-party data).

    Returns a result dict with ``ok``, ``files``, ``skipped``.
    """
    output_dir.mkdir(parents=True, exist_ok=True)
    _forbidden = [p.resolve() for p in (forbidden_prefixes or [])]

    converted: list[dict[str, Any]] = []
    skipped: list[dict[str, str]] = []
    total_chars = 0

    for src in _iter_files(paths):
        ext = src.suffix.lower()

        if ext not in SUPPORTED_EXTENSIONS:
            skipped.append({"path": str(src), "reason": "unsupported_type"})
            continue

        # Reject sources under forbidden prefixes
        try:
            resolved = src.resolve()
            for fp in _forbidden:
                if resolved == fp or str(resolved).startswith(str(fp) + os.sep):
                    skipped.append({"path": str(src), "reason": "forbidden_source"})
                    break
            else:
                resolved = None  # sentinel: not forbidden
            if resolved is not None:
                continue
        except OSError:
            skipped.append({"path": str(src), "reason": "unreadable"})
            continue

        try:
            size = src.stat().st_size
        except OSError:
            skipped.append({"path": str(src), "reason": "unreadable"})
            continue

        if size > MAX_FILE_BYTES:
            skipped.append({"path": str(src), "reason": "too_large"})
            continue

        if total_chars >= MAX_TOTAL_CHARS:
            skipped.append({"path": str(src), "reason": "total_cap_reached"})
            continue

        # OOXML bomb check before parsing
        if ext in _OOXML_EXTENSIONS:
            bomb_reason = _check_ooxml_safe(src)
            if bomb_reason:
                skipped.append({"path": str(src), "reason": bomb_reason})
                continue

        converter = _CONVERTERS.get(ext)
        if converter is None:
            skipped.append({"path": str(src), "reason": "unsupported_type"})
            continue

        try:
            text = converter(src)
        except Exception:
            skipped.append({"path": str(src), "reason": "conversion_failed"})
            continue

        # Truncate if over cap
        remaining = MAX_TOTAL_CHARS - total_chars
        if len(text) > remaining:
            text = text[:remaining]

        # Write output (guard write errors so one-JSON contract holds)
        safe_name = _safe_stem(src.name) + ".txt"
        out_path = output_dir / safe_name
        if out_path.exists():
            h = hashlib.md5(str(src).encode()).hexdigest()[:6]
            safe_name = _safe_stem(src.name) + f"-{h}.txt"
            out_path = output_dir / safe_name

        try:
            out_path.write_text(text, encoding="utf-8")
        except OSError:
            skipped.append({"path": str(src), "reason": "write_failed"})
            continue

        total_chars += len(text)

        converted.append({
            "path": str(out_path),
            "original_name": src.name,
            "chars": len(text),
        })

    return {
        "ok": True,
        "code": None,
        "batch_dir": str(output_dir),
        "files": converted,
        "skipped_count": len(skipped),
        "skipped": skipped[:20],
    }


def _iter_files(paths: list[Path]):
    """Yield individual files from paths (files directly, dirs recursively).

    Skips symlinks, NTFS junctions, hidden/system files. Caps total file
    count and total bytes to prevent runaway iteration.
    """
    file_count = 0
    total_bytes = 0

    for p in paths:
        if _is_junction_or_symlink(p):
            continue
        if p.is_file():
            if _is_hidden_or_system(p):
                continue
            if _is_windows_reserved_stem(p.name):
                continue
            try:
                sz = p.stat().st_size
            except OSError:
                continue
            total_bytes += sz
            file_count += 1
            if file_count > MAX_TOTAL_FILES or total_bytes > MAX_TOTAL_FILE_BYTES:
                return
            yield p
        elif p.is_dir():
            for child in sorted(p.rglob("*")):
                if _is_junction_or_symlink(child):
                    continue
                if not child.is_file():
                    continue
                try:
                    rel = child.relative_to(p)
                except ValueError:
                    continue
                if any(part.startswith(".") for part in rel.parts):
                    continue
                if _is_windows_reserved_stem(child.name):
                    continue
                try:
                    sz = child.stat().st_size
                except OSError:
                    continue
                total_bytes += sz
                file_count += 1
                if file_count > MAX_TOTAL_FILES or total_bytes > MAX_TOTAL_FILE_BYTES:
                    return
                yield child
