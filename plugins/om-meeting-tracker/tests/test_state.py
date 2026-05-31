# tests/test_state.py
from __future__ import annotations
import json
from pathlib import Path
from mt_core.state import (
    reminder_key, default_state, load_state, save_state,
    already_sent, record_sent, already_processed_reply, record_reply,
    set_human_reviewed,
)

def test_reminder_key_format():
    assert reminder_key("acme", "m1", "2026-W22", "2026-05-28") == "MT:acme:m1:2026-W22:2026-05-28"

def test_load_missing_returns_default(tmp_path):
    st = load_state(tmp_path / "nope.json", "acme")
    assert st["schema_version"] == 1 and st["tenant_id"] == "acme"
    assert st["sent_reminders"] == {}

def test_save_then_load_roundtrip(tmp_path):
    p = tmp_path / "state" / "s.json"
    st = default_state("acme")
    record_sent(st, reminder_key("acme","m1","2026-W22","2026-05-28"),
                owner_id="alice", provider_message_id="abc")
    save_state(p, st)
    again = load_state(p, "acme")
    assert again["sent_reminders"][reminder_key("acme","m1","2026-W22","2026-05-28")]["provider_message_id"] == "abc"

def test_already_sent(tmp_path):
    st = default_state("acme")
    k = reminder_key("acme","m1","2026-W22","2026-05-28")
    assert already_sent(st, k) is False
    record_sent(st, k, owner_id="alice")
    assert already_sent(st, k) is True

def test_reply_dedup():
    st = default_state("acme")
    assert already_processed_reply(st, "gmail-1") is False
    record_reply(st, "gmail-1", owner_id="alice", week="2026-W22", metric_ids=["m1"])
    assert already_processed_reply(st, "gmail-1") is True

def test_checkpoint():
    st = default_state("acme")
    set_human_reviewed(st, "blob123", "commit456", "2026-05-27T20:00:00+08:00")
    assert st["last_human_reviewed"]["tracking_file_blob_sha"] == "blob123"

def test_save_is_atomic_no_tmp_left(tmp_path):
    p = tmp_path / "state" / "s.json"
    save_state(p, default_state("acme"))
    leftover = list(tmp_path.rglob("*.tmp"))   # rglob：子目錄的 .tmp 洩漏也要抓到
    assert leftover == []
    assert json.loads(p.read_text(encoding="utf-8"))["tenant_id"] == "acme"
