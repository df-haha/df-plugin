from __future__ import annotations
from mt_core.freshness import check_freshness

def test_no_checkpoint_is_fresh():
    ok, msg = check_freshness({}, "headsha", lambda a, b: True)
    assert ok is True and "首次" in msg

def test_recorded_commit_is_ancestor_fresh():
    state = {"last_human_reviewed": {"tracking_file_commit_sha": "rec"}}
    ok, msg = check_freshness(state, "head", is_ancestor_fn=lambda a, b: a == "rec" and b == "head")
    assert ok is True and msg == "fresh"

def test_recorded_commit_not_ancestor_is_stale():
    state = {"last_human_reviewed": {"tracking_file_commit_sha": "rec"}}
    ok, msg = check_freshness(state, "head", is_ancestor_fn=lambda a, b: False)
    assert ok is False and "落後" in msg
