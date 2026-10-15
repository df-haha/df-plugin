"""Tests for daily_agenda.ledger — todo ledger pure functions."""

from __future__ import annotations

import copy
from datetime import datetime, timedelta, timezone

import pytest

from daily_agenda.ledger import (
    IllegalTransition,
    StaleReport,
    apply_model,
    auto_collapse,
    carry_days,
    mark_done,
    new_report_code,
    new_todo_id,
    restore,
)

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 8, 9, 0, 0, tzinfo=TZ)


def _todo(
    tid: str = "T-0001",
    status: str = "open",
    priority: str = "P1",
    src_ids: list[str] | None = None,
    created_at: str | None = None,
    maybe_done_since: str | None = None,
) -> dict:
    return {
        "id": tid,
        "title": "Test todo",
        "priority": priority,
        "due": None,
        "status": status,
        "src_ids": src_ids or ["mail:AAA"],
        "created_at": created_at or NOW.isoformat(),
        "updated_at": NOW.isoformat(),
        "maybe_done_since": maybe_done_since,
        "maybe_done_reason": None,
        "history": [],
    }


# ---------------------------------------------------------------------------
# new_todo_id / new_report_code
# ---------------------------------------------------------------------------

class TestIdGeneration:
    def test_new_todo_id_format(self) -> None:
        tid = new_todo_id(set())
        assert tid.startswith("T-")
        assert len(tid) == 6  # T- + 4 digits

    def test_new_todo_id_unique(self) -> None:
        existing = {f"T-{i:04d}" for i in range(50)}
        tid = new_todo_id(existing)
        assert tid not in existing
        assert tid.startswith("T-")

    def test_new_todo_id_exhausted(self, monkeypatch: pytest.MonkeyPatch) -> None:
        """When random always returns the same collision, raises RuntimeError."""
        import random as _random

        monkeypatch.setattr(_random, "choices", lambda *a, **kw: ["0", "0", "0", "0"])
        with pytest.raises(RuntimeError, match="failed to generate"):
            new_todo_id({"T-0000"})

    def test_new_report_code_format(self) -> None:
        code = new_report_code()
        assert code.startswith("R-")
        assert len(code) == 5  # R- + 3 chars
        # No ambiguous characters.
        for c in code[2:]:
            assert c not in "01OIL"



# ---------------------------------------------------------------------------
# carry_days
# ---------------------------------------------------------------------------

class TestCarryDays:
    def test_same_day(self) -> None:
        t = _todo(created_at=NOW.isoformat())
        assert carry_days(t, now=NOW) == 0

    def test_one_day(self) -> None:
        yesterday = (NOW - timedelta(days=1)).isoformat()
        t = _todo(created_at=yesterday)
        assert carry_days(t, now=NOW) == 1

    def test_cross_midnight(self) -> None:
        """Created at 23:00, now is 01:00 next day — should be 1 day."""
        created = datetime(2026, 10, 7, 23, 0, 0, tzinfo=TZ)
        now = datetime(2026, 10, 8, 1, 0, 0, tzinfo=TZ)
        t = _todo(created_at=created.isoformat())
        assert carry_days(t, now=now) == 1


# ---------------------------------------------------------------------------
# apply_model
# ---------------------------------------------------------------------------

class TestApplyModel:
    def test_new_item(self) -> None:
        items = [{"action": "new", "title": "Task A", "priority": "P0",
                  "due": "2026-10-10", "src_ids": ["mail:BBB"], "reason": "urgent"}]
        seq = [0]

        def id_factory(existing):
            seq[0] += 1
            return f"T-{seq[0]:04d}"

        result = apply_model([], items, now=NOW, id_factory=id_factory)
        assert len(result) == 1
        assert result[0]["id"] == "T-0001"
        assert result[0]["title"] == "Task A"
        assert result[0]["status"] == "open"
        assert result[0]["priority"] == "P0"
        assert result[0]["due"] == "2026-10-10"

    def test_update_item(self) -> None:
        ledger = [_todo(tid="T-4821", priority="P1")]
        items = [{"action": "update", "todo_id": "T-4821", "priority": "P0",
                  "due": "2026-10-15", "src_ids": ["mail:BBB"], "reason": "escalated"}]
        result = apply_model(ledger, items, now=NOW)
        assert result[0]["priority"] == "P0"
        assert result[0]["due"] == "2026-10-15"
        assert "mail:BBB" in result[0]["src_ids"]
        assert "mail:AAA" in result[0]["src_ids"]

    def test_maybe_done_from_open(self) -> None:
        ledger = [_todo(tid="T-4821")]
        items = [{"action": "maybe_done", "todo_id": "T-4821",
                  "src_ids": ["mail:BBB"], "reason": "confirmed"}]
        result = apply_model(ledger, items, now=NOW)
        assert result[0]["status"] == "maybe_done"
        assert result[0]["maybe_done_since"] is not None
        assert result[0]["maybe_done_reason"] == "confirmed"
        assert "mail:BBB" in result[0]["src_ids"]

    def test_maybe_done_already_maybe_done_merges(self) -> None:
        ledger = [_todo(
            tid="T-4821", status="maybe_done",
            maybe_done_since=NOW.isoformat(),
            src_ids=["mail:AAA"],
        )]
        items = [{"action": "maybe_done", "todo_id": "T-4821",
                  "src_ids": ["mail:BBB"], "reason": "more evidence"}]
        result = apply_model(ledger, items, now=NOW)
        assert result[0]["status"] == "maybe_done"
        assert "mail:AAA" in result[0]["src_ids"]
        assert "mail:BBB" in result[0]["src_ids"]
        assert result[0]["maybe_done_reason"] == "more evidence"

    def test_new_overlapping_src_ids_treated_as_update(self) -> None:
        """new item whose src_ids overlap with existing open todo → merge."""
        ledger = [_todo(tid="T-4821", src_ids=["mail:AAA"], priority="P1")]
        items = [{"action": "new", "title": "Dup task", "priority": "P0",
                  "due": None, "src_ids": ["mail:AAA", "mail:BBB"], "reason": "dup"}]
        result = apply_model(ledger, items, now=NOW)
        assert len(result) == 1  # no new todo created
        assert result[0]["id"] == "T-4821"
        assert result[0]["title"] == "Test todo"  # keeps existing title
        assert result[0]["priority"] == "P0"  # updated
        assert "mail:BBB" in result[0]["src_ids"]  # merged

    def test_skip_does_nothing(self) -> None:
        ledger = [_todo()]
        items = [{"action": "skip", "src_ids": ["mail:CCC"], "reason": "not actionable"}]
        result = apply_model(ledger, items, now=NOW)
        assert len(result) == 1
        assert result[0] == ledger[0]  # deep-copied but equal

    def test_input_not_mutated(self) -> None:
        ledger = [_todo(tid="T-4821")]
        original = copy.deepcopy(ledger)
        items = [{"action": "update", "todo_id": "T-4821", "priority": "P0",
                  "due": None, "src_ids": [], "reason": "x"}]
        apply_model(ledger, items, now=NOW)
        assert ledger == original


# ---------------------------------------------------------------------------
# auto_collapse
# ---------------------------------------------------------------------------

class TestAutoCollapse:
    def test_collapse_after_7_days(self) -> None:
        seven_days_ago = (NOW - timedelta(days=7)).isoformat()
        ledger = [_todo(status="maybe_done", maybe_done_since=seven_days_ago)]
        result = auto_collapse(ledger, now=NOW)
        assert result[0]["status"] == "collapsed"
        assert result[0]["history"][-1]["by"] == "system"

    def test_no_collapse_before_7_days(self) -> None:
        six_days_ago = (NOW - timedelta(days=6)).isoformat()
        ledger = [_todo(status="maybe_done", maybe_done_since=six_days_ago)]
        result = auto_collapse(ledger, now=NOW)
        assert result[0]["status"] == "maybe_done"

    def test_only_maybe_done_collapsed(self) -> None:
        seven_days_ago = (NOW - timedelta(days=7)).isoformat()
        ledger = [
            _todo(tid="T-0001", status="open"),
            _todo(tid="T-0002", status="maybe_done", maybe_done_since=seven_days_ago),
            _todo(tid="T-0003", status="done"),
        ]
        result = auto_collapse(ledger, now=NOW)
        assert result[0]["status"] == "open"
        assert result[1]["status"] == "collapsed"
        assert result[2]["status"] == "done"

    def test_input_not_mutated(self) -> None:
        seven_days_ago = (NOW - timedelta(days=7)).isoformat()
        ledger = [_todo(status="maybe_done", maybe_done_since=seven_days_ago)]
        original = copy.deepcopy(ledger)
        auto_collapse(ledger, now=NOW)
        assert ledger == original


# ---------------------------------------------------------------------------
# mark_done
# ---------------------------------------------------------------------------

class TestMarkDone:
    def test_by_todo_id(self) -> None:
        ledger = [_todo(tid="T-4821", status="open")]
        state = {"report_code": "R-7K2", "report_index": {"1": "T-4821"}}
        new_ledger, new_state = mark_done(
            ledger, state, report_code=None, todo_ids=["T-4821"], now=NOW,
        )
        assert new_ledger[0]["status"] == "done"
        assert new_ledger[0]["history"][-1]["by"] == "user"

    def test_by_number(self) -> None:
        ledger = [_todo(tid="T-4821")]
        state = {"report_code": "R-7K2", "report_index": {"1": "T-4821"}}
        new_ledger, _ = mark_done(
            ledger, state, report_code="R-7K2", numbers=[1], now=NOW,
        )
        assert new_ledger[0]["status"] == "done"

    def test_stale_report_code(self) -> None:
        ledger = [_todo()]
        state = {"report_code": "R-7K2", "report_index": {}}
        with pytest.raises(StaleReport):
            mark_done(ledger, state, report_code="R-OLD", numbers=[1], now=NOW)

    def test_no_report_code_in_state(self) -> None:
        ledger = [_todo()]
        state = {"report_code": None, "report_index": {}}
        with pytest.raises(StaleReport):
            mark_done(ledger, state, report_code="R-7K2", numbers=[1], now=NOW)

    def test_number_not_in_index(self) -> None:
        ledger = [_todo()]
        state = {"report_code": "R-7K2", "report_index": {"1": "T-0001"}}
        with pytest.raises(StaleReport):
            mark_done(ledger, state, report_code="R-7K2", numbers=[99], now=NOW)

    def test_maybe_done_to_done(self) -> None:
        ledger = [_todo(tid="T-4821", status="maybe_done")]
        state = {"report_code": "R-7K2", "report_index": {}}
        new_ledger, _ = mark_done(
            ledger, state, report_code=None, todo_ids=["T-4821"], now=NOW,
        )
        assert new_ledger[0]["status"] == "done"

    def test_collapsed_to_done(self) -> None:
        ledger = [_todo(tid="T-4821", status="collapsed")]
        state = {"report_code": "R-7K2", "report_index": {}}
        new_ledger, _ = mark_done(
            ledger, state, report_code=None, todo_ids=["T-4821"], now=NOW,
        )
        assert new_ledger[0]["status"] == "done"

    def test_input_not_mutated(self) -> None:
        ledger = [_todo(tid="T-4821")]
        state = {"report_code": "R-7K2", "report_index": {"1": "T-4821"}}
        original_ledger = copy.deepcopy(ledger)
        original_state = copy.deepcopy(state)
        mark_done(ledger, state, report_code="R-7K2", numbers=[1], now=NOW)
        assert ledger == original_ledger
        assert state == original_state


# ---------------------------------------------------------------------------
# restore
# ---------------------------------------------------------------------------

class TestRestore:
    def test_restore_collapsed(self) -> None:
        ledger = [_todo(tid="T-4821", status="collapsed",
                        maybe_done_since=NOW.isoformat())]
        result = restore(ledger, "T-4821", now=NOW)
        assert result[0]["status"] == "open"
        assert result[0]["maybe_done_since"] is None
        assert result[0]["maybe_done_reason"] is None
        assert result[0]["history"][-1]["by"] == "user"

    def test_restore_not_collapsed(self) -> None:
        ledger = [_todo(tid="T-4821", status="open")]
        with pytest.raises(IllegalTransition):
            restore(ledger, "T-4821", now=NOW)

    def test_restore_not_found(self) -> None:
        with pytest.raises(KeyError):
            restore([], "T-9999", now=NOW)

    def test_input_not_mutated(self) -> None:
        ledger = [_todo(tid="T-4821", status="collapsed",
                        maybe_done_since=NOW.isoformat())]
        original = copy.deepcopy(ledger)
        restore(ledger, "T-4821", now=NOW)
        assert ledger == original


# ---------------------------------------------------------------------------
# Transition validation — illegal transitions
# ---------------------------------------------------------------------------

class TestIllegalTransitions:
    """Non-allowed transitions raise IllegalTransition; unknown IDs raise KeyError."""

    def test_done_to_done_raises(self) -> None:
        ledger = [_todo(tid="T-4821", status="done")]
        state = {"report_code": "R-7K2", "report_index": {}}
        with pytest.raises(IllegalTransition):
            mark_done(ledger, state, report_code=None, todo_ids=["T-4821"], now=NOW)

    def test_dismissed_to_done_raises(self) -> None:
        ledger = [_todo(tid="T-4821", status="dismissed")]
        state = {"report_code": "R-7K2", "report_index": {}}
        with pytest.raises(IllegalTransition):
            mark_done(ledger, state, report_code=None, todo_ids=["T-4821"], now=NOW)

    def test_unknown_todo_id_raises(self) -> None:
        state = {"report_code": "R-7K2", "report_index": {}}
        with pytest.raises(KeyError):
            mark_done([], state, report_code=None, todo_ids=["T-9999"], now=NOW)

    def test_no_report_code_with_numbers_raises(self) -> None:
        """Passing numbers without report_code should raise StaleReport."""
        ledger = [_todo(tid="T-4821")]
        state = {"report_code": "R-7K2", "report_index": {"1": "T-4821"}}
        with pytest.raises(StaleReport):
            mark_done(ledger, state, report_code=None, numbers=[1], now=NOW)


# ---------------------------------------------------------------------------
# AS-11: carry days, maybe_done confirm, 7-day collapse, restore
# ---------------------------------------------------------------------------

class TestAS11FullCycle:
    """AS-11 acceptance scenario: carry → maybe_done → confirm or collapse → restore."""

    def test_full_lifecycle(self) -> None:
        # Day 1: new todo.
        day1 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=TZ)
        items = [{"action": "new", "title": "Respond to client", "priority": "P1",
                  "src_ids": ["mail:AAA"], "reason": "request"}]
        ledger = apply_model([], items, now=day1, id_factory=lambda _: "T-0001")
        assert carry_days(ledger[0], now=day1) == 0

        # Day 3: carry_days = 2; model marks maybe_done.
        day3 = datetime(2026, 10, 3, 9, 0, 0, tzinfo=TZ)
        assert carry_days(ledger[0], now=day3) == 2
        items2 = [{"action": "maybe_done", "todo_id": "T-0001",
                   "src_ids": ["mail:BBB"], "reason": "reply sent"}]
        ledger = apply_model(ledger, items2, now=day3)
        assert ledger[0]["status"] == "maybe_done"

        # Day 3: user confirms done.
        state = {"report_code": "R-AAA", "report_index": {}}
        ledger, _ = mark_done(
            ledger, state, report_code=None, todo_ids=["T-0001"], now=day3,
        )
        assert ledger[0]["status"] == "done"

    def test_collapse_and_restore(self) -> None:
        # maybe_done on day 1.
        day1 = datetime(2026, 10, 1, 9, 0, 0, tzinfo=TZ)
        ledger = [_todo(tid="T-0001", status="maybe_done",
                        maybe_done_since=day1.isoformat())]

        # Day 8: auto_collapse triggers.
        day8 = datetime(2026, 10, 8, 9, 0, 0, tzinfo=TZ)
        ledger = auto_collapse(ledger, now=day8)
        assert ledger[0]["status"] == "collapsed"

        # User restores.
        ledger = restore(ledger, "T-0001", now=day8)
        assert ledger[0]["status"] == "open"
        assert ledger[0]["maybe_done_since"] is None
