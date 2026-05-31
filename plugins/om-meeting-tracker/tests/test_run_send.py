# tests/test_run_send.py
from __future__ import annotations
from datetime import date
from mt_core.config import Config, Owner, Metric, Paths, SendCfg
from mt_core.reminders import compute_reminders
from mt_core.state import default_state, reminder_key, record_sent
from mt_core.timeutil import iso_week_str
from mt_core.send_adapters import SendResult
from mt_core.run import send_digests

class FakeAdapter:
    def __init__(self): self.sent = []
    def send(self, *, to, subject, body_text, body_html, idempotency_key):
        self.sent.append((to, idempotency_key))
        return SendResult(provider_message_id=f"fake-{len(self.sent)}")

def _cfg():
    return Config(1, "acme", "Asia/Taipei", "monday", "wednesday",
                  ["mon","tue","wed","thu","fri"],
                  Paths("t.md","d/","c/","state/s.json","run-log/"), SendCfg("n8n_webhook"),
                  [Owner("alice","Alice","alice@example.com",[])],
                  [Metric("g1","alice","G1",date(2026,5,31),"daily","mtg")])

def test_send_then_same_day_skip():
    cfg = _cfg(); st = default_state("acme"); today = date(2026,5,29)
    rems = compute_reminders(cfg, [], today, st)
    a = FakeAdapter()
    s1 = send_digests(cfg, rems, st, a, today)
    s2 = send_digests(cfg, rems, st, a, today)
    assert s1["sent"] == 1
    assert s2["sent"] == 0 and s2["skipped"] == 1
    assert len(a.sent) == 1                       # 第二次沒再寄
    key = reminder_key("acme", "digest:alice", iso_week_str(today), "2026-05-29")
    assert st["sent_reminders"][key]["status"] == "sent"
    assert st["metric_last_nudge"]["g1"] == "2026-05-29"

def test_pending_from_crash_resends_safely():
    # 上次「pre-write pending 後 crash」（未標 sent）→ 本次應重送（adapter 端靠 idempotency_key 去重）
    cfg = _cfg(); st = default_state("acme"); today = date(2026,5,29)
    rems = compute_reminders(cfg, [], today, st)
    key = reminder_key("acme", "digest:alice", iso_week_str(today), "2026-05-29")
    record_sent(st, key, owner_id="alice", status="pending", idempotency_key=key)
    a = FakeAdapter()
    s = send_digests(cfg, rems, st, a, today)
    assert s["sent"] == 1 and len(a.sent) == 1          # pending 會重送
    assert a.sent[0][1] == key                          # 帶同一 idempotency_key（寄送端去重）
    assert st["sent_reminders"][key]["status"] == "sent"

def test_dry_run_does_not_send_or_record():
    cfg = _cfg(); st = default_state("acme"); today = date(2026,5,29)
    rems = compute_reminders(cfg, [], today, st)
    a = FakeAdapter()
    s = send_digests(cfg, rems, st, a, today, dry_run=True)
    assert a.sent == [] and st["sent_reminders"] == {}
    assert s["owners"][0]["to_masked"] == "a***@example.com"
