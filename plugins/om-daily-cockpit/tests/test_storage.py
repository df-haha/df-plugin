"""storage adapter 測試（Phase 4.5）。

核心保證：quick_only **不落任何 DB**；sqlite round-trip + dedup；表名注入防護；工廠選擇。
"""

from __future__ import annotations

import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from oc_core.config import ConfigError, Module, load_config  # noqa: E402
from oc_core.storage import (  # noqa: E402
    QuickOnlyStorage,
    SqliteStorage,
    _safe_table,
    get_storage,
)

ITEMS = [
    {"url": "https://e.x/1", "title": "A", "summary": "sa", "published_at": "2026-06-01"},
    {"url": "https://e.x/2", "title": "B", "summary": "sb", "published_at": "2026-06-02"},
]


# --- quick_only：不落 DB ---------------------------------------------------

def test_quick_only_is_noop():
    s = QuickOnlyStorage()
    assert s.store("intel", ITEMS) == 0      # 不持久化
    assert s.recent("intel", "2026-01-01") == []


# --- sqlite round-trip + dedup -------------------------------------------

def test_sqlite_store_and_recent(tmp_path):
    s = SqliteStorage(tmp_path / "t.sqlite3")
    assert s.store("intel", ITEMS) == 2
    got = s.recent("intel", "2000-01-01")
    assert {r["url"] for r in got} == {"https://e.x/1", "https://e.x/2"}


def test_sqlite_dedup_on_url(tmp_path):
    s = SqliteStorage(tmp_path / "t.sqlite3")
    s.store("intel", ITEMS)
    again = s.store("intel", ITEMS)          # 同 url 再存
    assert again == 0                         # 全部被 dedup


def test_sqlite_recent_empty_table(tmp_path):
    s = SqliteStorage(tmp_path / "t.sqlite3")
    assert s.recent("tender", "2026-01-01") == []   # 未存過也不報錯


# --- 表名注入防護 --------------------------------------------------------

def test_safe_table_whitelist():
    assert _safe_table("intel") == "intel_items"
    with pytest.raises(ConfigError):
        _safe_table("intel; DROP TABLE x")
    with pytest.raises(ConfigError):
        _safe_table("users")


# --- 工廠選擇 ------------------------------------------------------------

DUMMY = {
    "schema_version": 1, "tenant_id": "acme-ops", "timezone": "Asia/Taipei",
    "identity": {"department": "Ops", "company": "Acme", "persona": "Cockpit"},
    "team": {"members": [{"member_id": "a", "name": "A", "email": "a@acme.example"}]},
    "email": {"adapter": "outlook_local", "account": "b@acme.example",
              "daily_report_folder": "R", "processed_category": "P"},
    "paths": {"archive_dir": "data", "daily_proposal_dir": "dp"},
    "directive": {"subject_prefix": "[T]", "marker": "<!-- om -->"},
    "services": {"database_url_env": "OM_COCKPIT_DATABASE_URL",
                 "gemini_key_env": "OM_COCKPIT_GEMINI_API_KEY",
                 "n8n_api_url_env": "OM_COCKPIT_N8N_API_URL",
                 "n8n_api_key_env": "OM_COCKPIT_N8N_API_KEY",
                 "telegram_token_env": "OM_COCKPIT_TELEGRAM_TOKEN",
                 "telegram_chat_id_env": "OM_COCKPIT_TELEGRAM_CHAT_ID"},
    "modules": {"intel": {"enabled": False, "storage": "quick_only"},
                "tender": {"enabled": False, "storage": "quick_only"},
                "fb": {"enabled": False, "storage": "quick_only"}},
}


def _cfg(tmp_path):
    block = yaml.safe_dump(DUMMY, allow_unicode=True, sort_keys=False)
    p = tmp_path / "config.md"
    p.write_text(f"```oc-config\n{block}```\n", encoding="utf-8")
    return load_config(p)


def test_factory_quick_only_default(tmp_path):
    cfg = _cfg(tmp_path)
    s = get_storage(cfg.modules["intel"], cfg)
    assert s.backend == "quick_only"


def test_factory_sqlite(tmp_path):
    cfg = _cfg(tmp_path)
    mod = Module(key="intel", enabled=True, storage="sqlite")
    s = get_storage(mod, cfg, base_dir=tmp_path)
    assert s.backend == "sqlite"
    assert str(tmp_path) in str(s.db_path)


def test_postgres_recent_ensures_table():
    """Codex F3：PG recent() 首跑須先建表（鏡像 SQLite），否則 undefined-table。

    無 DB 環境 → 用結構檢查（recent 原始碼含 _ddl 呼叫）+ 驗 _qualified 引號。
    """
    import inspect

    from oc_core.storage import PostgresStorage

    s = PostgresStorage("postgresql://x/y", schema="public")
    assert s._qualified("intel") == '"public"."intel_items"'
    assert "CREATE TABLE IF NOT EXISTS" in s._ddl(s._qualified("intel"))
    assert "_ddl" in inspect.getsource(PostgresStorage.recent), "recent() 必須先 ensure table"


def test_factory_postgres_requires_env(tmp_path, monkeypatch):
    monkeypatch.delenv("OM_COCKPIT_DATABASE_URL", raising=False)
    cfg = _cfg(tmp_path)
    mod = Module(key="intel", enabled=True, storage="postgres")
    with pytest.raises(ConfigError, match="缺環境變數"):
        get_storage(mod, cfg)
