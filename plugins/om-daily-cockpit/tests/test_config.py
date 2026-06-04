"""om-daily-cockpit config loader 測試。

驗證 schema 規則、密鑰拒絕、嚴格 email 比對、storage 後端驗證。
全程零 tenant hard-code：用 acme-ops 假資料。

註：測「密鑰偵測」時，假密鑰用字串串接構造，避免 source 內出現連續 pattern
（否則自家 secret-scan hook 會擋下這支測試檔本身）。
"""

from __future__ import annotations

import copy
import sys
from pathlib import Path

import pytest
import yaml

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

from oc_core.config import (  # noqa: E402
    Config,
    ConfigError,
    load_config,
    require_env,
    resolve_config_path,
)

BASE: dict = {
    "schema_version": 1,
    "tenant_id": "acme-ops",
    "timezone": "Asia/Taipei",
    "identity": {"department": "營運部", "company": "Acme Inc", "persona": "每日駕駛艙"},
    "team": {
        "members": [
            {"member_id": "a-chen", "name": "A Chen", "email": "a.chen@acme.example",
             "alias_allowlist": ["achen@personal.example"]},
            {"member_id": "b-lin", "name": "B Lin", "email": "b.lin@acme.example"},
        ]
    },
    "email": {
        "adapter": "outlook_local",
        "account": "boss@acme.example",
        "daily_report_folder": "Daily Reports",
        "processed_category": "AI Processed",
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
        "intel": {"enabled": False, "storage": "quick_only", "sources": [], "keywords": []},
        "tender": {"enabled": False, "storage": "quick_only", "keywords": []},
        "fb": {"enabled": False, "storage": "quick_only", "org_ids": []},
    },
}

# 假密鑰：用串接讓 source 不含連續 pattern，runtime 才拼成可觸發 guard 的字串。
FAKE_GEMINI = "AI" + "za" + "Sy" + "0123456789abcdefghij0123456789"
FAKE_JWT = "ey" + "J" + "abcdefghij" + "." + "klmnopqrst"


def _write_md(tmp_path: Path, data: dict, *, extra: str = "") -> Path:
    block = yaml.safe_dump(data, allow_unicode=True, sort_keys=False)
    md = f"# config\n\n說明文字\n\n```oc-config\n{block}```\n\n{extra}"
    p = tmp_path / "config.md"
    p.write_text(md, encoding="utf-8")
    return p


def _mutate(**overrides) -> dict:
    d = copy.deepcopy(BASE)
    for k, v in overrides.items():
        d[k] = v
    return d


# --- happy path ---------------------------------------------------------

def test_valid_config_loads(tmp_path):
    cfg = load_config(_write_md(tmp_path, BASE))
    assert isinstance(cfg, Config)
    assert cfg.tenant_id == "acme-ops"
    assert len(cfg.members) == 2
    assert cfg.identity.company == "Acme Inc"
    assert cfg.modules["intel"].enabled is False
    assert cfg.modules["intel"].storage == "quick_only"


def test_narrative_outside_block_ignored(tmp_path):
    cfg = load_config(_write_md(tmp_path, BASE, extra="這段在區塊外，程式不該讀。tenant_id: hacker"))
    assert cfg.tenant_id == "acme-ops"


# --- schema validation --------------------------------------------------

def test_wrong_schema_version(tmp_path):
    with pytest.raises(ConfigError, match="schema_version"):
        load_config(_write_md(tmp_path, _mutate(schema_version=2)))


def test_bad_tenant_slug(tmp_path):
    with pytest.raises(ConfigError, match="tenant_id"):
        load_config(_write_md(tmp_path, _mutate(tenant_id="Acme Ops!")))


def test_bad_timezone(tmp_path):
    with pytest.raises(ConfigError, match="timezone"):
        load_config(_write_md(tmp_path, _mutate(timezone="Mars/Phobos")))


def test_missing_identity_field(tmp_path):
    d = _mutate(identity={"department": "營運部", "company": "Acme Inc"})  # 缺 persona
    with pytest.raises(ConfigError, match="persona"):
        load_config(_write_md(tmp_path, d))


def test_duplicate_member_id(tmp_path):
    d = copy.deepcopy(BASE)
    d["team"]["members"][1]["member_id"] = "a-chen"
    with pytest.raises(ConfigError, match="member_id 重複"):
        load_config(_write_md(tmp_path, d))


def test_duplicate_member_email(tmp_path):
    d = copy.deepcopy(BASE)
    d["team"]["members"][1]["email"] = "a.chen@acme.example"
    with pytest.raises(ConfigError, match="email 重複"):
        load_config(_write_md(tmp_path, d))


def test_bad_member_email(tmp_path):
    d = copy.deepcopy(BASE)
    d["team"]["members"][0]["email"] = "not-an-email"
    with pytest.raises(ConfigError, match="email 非法"):
        load_config(_write_md(tmp_path, d))


def test_alias_collides_with_other_member_primary(tmp_path):
    """Codex F4：b 的 alias = a 的主 email → 必須拒絕（否則 member_by_email 串錯人）。"""
    d = copy.deepcopy(BASE)
    d["team"]["members"][1]["alias_allowlist"] = ["a.chen@acme.example"]
    with pytest.raises(ConfigError, match="alias email 與其他成員重複"):
        load_config(_write_md(tmp_path, d))


def test_alias_collides_with_other_member_alias(tmp_path):
    """Codex F4：b 的 alias = a 的 alias → 必須拒絕。"""
    d = copy.deepcopy(BASE)
    d["team"]["members"][1]["alias_allowlist"] = ["achen@personal.example"]  # = a-chen 既有 alias
    with pytest.raises(ConfigError, match="重複"):
        load_config(_write_md(tmp_path, d))


def test_no_members(tmp_path):
    d = copy.deepcopy(BASE)
    d["team"]["members"] = []
    with pytest.raises(ConfigError, match="至少要一個"):
        load_config(_write_md(tmp_path, d))


def test_email_adapter_rejects_unsupported(tmp_path):
    d = copy.deepcopy(BASE)
    d["email"]["adapter"] = "gmail_smtp"
    with pytest.raises(ConfigError, match="adapter"):
        load_config(_write_md(tmp_path, d))


def test_email_adapter_df_graph_accepted_without_category(tmp_path):
    # df_graph 改用本地檔去重，processed_category 可省略。
    d = copy.deepcopy(BASE)
    d["email"]["adapter"] = "df_graph"
    del d["email"]["processed_category"]
    cfg = load_config(_write_md(tmp_path, d))
    assert cfg.email.adapter == "df_graph"
    assert cfg.email.processed_category == ""


def test_email_adapter_outlook_local_still_requires_category(tmp_path):
    d = copy.deepcopy(BASE)
    del d["email"]["processed_category"]
    with pytest.raises(ConfigError, match="processed_category"):
        load_config(_write_md(tmp_path, d))


def test_absolute_path_rejected(tmp_path):
    d = copy.deepcopy(BASE)
    d["paths"]["archive_dir"] = "/etc/passwd"
    with pytest.raises(ConfigError, match="絕對路徑"):
        load_config(_write_md(tmp_path, d))


# --- services env-name enforcement & secret guard -----------------------

def test_services_value_must_be_env_name(tmp_path):
    d = copy.deepcopy(BASE)
    d["services"]["database_url_env"] = "lowercase-not-env-name"
    with pytest.raises(ConfigError, match="services.database_url_env"):
        load_config(_write_md(tmp_path, d))


def test_secret_anywhere_in_config_rejected(tmp_path):
    d = copy.deepcopy(BASE)
    d["identity"]["persona"] = f"key {FAKE_GEMINI}"
    with pytest.raises(ConfigError, match="密鑰"):
        load_config(_write_md(tmp_path, d))


def test_jwt_in_config_rejected(tmp_path):
    d = copy.deepcopy(BASE)
    d["modules"]["intel"]["keywords"] = [FAKE_JWT]
    with pytest.raises(ConfigError, match="密鑰"):
        load_config(_write_md(tmp_path, d))


# --- modules ------------------------------------------------------------

def test_bad_storage_backend(tmp_path):
    d = copy.deepcopy(BASE)
    d["modules"]["intel"]["storage"] = "mysql"
    with pytest.raises(ConfigError, match="storage"):
        load_config(_write_md(tmp_path, d))


def test_enabled_module_carries_sources(tmp_path):
    d = copy.deepcopy(BASE)
    d["modules"]["intel"] = {
        "enabled": True, "storage": "quick_only",
        "sources": ["https://example.com/rss"], "keywords": ["回收"],
    }
    cfg = load_config(_write_md(tmp_path, d))
    assert cfg.modules["intel"].enabled is True
    assert cfg.modules["intel"].sources == ["https://example.com/rss"]


# --- strict email match (anti cross-wire) -------------------------------

def test_member_by_email_strict_match(tmp_path):
    cfg = load_config(_write_md(tmp_path, BASE))
    assert cfg.member_by_email("a.chen@acme.example").member_id == "a-chen"
    assert cfg.member_by_email("ACHEN@personal.example").member_id == "a-chen"  # alias + 大小寫
    assert cfg.member_by_email("stranger@acme.example") is None
    assert cfg.member_by_email("") is None


# --- fenced block edge cases --------------------------------------------

def test_no_config_block(tmp_path):
    p = tmp_path / "config.md"
    p.write_text("# 沒有 fenced block", encoding="utf-8")
    with pytest.raises(ConfigError, match="找不到 oc-config"):
        load_config(p)


def test_two_config_blocks(tmp_path):
    block = yaml.safe_dump(BASE, allow_unicode=True, sort_keys=False)
    md = f"```oc-config\n{block}```\n\n```oc-config\n{block}```\n"
    p = tmp_path / "config.md"
    p.write_text(md, encoding="utf-8")
    with pytest.raises(ConfigError, match="必須恰好一個"):
        load_config(p)


# --- CLI helpers --------------------------------------------------------

def test_resolve_config_path_missing(monkeypatch):
    monkeypatch.delenv("OM_DAILY_COCKPIT_CONFIG", raising=False)
    with pytest.raises(ConfigError, match="未提供 config"):
        resolve_config_path(None)


def test_require_env_missing(monkeypatch):
    monkeypatch.delenv("OM_COCKPIT_TEST_SECRET", raising=False)
    with pytest.raises(ConfigError, match="缺環境變數"):
        require_env("OM_COCKPIT_TEST_SECRET")


def test_require_env_present(monkeypatch):
    monkeypatch.setenv("OM_COCKPIT_TEST_SECRET", "value123")
    assert require_env("OM_COCKPIT_TEST_SECRET") == "value123"
