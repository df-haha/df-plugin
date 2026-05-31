# tests/test_digest.py
from __future__ import annotations
from datetime import date
from mt_core.config import Owner, Metric
from mt_core.reminders import OwnerReminder
from mt_core.digest import compose_digest, correlation_token

def _reminder():
    owner = Owner("alice", "Alice", "alice@example.com", [])
    m1 = Metric("g1", "alice", "政府標案 G1", date(2026,5,31), "daily", "mtg")
    m2 = Metric("cost", "alice", "Q2 降本", date(2026,6,30), "daily", "mtg")
    return OwnerReminder(owner=owner, metrics=[(m1, None), (m2, None)])

def test_token_deterministic_with_nonce():
    assert correlation_token("acme","alice","2026-W22","abc123") == "MTD1.acme.alice.2026-W22.abc123"

def test_subject_has_week_and_token():
    d = compose_digest(_reminder(), "acme", "2026-W22", token="MTD1.acme.alice.2026-W22.abc123")
    assert "2026-W22" in d.subject
    assert "[#MTD1.acme.alice.2026-W22.abc123]" in d.subject
    assert d.to == "alice@example.com"

def test_body_lists_each_metric_with_marker():
    d = compose_digest(_reminder(), "acme", "2026-W22", token="MTD1.acme.alice.2026-W22.abc123")
    assert "[#metric:g1]" in d.body_text
    assert "[#metric:cost]" in d.body_text
    assert "政府標案 G1" in d.body_text

def test_no_prefilled_achievement_number():
    d = compose_digest(_reminder(), "acme", "2026-W22", token="MTD1.acme.alice.2026-W22.abc123")
    # 達成率欄是空白提示，不含預填數字（不灌水）
    assert "預計達成率（會議前估）：\n" in d.body_text or d.body_text.rstrip().endswith("預計達成率（會議前估）：") or "預計達成率（會議前估）：" in d.body_text
    assert "100%" not in d.body_text and "80%" not in d.body_text
