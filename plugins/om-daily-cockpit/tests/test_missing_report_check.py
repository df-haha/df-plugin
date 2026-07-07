"""missing_report_check.py + config on_leave_until 測試。

離線：用 tmp 目錄造假歸檔檔案；日期固定（無 holidays 檔，工作日序列可預期）。
基準：as_of = 2026-07-10（週五）→ recent_workdays 5 = [07-09, 07-08, 07-07, 07-06, 07-03]
（跳過 07-04 週六 / 07-05 週日）。
"""

from __future__ import annotations

import copy
import json
import sys
from datetime import date
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import missing_report_check as mrc  # noqa: E402
from oc_core.config import ConfigError, load_config  # noqa: E402

AS_OF = date(2026, 7, 10)
EXPECTED_WORKDAYS = [
    date(2026, 7, 9), date(2026, 7, 8), date(2026, 7, 7),
    date(2026, 7, 6), date(2026, 7, 3),
]

BASE: dict = {
    "schema_version": 1,
    "tenant_id": "acme-ops",
    "timezone": "Asia/Taipei",
    "identity": {"department": "營運部", "company": "Acme Inc", "persona": "駕駛艙"},
    "team": {
        "members": [
            {"member_id": "a-chen", "name": "A Chen", "email": "a.chen@acme.example"},
            {"member_id": "b-lin", "name": "B Lin", "email": "b.lin@acme.example"},
        ]
    },
    "email": {
        "adapter": "df_graph",
        "account": "boss@acme.example",
        "daily_report_folder": "Daily Reports",
    },
    "paths": {"archive_dir": "data/daily_reports", "daily_proposal_dir": "daily_proposal"},
    "directive": {"subject_prefix": "[Daily Track]", "marker": "<!-- om-directive -->"},
    "services": {
        "database_url_env": "OM_COCKPIT_DATABASE_URL",
        "gemini_key_env": "OM_COCKPIT_GEMINI_API_KEY",
        "n8n_api_url_env": "OM_COCKPIT_N8N_API_URL",
        "n8n_api_key_env": "OM_COCKPIT_N8N_API_KEY",
        "telegram_token_env": "OM_COCKPIT_TELEGRAM_TOKEN",
        "telegram_chat_id_env": "OM_COCKPIT_TELEGRAM_CHAT_ID",
    },
    "modules": {
        "intel": {"enabled": False, "storage": "quick_only"},
        "tender": {"enabled": False, "storage": "quick_only"},
        "fb": {"enabled": False, "storage": "quick_only"},
    },
}


def _write_md(tmp_path: Path, data: dict) -> Path:
    block = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    p = tmp_path / "config.md"
    p.write_text(f"# config\n\n```oc-config\n{block}```\n", encoding="utf-8")
    return p


def _load(tmp_path: Path, **member_overrides):
    """載入 BASE config，member[0]（A Chen）套用 overrides。"""
    d = copy.deepcopy(BASE)
    d["team"]["members"][0].update(member_overrides)
    return load_config(_write_md(tmp_path, d))


def _put_report(archive_root: Path, name: str, d: date) -> None:
    day_dir = archive_root / d.isoformat()
    day_dir.mkdir(parents=True, exist_ok=True)
    (day_dir / f"{name}_daily_work_log_{d.isoformat()}.md").write_text(
        f"# {name} {d.isoformat()}\n", encoding="utf-8"
    )


def _check(cfg, archive_root: Path, *, threshold: int = 2) -> dict:
    result = mrc.check_all(cfg, archive_root, AS_OF, threshold)
    return {m["member_id"]: m for m in result["members"]}


# --- 工作日序列 ---------------------------------------------------------

def test_recent_workdays_skips_weekend():
    assert mrc.recent_workdays(AS_OF, 5) == EXPECTED_WORKDAYS


# --- 連續缺報升級 -------------------------------------------------------

def test_two_consecutive_missing_escalates(tmp_path):
    cfg = _load(tmp_path)
    archive = tmp_path / "archive"
    # A Chen：07-09、07-08 缺，07-07 有報 → streak 停在 2
    _put_report(archive, "A Chen", date(2026, 7, 7))
    row = _check(cfg, archive)["a-chen"]
    assert row["missing_streak"] == 2
    assert row["escalate"] is True
    assert row["on_leave"] is False
    assert row["missing_dates"] == ["2026-07-09", "2026-07-08"]


def test_report_present_breaks_streak(tmp_path):
    cfg = _load(tmp_path)
    archive = tmp_path / "archive"
    _put_report(archive, "A Chen", date(2026, 7, 9))  # 最近工作日就有報
    row = _check(cfg, archive)["a-chen"]
    assert row["missing_streak"] == 0
    assert row["escalate"] is False


def test_one_missing_below_threshold(tmp_path):
    cfg = _load(tmp_path)
    archive = tmp_path / "archive"
    _put_report(archive, "A Chen", date(2026, 7, 8))  # 只有 07-09 缺
    row = _check(cfg, archive, threshold=2)["a-chen"]
    assert row["missing_streak"] == 1
    assert row["escalate"] is False


# --- 休假豁免 -----------------------------------------------------------

def test_on_leave_not_escalated(tmp_path):
    # 休假到 07-31（含 as_of）；全窗都缺報也不升級
    cfg = _load(tmp_path, on_leave_until="2026-07-31")
    archive = tmp_path / "archive"
    row = _check(cfg, archive)["a-chen"]
    assert row["on_leave"] is True
    assert row["missing_streak"] == 0
    assert row["escalate"] is False
    assert row["missing_dates"] == []


def test_leave_expired_resumes_counting(tmp_path):
    # 休假到 07-01（早於全部近期工作日）→ 休假過期、缺報恢復計算
    cfg = _load(tmp_path, on_leave_until="2026-07-01")
    archive = tmp_path / "archive"
    row = _check(cfg, archive)["a-chen"]
    assert row["on_leave"] is False
    assert row["missing_streak"] == 5
    assert row["escalate"] is True


def test_leave_end_exempts_all_earlier_days(tmp_path):
    # on_leave_until 是休假迄日（無起日）→ 所有 <= 07-08 的工作日皆豁免，
    # 只剩 07-09 這個休假結束後的工作日計缺報。
    cfg = _load(tmp_path, on_leave_until="2026-07-08")
    archive = tmp_path / "archive"
    row = _check(cfg, archive)["a-chen"]
    assert row["on_leave"] is False  # as_of 07-10 已非休假
    assert row["missing_streak"] == 1
    assert row["missing_dates"] == ["2026-07-09"]
    assert row["escalate"] is False  # 未達 threshold 2


# --- config schema：on_leave_until 驗證 ---------------------------------

def test_legacy_config_without_field_backward_compatible(tmp_path):
    cfg = _load(tmp_path)  # BASE 無 on_leave_until
    m = cfg.member_by_email("a.chen@acme.example")
    assert m.on_leave_until is None
    assert m.is_on_leave(AS_OF) is False


def test_valid_leave_string_parsed(tmp_path):
    cfg = _load(tmp_path, on_leave_until="2026-07-15")
    m = cfg.member_by_email("a.chen@acme.example")
    assert m.on_leave_until == "2026-07-15"
    assert m.is_on_leave(date(2026, 7, 15)) is True   # 含當日
    assert m.is_on_leave(date(2026, 7, 16)) is False


def test_yaml_unquoted_date_accepted(tmp_path):
    # YAML 未加引號 → safe_load 轉成 datetime.date，仍應正規化為字串通過
    cfg = _load(tmp_path, on_leave_until=date(2026, 7, 20))
    m = cfg.member_by_email("a.chen@acme.example")
    assert m.on_leave_until == "2026-07-20"


def test_illegal_leave_date_rejected(tmp_path):
    with pytest.raises(ConfigError, match="on_leave_until"):
        _load(tmp_path, on_leave_until="2026-13-40")


def test_non_string_leave_rejected(tmp_path):
    with pytest.raises(ConfigError, match="on_leave_until"):
        _load(tmp_path, on_leave_until=12345)


# --- CLI 整合（main → stdout JSON）-------------------------------------

def test_cli_main_emits_json(tmp_path, monkeypatch, capsys):
    d = copy.deepcopy(BASE)
    d["team"]["members"][0]["on_leave_until"] = "2026-07-31"  # A Chen 休假
    cfg_path = _write_md(tmp_path, d)
    archive = tmp_path / "arch"
    _put_report(archive, "B Lin", date(2026, 7, 9))  # B Lin 有報

    monkeypatch.setattr(sys, "argv", [
        "missing_report_check.py",
        "--config", str(cfg_path),
        "--archive-root", str(archive),
        "--as-of", "2026-07-10",
        "--threshold", "2",
    ])
    assert mrc.main() == 0
    out = json.loads(capsys.readouterr().out)
    assert out["as_of"] == "2026-07-10"
    assert out["threshold"] == 2
    rows = {m["member_id"]: m for m in out["members"]}
    assert rows["a-chen"]["on_leave"] is True
    assert rows["a-chen"]["escalate"] is False
    assert rows["b-lin"]["missing_streak"] == 0
    assert rows["b-lin"]["escalate"] is False
