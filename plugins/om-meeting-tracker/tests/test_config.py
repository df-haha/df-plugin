from __future__ import annotations
from pathlib import Path
import pytest
from mt_core.config import load_config, extract_config_block, ConfigError, Config

FIX = Path(__file__).parent / "fixtures"

def test_load_valid_config():
    cfg = load_config(FIX / "config_valid.md")
    assert isinstance(cfg, Config)
    assert cfg.tenant_id == "acme-ops"
    assert cfg.timezone == "Asia/Taipei"
    assert [o.owner_id for o in cfg.owners] == ["alice", "bob"]
    assert cfg.owners[0].alias_allowlist == ["alice.work@example.com"]
    assert {m.metric_id for m in cfg.metrics} == {"cost-q2", "revenue-q2"}
    assert cfg.metrics[0].deadline.isoformat() == "2026-06-30"
    assert cfg.send.adapter == "n8n_webhook"
    assert cfg.owners[0].tier == 2   # alice 顯式
    assert cfg.owners[1].tier == 1   # bob 省略 → 預設 1

def test_zero_block_raises():
    with pytest.raises(ConfigError, match="找不到"):
        extract_config_block("# no block here\n")

def test_two_blocks_raise():
    md = "```mt-config\nschema_version: 1\n```\n\n```mt-config\nschema_version: 1\n```\n"
    with pytest.raises(ConfigError, match="2 個"):
        extract_config_block(md)

def _mutate(tmp_path: Path, replace: tuple[str, str]) -> Path:
    text = (FIX / "config_valid.md").read_text(encoding="utf-8").replace(*replace)
    p = tmp_path / "config.md"
    p.write_text(text, encoding="utf-8")
    return p

def test_duplicate_owner_id(tmp_path):
    p = _mutate(tmp_path, ("owner_id: bob", "owner_id: alice"))
    with pytest.raises(ConfigError, match="owner_id 重複"):
        load_config(p)

def test_metric_owner_not_exist(tmp_path):
    p = _mutate(tmp_path, ("owner_id: alice\n    title: Q2 降本", "owner_id: ghost\n    title: Q2 降本"))
    with pytest.raises(ConfigError, match="owner_id 不存在"):
        load_config(p)

def test_bad_email(tmp_path):
    p = _mutate(tmp_path, ("alice@example.com", "not-an-email"))
    with pytest.raises(ConfigError, match="email 非法"):
        load_config(p)

def test_bad_date(tmp_path):
    p = _mutate(tmp_path, ('deadline: "2026-06-30"', 'deadline: "2026-13-40"'))
    with pytest.raises(ConfigError, match="deadline 非法"):
        load_config(p)

def test_bad_cadence(tmp_path):
    p = _mutate(tmp_path, ("cadence: daily", "cadence: hourly"))
    with pytest.raises(ConfigError, match="cadence 非法"):
        load_config(p)

def test_path_traversal(tmp_path):
    p = _mutate(tmp_path, ("tracking_file: tracking/weekly.md", "tracking_file: ../escape.md"))
    with pytest.raises(ConfigError, match="不可為絕對路徑或含"):
        load_config(p)

def test_bad_timezone(tmp_path):
    p = _mutate(tmp_path, ("timezone: Asia/Taipei", "timezone: Mars/Olympus"))
    with pytest.raises(ConfigError, match="timezone"):
        load_config(p)

def test_snooze_cadence_ok(tmp_path):
    p = _mutate(tmp_path, ("cadence: daily", "cadence: snooze:2026-07-01"))
    cfg = load_config(p)
    assert cfg.metrics[0].cadence == "snooze:2026-07-01"

def test_bad_tier(tmp_path):
    p = _mutate(tmp_path, ("tier: 2", "tier: 3"))
    with pytest.raises(ConfigError, match="tier 非法"):
        load_config(p)

def test_default_state_backend():
    cfg = load_config(FIX / "config_valid.md")
    assert cfg.state_backend == "git_branch"

def test_bad_state_backend(tmp_path):
    p = _mutate(tmp_path, ("send:\n  adapter: n8n_webhook", "send:\n  adapter: n8n_webhook\nstate_backend: redis"))
    with pytest.raises(ConfigError, match="state_backend 非法"):
        load_config(p)
