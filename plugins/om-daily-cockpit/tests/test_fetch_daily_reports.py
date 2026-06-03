"""驗證 fetch_daily_reports 的 config→PowerShell args 映射（零 hard-code）。

純函式 build_ps_command 不需 PowerShell/wslpath，可在任何環境跑。
重點：用 acme dummy config 跑出來的指令必須是 acme 值，不得殘留任何 tenant 寫死。
"""

from __future__ import annotations

import sys
from pathlib import Path

import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from oc_core.config import load_config  # noqa: E402

# 動態載入 fetch_daily_reports（檔名非套件，用 importlib）
import importlib.util  # noqa: E402

_spec = importlib.util.spec_from_file_location(
    "fetch_daily_reports", ROOT / "scripts" / "fetch_daily_reports.py"
)
_fdr = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_fdr)
build_ps_command = _fdr.build_ps_command


DUMMY = {
    "schema_version": 1,
    "tenant_id": "acme-ops",
    "timezone": "Asia/Taipei",
    "identity": {"department": "Ops", "company": "Acme Inc", "persona": "Cockpit"},
    "team": {"members": [
        {"member_id": "a-chen", "name": "A Chen", "email": "a.chen@acme.example"},
        {"member_id": "b-lin", "name": "B Lin", "email": "b.lin@acme.example"},
    ]},
    "email": {"adapter": "outlook_local", "account": "boss@acme.example",
              "daily_report_folder": "Daily Reports", "processed_category": "AI Processed",
              "inbox_name": "Inbox"},
    "paths": {"archive_dir": "data/daily_reports", "daily_proposal_dir": "daily_proposal"},
    "directive": {"subject_prefix": "[Track]", "marker": "<!-- om-directive -->"},
    "services": {
        "database_url_env": "OM_COCKPIT_DATABASE_URL",
        "gemini_key_env": "OM_COCKPIT_GEMINI_API_KEY",
        "n8n_api_url_env": "OM_COCKPIT_N8N_API_URL",
        "n8n_api_key_env": "OM_COCKPIT_N8N_API_KEY",
        "telegram_token_env": "OM_COCKPIT_TELEGRAM_TOKEN",
        "telegram_chat_id_env": "OM_COCKPIT_TELEGRAM_CHAT_ID",
    },
    "modules": {"intel": {"enabled": False, "storage": "quick_only"},
                "tender": {"enabled": False, "storage": "quick_only"},
                "fb": {"enabled": False, "storage": "quick_only"}},
}


def _cfg(tmp_path):
    block = yaml.safe_dump(DUMMY, allow_unicode=True, sort_keys=False)
    p = tmp_path / "config.md"
    p.write_text(f"```oc-config\n{block}```\n", encoding="utf-8")
    return load_config(p)


def test_cmd_uses_config_values(tmp_path):
    cfg = _cfg(tmp_path)
    cmd = build_ps_command(cfg, "2026-04-21", r"C:\win\archive", r"C:\win\script.ps1")
    joined = " ".join(cmd)
    # 來自 config 的值
    assert "boss@acme.example" in cmd
    assert "A Chen,B Lin" in cmd            # 成員顯示名，逗號串接
    assert "Daily Reports" in cmd
    assert "Inbox" in cmd
    assert "AI Processed" in cmd
    assert "2026-04-21" in cmd
    # 參數旗標齊全
    for flag in ("-OutlookAccount", "-FolderName", "-InboxName", "-TeamMembers",
                 "-ArchiveDir", "-Category", "-AttachmentPattern", "-SubjectPattern"):
        assert flag in cmd, f"缺旗標 {flag}"


def test_no_dafeng_leak(tmp_path):
    """跑 acme config 不得殘留任何大豐 tenant 值。"""
    cfg = _cfg(tmp_path)
    cmd = build_ps_command(cfg, "2026-04-21", r"C:\win\archive", r"C:\win\script.ps1")
    joined = " ".join(cmd)
    for banned in ("游宗霖", "林梅杏", "蕭欣萍", "df-recycle", "haha.huang", "收件匣"):
        assert banned not in joined, f"洩漏 tenant 值：{banned}"


def test_subject_pattern_language_neutral(tmp_path):
    """預設 subject pattern 應為語言中性日期 matcher（不含中文）。"""
    cfg = _cfg(tmp_path)
    assert "每日工作報告" not in cfg.email.report_subject_pattern
    assert r"(\d{4})" in cfg.email.report_subject_pattern


def test_last_workday_accepts_timezone():
    """Codex R4-F2：get_last_workday 接受 tz 參數（非台灣時區 tenant 才算得對）。"""
    from datetime import date
    from zoneinfo import ZoneInfo

    import last_workday

    # ref-based 計算：2026-06-03(三) → 上一工作日 2026-06-02(二)；tz 參數須存在且不報錯
    assert last_workday.get_last_workday(
        ref=date(2026, 6, 3), tz=ZoneInfo("America/New_York")
    ) == date(2026, 6, 2)
