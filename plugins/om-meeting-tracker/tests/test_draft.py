from __future__ import annotations
from dataclasses import dataclass, field
from datetime import date
from mt_core.config import Config, Owner, Metric, Paths, SendCfg
from mt_core.tracking import TrackedMetric
from mt_core.draft import render_draft

@dataclass
class Report:
    owner_id: str
    metric_ids: list[str]
    text: str

def _cfg():
    return Config(1, "acme", "Asia/Taipei", "monday", "wednesday",
                  ["mon","tue","wed","thu","fri"],
                  Paths("t.md","d/","c/","state/s.json","run-log/"),
                  SendCfg("n8n_webhook"),
                  [Owner("alice","Alice","a@x.com",[]), Owner("bob","Bob","b@x.com",[])],
                  [Metric("g1","alice","G1 標案",date(2026,5,31),"daily","mtg"),
                   Metric("cost","bob","Q2 降本",date(2026,6,30),"daily","mtg")])

def test_pending_block_when_no_reports():
    out = render_draft(_cfg(), [], "2026-W22", reports=[])
    assert "## ⚠️ 待回填" in out
    assert "G1 標案" in out and "Q2 降本" in out
    assert "⏳ 待會議" in out  # 達成率不灌水

def test_report_appears_once_and_idempotent():
    cfg = _cfg()
    reports = [Report("alice", ["g1"], "本週完成投標文件，等待開標")]
    a = render_draft(cfg, [], "2026-W22", reports=reports)
    b = render_draft(cfg, [], "2026-W22", reports=reports)
    assert a == b  # idempotent（regenerate）
    assert a.count("本週完成投標文件") == 1
    assert "(source: owner email)" in a

def test_rag_from_tracked():
    cfg = _cfg()
    tracked = [TrackedMetric("g1","alice","red",None,date(2026,5,31),{})]
    out = render_draft(cfg, tracked, "2026-W22", reports=[])
    assert "RAG：red" in out

def test_unassigned_report_listed():
    cfg = _cfg()
    reports = [Report("alice", [], "一些沒指定指標的回報")]
    out = render_draft(cfg, [], "2026-W22", reports=reports)
    assert "未指定指標的回報" in out
