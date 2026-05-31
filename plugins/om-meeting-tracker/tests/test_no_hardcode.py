from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Tenant-specific / dafeng-specific terms that must NOT appear in generic plugin code.
# Allowed only in examples/dafeng-ops/ (intentionally excluded from SCAN_DIRS).
#
# ASCII_BANNED: matched CASE-INSENSITIVELY (casefold) — catches DaFeng, DAFENG, HahaUCCU, etc.
# CJK_BANNED:   matched EXACTLY (casefold is a no-op for CJK, kept exact for clarity).
# EXACT_BANNED:  matched exactly (digits, mixed — casefold irrelevant).
_ASCII_RE = re.compile(r"^[A-Za-z0-9_]+$")

ASCII_BANNED = [
    "dafeng",
    "cockpit",
    "daily_proposal",
    "hahauccu",
    "DF_Haha",
]

CJK_BANNED = [
    "大豐",
    "向性指標",
    "策略文件",
]

EXACT_BANNED = [
    "1679325299",
]

# Directories to scan — intentionally excludes examples/ (de-identified seed fixture)
SCAN_DIRS = ["scripts", "templates", "skills", "ci"]
SCAN_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".json", ".sh"}


def _find_banned(text: str) -> list[str]:
    """Return list of banned terms found in `text`.

    ASCII terms are matched case-insensitively (casefold).
    CJK terms are matched exactly (casefold is no-op for CJK).
    Exact terms (digits) are matched exactly.
    """
    found: list[str] = []
    lower_text = text.casefold()
    for term in ASCII_BANNED:
        if term.casefold() in lower_text:
            found.append(term)
    for term in CJK_BANNED:
        if term in text:
            found.append(term)
    for term in EXACT_BANNED:
        if term in text:
            found.append(term)
    return found


def _iter_files():
    for base in SCAN_DIRS:
        base_path = ROOT / base
        if not base_path.exists():
            continue
        for p in base_path.rglob("*"):
            if p.is_file() and p.suffix in SCAN_SUFFIXES:
                yield p


def test_no_dafeng_hardcode_in_plugin_code():
    """No tenant-specific / dafeng-specific terms in plugin generic code/templates/skills/ci."""
    hits = []
    for p in _iter_files():
        try:
            text = p.read_text(encoding="utf-8")
        except UnicodeDecodeError:
            text = p.read_text(encoding="utf-8", errors="replace")
        for term in _find_banned(text):
            hits.append(f"{p.relative_to(ROOT)} 含禁用詞 {term!r}")
    assert hits == [], (
        "發現 tenant hard-code（應移到 examples/ 或改成 config 驅動）：\n"
        + "\n".join(hits)
    )


def test_casefold_would_catch_variants():
    """FIX 2: case-insensitive matching catches upper/mixed-case ASCII variants.

    Calls _find_banned directly on synthetic strings — does NOT scan the repo.
    """
    # These variants must be caught
    assert "dafeng" in _find_banned("DAFENG ops"), "DAFENG (all-caps) must be caught"
    assert "dafeng" in _find_banned("DaFeng Corp"), "DaFeng (mixed-case) must be caught"
    assert "hahauccu" in _find_banned("HahaUCCU.zeabur.app"), "HahaUCCU must be caught"
    assert "cockpit" in _find_banned("schema COCKPIT"), "COCKPIT must be caught"
    assert "daily_proposal" in _find_banned("dir: Daily_Proposal"), "Daily_Proposal must be caught"

    # CJK terms must still be caught exactly
    assert "大豐" in _find_banned("大豐環保"), "CJK exact match must work"
    assert "向性指標" in _find_banned("向性指標報告"), "CJK exact match must work"

    # Clean strings must not produce false positives
    assert _find_banned("some generic text without tenant terms") == []
    assert _find_banned("draft_dir: drafts/") == []
