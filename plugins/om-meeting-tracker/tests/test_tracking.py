# tests/test_tracking.py
from __future__ import annotations
from datetime import date
from mt_core.tracking import parse_metrics, TrackedMetric

MD = """\
# 追蹤表
| 指標 | owner |
|------|-------|
| G1 | Haha |
<!-- mt:metric id=g1-tender owner=haha rag=red achieved= deadline=2026-05-31 -->
<!-- mt:metric id=cost-q2 owner=hsin-ping rag=green achieved=80% deadline=2026-06-30 -->
"""

def test_parse_two_metrics():
    ms = parse_metrics(MD)
    assert [m.metric_id for m in ms] == ["g1-tender", "cost-q2"]

def test_empty_achieved_is_none():
    ms = parse_metrics(MD)
    assert ms[0].achieved is None
    assert ms[0].rag == "red"
    assert ms[1].achieved == "80%"

def test_deadline_parsed():
    ms = parse_metrics(MD)
    assert ms[0].deadline == date(2026, 5, 31)

def test_no_anchor_returns_empty():
    assert parse_metrics("# 沒有 anchor\n") == []
