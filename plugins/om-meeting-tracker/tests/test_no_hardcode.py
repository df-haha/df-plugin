from __future__ import annotations

from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# Tenant-specific / dafeng-specific terms that must NOT appear in generic plugin code.
# Allowed only in examples/dafeng-ops/ (intentionally excluded from SCAN_DIRS).
BANNED = [
    # company / tenant identifiers
    "大豐",
    "dafeng",
    # harness-protected schema
    "cockpit",
    # dafeng-specific document names
    "向性指標",
    "daily_proposal",
    "策略文件",
    # infrastructure / secrets hard-coded to dafeng instance
    "hahauccu",
    "DF_Haha",
    "1679325299",
]

# Directories to scan — intentionally excludes examples/ (de-identified seed fixture)
SCAN_DIRS = ["scripts", "templates", "skills", "ci"]
SCAN_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".json", ".sh"}


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
        for term in BANNED:
            if term in text:
                hits.append(f"{p.relative_to(ROOT)} 含禁用詞 {term!r}")
    assert hits == [], (
        "發現 tenant hard-code（應移到 examples/ 或改成 config 驅動）：\n"
        + "\n".join(hits)
    )
