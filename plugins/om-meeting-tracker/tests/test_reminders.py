# tests/test_reminders.py
from __future__ import annotations
from datetime import date
from mt_core.config import Config, Owner, Metric, Paths, SendCfg
from mt_core.timeutil import weekday_abbr
from mt_core.reminders import compute_reminders, should_remind

def _cfg(metrics, business_days=None):
    return Config(
        schema_version=1, tenant_id="acme", timezone="Asia/Taipei",
        week_start="monday", meeting_day="wednesday",
        business_days=business_days or ["mon","tue","wed","thu","fri"],
        paths=Paths("t.md","d/","c/","state/s.json","run-log/"),
        send=SendCfg("n8n_webhook"),
        owners=[Owner("alice","Alice","a@x.com",[]), Owner("bob","Bob","b@x.com",[])],
        metrics=metrics,
    )

def _m(mid, owner, cadence, deadline="2026-12-31"):
    return Metric(mid, owner, mid, date.fromisoformat(deadline), cadence, "mtg")

T = date(2026, 6, 15)  # 固定一天，星期幾由 weekday_abbr 推導

def test_daily_always():
    cfg = _cfg([_m("m1","alice","daily")])
    assert should_remind(cfg.metrics[0], None, T, cfg, {"metric_last_nudge":{}}) is True

def test_business_days_skips_non_business_day():
    today_abbr = weekday_abbr(T)
    bdays = [d for d in ["mon","tue","wed","thu","fri","sat","sun"] if d != today_abbr]
    cfg = _cfg([_m("m1","alice","business_days")], business_days=bdays)
    assert should_remind(cfg.metrics[0], None, T, cfg, {"metric_last_nudge":{}}) is False

def test_overdue_only_far_deadline_skips():
    cfg = _cfg([_m("m1","alice","overdue_only","2026-12-31")])
    assert should_remind(cfg.metrics[0], None, T, cfg, {"metric_last_nudge":{}}) is False

def test_overdue_only_overdue_reminds():
    cfg = _cfg([_m("m1","alice","overdue_only","2026-06-01")])
    assert should_remind(cfg.metrics[0], None, T, cfg, {"metric_last_nudge":{}}) is True

def test_snooze_future_skips():
    cfg = _cfg([_m("m1","alice","snooze:2026-07-01","2026-12-31")])
    assert should_remind(cfg.metrics[0], None, T, cfg, {"metric_last_nudge":{}}) is False

def test_snooze_past_reminds():
    cfg = _cfg([_m("m1","alice","snooze:2026-06-01","2026-12-31")])
    assert should_remind(cfg.metrics[0], None, T, cfg, {"metric_last_nudge":{}}) is True

def test_red_rag_forces_remind_even_on_non_business_day():
    from mt_core.tracking import TrackedMetric
    today_abbr = weekday_abbr(T)
    bdays = [d for d in ["mon","tue","wed","thu","fri","sat","sun"] if d != today_abbr]
    cfg = _cfg([_m("m1","alice","business_days","2026-12-31")], business_days=bdays)
    tm = TrackedMetric("m1","alice","red",None,date(2026,12,31),{})
    assert should_remind(cfg.metrics[0], tm, T, cfg, {"metric_last_nudge":{}}) is True

def test_near_deadline_forces_remind():
    cfg = _cfg([_m("m1","alice","overdue_only","2026-06-16")])  # T+1
    assert should_remind(cfg.metrics[0], None, T, cfg, {"metric_last_nudge":{}}) is True

def test_stale_nudge_forces_remind():
    cfg = _cfg([_m("m1","alice","overdue_only","2026-12-31")])
    state = {"metric_last_nudge": {"m1": "2026-06-10"}}  # 5 天前
    assert should_remind(cfg.metrics[0], None, T, cfg, state) is True

def test_compute_groups_by_owner_and_skips_empty():
    cfg = _cfg([_m("m1","alice","daily"), _m("m2","bob","overdue_only","2026-12-31")])
    rems = compute_reminders(cfg, [], T, {"metric_last_nudge":{}})
    # alice daily→催；bob overdue_only 遠 deadline→不催；故只有 alice
    assert [r.owner.owner_id for r in rems] == ["alice"]
    assert [m.metric_id for m,_ in rems[0].metrics] == ["m1"]
