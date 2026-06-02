"""Phase 0 Gate 機械化執法：通用 plugin 碼內不得有 tenant hard-code 或密鑰形狀。

兩道檢查：
1. test_no_tenant_hardcode — 掃 tenant 識別詞（公司/人名/基礎設施）。
2. test_no_secret_shapes  — 用 regex 掃密鑰「形狀」（不嵌真密鑰字面值）。

掃描範圍刻意排除 examples/（去識別化種子）與 tests/（本檔即含禁用詞清單）。
"""

from __future__ import annotations

import re
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent

# --- tenant 識別詞（非密鑰，可明列）---------------------------------------
# ASCII：casefold 比對（抓 DaFeng / HahaUCCU 等變體）。注意「cockpit」是本 plugin
# 自身名稱，刻意「不」列入。
ASCII_BANNED = [
    "dafeng",
    "df-recycle",
    "hahauccu",
]

CJK_BANNED = [
    "大豐",
    "向性指標",
    "策略文件",
    "游宗霖",
    "林梅杏",
    "蕭欣萍",
    "王彬褣",
    "黃奕儒",
]

EXACT_BANNED = [
    "1679325299",        # Telegram chat id
    "haha.huang",        # Windows username
]

# --- 密鑰形狀（regex，不嵌真值）------------------------------------------
SECRET_SHAPES = [
    re.compile(r"AIza[0-9A-Za-z_\-]{20,}"),                      # Google/Gemini key
    re.compile(r"eyJ[A-Za-z0-9_\-]{8,}\.[A-Za-z0-9_\-]{8,}"),    # JWT（n8n key）
    re.compile(r"postgres(?:ql)?://[^/\s]+:[^@\s]+@"),           # 連線字串含密碼
]

SCAN_DIRS = ["scripts", "templates", "skills", "ci", "commands"]
SCAN_SUFFIXES = {".py", ".md", ".yml", ".yaml", ".json", ".sh", ".js"}


def _iter_files():
    for base in SCAN_DIRS:
        base_path = ROOT / base
        if not base_path.exists():
            continue
        for p in base_path.rglob("*"):
            if p.is_file() and p.suffix in SCAN_SUFFIXES and "__pycache__" not in p.parts:
                yield p


def _read(p: Path) -> str:
    try:
        return p.read_text(encoding="utf-8")
    except UnicodeDecodeError:
        return p.read_text(encoding="utf-8", errors="replace")


def _find_tenant_terms(text: str) -> list[str]:
    found: list[str] = []
    lower = text.casefold()
    for term in ASCII_BANNED:
        if term.casefold() in lower:
            found.append(term)
    for term in CJK_BANNED:
        if term in text:
            found.append(term)
    for term in EXACT_BANNED:
        if term in text:
            found.append(term)
    return found


def test_no_tenant_hardcode():
    """通用碼/模板/skill 內不得出現 tenant 識別詞（應移到 examples/ 或改 config 驅動）。"""
    hits = []
    for p in _iter_files():
        for term in _find_tenant_terms(_read(p)):
            hits.append(f"{p.relative_to(ROOT)} 含禁用詞 {term!r}")
    assert hits == [], "發現 tenant hard-code：\n" + "\n".join(hits)


def test_no_secret_shapes():
    """通用碼內不得出現密鑰形狀字串。"""
    hits = []
    for p in _iter_files():
        text = _read(p)
        for pat in SECRET_SHAPES:
            if pat.search(text):
                hits.append(f"{p.relative_to(ROOT)} 含疑似密鑰（pattern: {pat.pattern}）")
    assert hits == [], "發現疑似硬編碼密鑰：\n" + "\n".join(hits)


def test_find_tenant_terms_self_check():
    """偵測邏輯自我驗證（不掃 repo，純合成字串）。"""
    assert "dafeng" in _find_tenant_terms("DAFENG ops")          # 全大寫
    assert "dafeng" in _find_tenant_terms("DaFeng Corp")          # 混合
    assert "hahauccu" in _find_tenant_terms("HahaUCCU.zeabur.app")
    assert "大豐" in _find_tenant_terms("大豐環保")
    assert "1679325299" in _find_tenant_terms("chatId 1679325299")
    # 乾淨字串不誤判（且「cockpit」不在禁用詞內）
    assert _find_tenant_terms("om-daily-cockpit generic code") == []
