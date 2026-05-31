# tests/test_state_store.py
from __future__ import annotations
import json
from pathlib import Path
from mt_core.config import load_config
from mt_core.state import default_state
from mt_core.state_store import load_state_for, save_state_for, PostgresStore

FIX = Path(__file__).parent / "fixtures"   # config_valid.md 預設 state_backend=git_branch

def test_git_branch_dispatch_roundtrip(tmp_path):
    cfg = load_config(FIX / "config_valid.md")          # state_backend == "git_branch"
    save_state_for(cfg, tmp_path, default_state(cfg.tenant_id))
    assert (tmp_path / cfg.paths.state_file).exists()
    st = load_state_for(cfg, tmp_path)
    assert st["tenant_id"] == cfg.tenant_id

class _FakeCur:
    def __init__(self, backing): self.backing = backing; self._last = None
    def execute(self, sql, params):
        if sql.lstrip().upper().startswith("SELECT"):
            self._last = self.backing.get(params[0])
        else:
            self.backing[params[0]] = json.loads(params[1])  # %s::jsonb 收 json 字串
    def fetchone(self): return (self._last,) if self._last is not None else None
    def __enter__(self): return self
    def __exit__(self, *a): return False

class _FakeConn:
    def __init__(self, backing): self.backing = backing
    def cursor(self): return _FakeCur(self.backing)
    def commit(self): pass
    def close(self): pass

def test_postgres_store_roundtrip():
    backing = {}
    store = PostgresStore("acme", connect=lambda: _FakeConn(backing))
    assert store.load()["tenant_id"] == "acme"          # 空 → default_state
    st = default_state("acme"); st["metric_last_nudge"]["m1"] = "2026-05-29"
    store.save(st)
    assert store.load()["metric_last_nudge"]["m1"] == "2026-05-29"
