"""Tests for the confirm command (C5 — digest-page batch confirmation)."""

from __future__ import annotations

import io
import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch

import pytest

from daily_agenda import ledger, state
from daily_agenda.cli import main
from daily_agenda.cli._common import Deps
from daily_agenda.confirm import (
    INPUT_BYTE_CAP,
    ParsedConfirm,
    parse_confirm_text,
    validate_confirm,
)
from daily_agenda.digest_script import DIGEST_SCRIPT
from tests.conftest import TAIPEI, NOW


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _deps(tmp_path: Path, **overrides: Any) -> Deps:
    return Deps(
        home=tmp_path,
        now_factory=lambda: NOW,
        plugin_root=Path(__file__).resolve().parents[2],
        **overrides,
    )


def _run_cli(argv: list[str], deps: Deps, capsys: Any, stdin_text: str = "") -> tuple[int, dict]:
    """Run the CLI with fake stdin and capture JSON output."""
    with patch("sys.stdin", io.StringIO(stdin_text)):
        code = main(argv, deps=deps)
    captured = capsys.readouterr()
    lines = [ln for ln in captured.out.strip().split("\n") if ln.strip()]
    obj = json.loads(lines[-1])
    return code, obj


def _setup_generation(tmp_path: Path, *, num_todos: int = 4) -> tuple[str, dict]:
    """Create a generation with report_code and ledger.

    Returns (report_code, report_index).
    """
    state.accept_consent(tmp_path, NOW)
    st = state.load_state(tmp_path)
    report_code = "R-ABC"
    st["report_code"] = report_code
    report_index: dict[str, str] = {}
    ldg_items: list[dict] = []

    for i in range(1, num_todos + 1):
        tid = f"T-{i:04d}"
        report_index[str(i)] = tid
        ldg_items.append({
            "id": tid,
            "title": f"Task {i}",
            "priority": "P1",
            "due": None,
            "status": "open",
            "src_ids": [f"mail:m{i}"],
            "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(),
            "maybe_done_since": None,
            "maybe_done_reason": None,
            "history": [],
        })

    st["report_index"] = report_index

    staging = state.stage_generation(
        tmp_path, state=st, ledger=ldg_items, outputs={"md": "# test"},
        publish_targets=[{"fmt": "md", "path": str(tmp_path / "output" / "test.md")}],
    )
    state.commit_generation(tmp_path, staging)
    return report_code, report_index


# ---------------------------------------------------------------------------
# Parser unit tests
# ---------------------------------------------------------------------------

class TestParseConfirmText:
    def test_full_block(self) -> None:
        text = "確認待辦 R-ABC\n完成：1、8\n刪除：6\n改優先：3→P0\n其餘保留\n"
        parsed = parse_confirm_text(text)
        assert parsed.report_code == "R-ABC"
        assert parsed.done_numbers == [1, 8]
        assert parsed.drop_numbers == [6]
        assert parsed.priority_changes == [(3, "P0")]

    def test_all_keep(self) -> None:
        parsed = parse_confirm_text("確認待辦 R-XYZ\n全部保留\n")
        assert parsed.report_code == "R-XYZ"
        assert parsed.done_numbers == []
        assert parsed.drop_numbers == []
        assert parsed.priority_changes == []

    def test_no_report_code(self) -> None:
        parsed = parse_confirm_text("確認待辦\n全部保留")
        assert parsed.report_code is None

    def test_ascii_colon_and_comma(self) -> None:
        text = "確認待辦 R-ABC\n完成:1,2,3\n其餘保留"
        parsed = parse_confirm_text(text)
        assert parsed.done_numbers == [1, 2, 3]

    def test_fullwidth_comma(self) -> None:
        text = "確認待辦 R-ABC\n完成：1，2，3\n其餘保留"
        parsed = parse_confirm_text(text)
        assert parsed.done_numbers == [1, 2, 3]

    def test_spaces_as_separator(self) -> None:
        text = "確認待辦 R-ABC\n完成：1 2 3\n其餘保留"
        parsed = parse_confirm_text(text)
        assert parsed.done_numbers == [1, 2, 3]

    def test_ascii_arrow(self) -> None:
        text = "確認待辦 R-ABC\n改優先：3->P0、5->P2\n其餘保留"
        parsed = parse_confirm_text(text)
        assert parsed.priority_changes == [(3, "P0"), (5, "P2")]

    def test_blank_lines_stripped(self) -> None:
        text = "\n\n確認待辦 R-ABC\n\n完成：1\n\n其餘保留\n\n"
        parsed = parse_confirm_text(text)
        assert parsed.done_numbers == [1]

    def test_leading_trailing_whitespace(self) -> None:
        text = "  確認待辦 R-ABC  \n  完成：1  \n  其餘保留  "
        parsed = parse_confirm_text(text)
        assert parsed.done_numbers == [1]

    def test_missing_optional_lines(self) -> None:
        """Only header and done line, no 其餘保留."""
        parsed = parse_confirm_text("確認待辦 R-ABC\n完成：5")
        assert parsed.done_numbers == [5]

    def test_empty_raises(self) -> None:
        with pytest.raises(ValueError, match="為空"):
            parse_confirm_text("")

    def test_bad_header_raises(self) -> None:
        with pytest.raises(ValueError, match="確認待辦"):
            parse_confirm_text("Hello\n完成：1")

    def test_unknown_line_raises(self) -> None:
        with pytest.raises(ValueError, match="格式不正確"):
            parse_confirm_text("確認待辦 R-ABC\n備註：something")

    def test_duplicate_key_raises(self) -> None:
        with pytest.raises(ValueError, match="重複"):
            parse_confirm_text("確認待辦 R-ABC\n完成：1\n完成：2")

    def test_non_integer_raises(self) -> None:
        with pytest.raises(ValueError, match="項目編號必須是數字"):
            parse_confirm_text("確認待辦 R-ABC\n完成：abc")

    def test_crlf_handled(self) -> None:
        text = "確認待辦 R-ABC\r\n完成：1、2\r\n其餘保留\r\n"
        parsed = parse_confirm_text(text)
        assert parsed.done_numbers == [1, 2]

    def test_bom_stripped_by_caller(self) -> None:
        """BOM is handled by the CLI layer (utf-8-sig), not the parser."""
        # The parser itself doesn't strip BOM, the CLI does.
        text = "﻿確認待辦 R-ABC\n全部保留"
        # With BOM, the header won't match — this is correct because
        # the CLI strips it before calling the parser.
        with pytest.raises(ValueError, match="確認待辦"):
            parse_confirm_text(text)


# ---------------------------------------------------------------------------
# Validation unit tests
# ---------------------------------------------------------------------------

class TestValidateConfirm:
    def _state_and_ledger(self) -> tuple[dict, list[dict]]:
        st = {
            "report_code": "R-ABC",
            "report_index": {"1": "T-0001", "2": "T-0002", "3": "T-0003"},
        }
        ldg = [
            {"id": "T-0001", "status": "open", "title": "Task 1", "priority": "P1",
             "src_ids": [], "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
             "due": None, "maybe_done_since": None, "maybe_done_reason": None, "history": []},
            {"id": "T-0002", "status": "open", "title": "Task 2", "priority": "P2",
             "src_ids": [], "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
             "due": None, "maybe_done_since": None, "maybe_done_reason": None, "history": []},
            {"id": "T-0003", "status": "maybe_done", "title": "Task 3", "priority": "P1",
             "src_ids": [], "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
             "due": None, "maybe_done_since": NOW.isoformat(), "maybe_done_reason": "looks done",
             "history": []},
        ]
        return st, ldg

    def test_valid_confirm(self) -> None:
        st, ldg = self._state_and_ledger()
        parsed = ParsedConfirm(report_code="R-ABC", done_numbers=[1], drop_numbers=[2])
        errors = validate_confirm(parsed, state=st, ledger=ldg)
        assert errors == []

    def test_unknown_number(self) -> None:
        st, ldg = self._state_and_ledger()
        parsed = ParsedConfirm(report_code="R-ABC", done_numbers=[99])
        errors = validate_confirm(parsed, state=st, ledger=ldg)
        assert len(errors) == 1
        assert errors[0].no == 99
        assert "不在目前報表" in errors[0].reason

    def test_cross_action_conflict(self) -> None:
        st, ldg = self._state_and_ledger()
        parsed = ParsedConfirm(
            report_code="R-ABC", done_numbers=[1], drop_numbers=[1],
        )
        errors = validate_confirm(parsed, state=st, ledger=ldg)
        assert any(e.no == 1 and "同時出現" in e.reason for e in errors)

    def test_dismiss_maybe_done_ok(self) -> None:
        """maybe_done items can be dismissed (刪除 means 不追了)."""
        st, ldg = self._state_and_ledger()
        parsed = ParsedConfirm(report_code="R-ABC", drop_numbers=[3])
        errors = validate_confirm(parsed, state=st, ledger=ldg)
        assert errors == []

    def test_dismiss_done_rejected(self) -> None:
        """done items cannot be dismissed."""
        st, ldg = self._state_and_ledger()
        ldg[0]["status"] = "done"
        parsed = ParsedConfirm(report_code="R-ABC", drop_numbers=[1])
        errors = validate_confirm(parsed, state=st, ledger=ldg)
        assert len(errors) == 1
        assert "無法刪除" in errors[0].reason

    def test_mark_done_maybe_done_ok(self) -> None:
        """maybe_done items can be marked done."""
        st, ldg = self._state_and_ledger()
        parsed = ParsedConfirm(report_code="R-ABC", done_numbers=[3])
        errors = validate_confirm(parsed, state=st, ledger=ldg)
        assert errors == []

    def test_priority_change_on_maybe_done_ok(self) -> None:
        st, ldg = self._state_and_ledger()
        parsed = ParsedConfirm(
            report_code="R-ABC", priority_changes=[(3, "P0")],
        )
        errors = validate_confirm(parsed, state=st, ledger=ldg)
        assert errors == []

    def test_within_line_duplicate(self) -> None:
        st, ldg = self._state_and_ledger()
        parsed = ParsedConfirm(
            report_code="R-ABC", done_numbers=[1, 1],
        )
        errors = validate_confirm(parsed, state=st, ledger=ldg)
        assert any("重複" in e.reason for e in errors)


# ---------------------------------------------------------------------------
# Ledger pure function tests (set_priority, dismiss)
# ---------------------------------------------------------------------------

class TestSetPriority:
    def test_set_priority_open(self) -> None:
        ldg = [{"id": "T-0001", "status": "open", "priority": "P1",
                "updated_at": NOW.isoformat()}]
        result = ledger.set_priority(ldg, "T-0001", "P0", now=NOW)
        assert result[0]["priority"] == "P0"
        assert result[0]["updated_at"] == NOW.isoformat()
        # Original not mutated.
        assert ldg[0]["priority"] == "P1"

    def test_set_priority_maybe_done(self) -> None:
        ldg = [{"id": "T-0001", "status": "maybe_done", "priority": "P2",
                "updated_at": NOW.isoformat()}]
        result = ledger.set_priority(ldg, "T-0001", "P1", now=NOW)
        assert result[0]["priority"] == "P1"

    def test_set_priority_done_rejected(self) -> None:
        ldg = [{"id": "T-0001", "status": "done", "priority": "P1",
                "updated_at": NOW.isoformat()}]
        with pytest.raises(ledger.IllegalTransition):
            ledger.set_priority(ldg, "T-0001", "P0", now=NOW)

    def test_set_priority_invalid(self) -> None:
        ldg = [{"id": "T-0001", "status": "open", "priority": "P1",
                "updated_at": NOW.isoformat()}]
        with pytest.raises(ValueError, match="invalid priority"):
            ledger.set_priority(ldg, "T-0001", "P9", now=NOW)

    def test_set_priority_not_found(self) -> None:
        with pytest.raises(KeyError):
            ledger.set_priority([], "T-9999", "P0", now=NOW)


class TestDismissPure:
    def test_dismiss_open(self) -> None:
        ldg = [{"id": "T-0001", "status": "open", "priority": "P1",
                "src_ids": [], "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(), "history": [],
                "maybe_done_since": None, "maybe_done_reason": None}]
        result = ledger.dismiss(ldg, "T-0001", now=NOW)
        assert result[0]["status"] == "dismissed"
        assert ldg[0]["status"] == "open"  # no mutation

    def test_dismiss_maybe_done(self) -> None:
        ldg = [{"id": "T-0001", "status": "maybe_done", "priority": "P1",
                "src_ids": [], "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(), "history": [],
                "maybe_done_since": NOW.isoformat(), "maybe_done_reason": "x"}]
        result = ledger.dismiss(ldg, "T-0001", now=NOW)
        assert result[0]["status"] == "dismissed"

    def test_dismiss_collapsed(self) -> None:
        ldg = [{"id": "T-0001", "status": "collapsed", "priority": "P1",
                "src_ids": [], "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(), "history": [],
                "maybe_done_since": NOW.isoformat(), "maybe_done_reason": "old"}]
        result = ledger.dismiss(ldg, "T-0001", now=NOW)
        assert result[0]["status"] == "dismissed"

    def test_dismiss_done_rejected(self) -> None:
        ldg = [{"id": "T-0001", "status": "done", "priority": "P1",
                "src_ids": [], "created_at": NOW.isoformat(),
                "updated_at": NOW.isoformat(), "history": []}]
        with pytest.raises(ledger.IllegalTransition):
            ledger.dismiss(ldg, "T-0001", now=NOW)


# ---------------------------------------------------------------------------
# CLI integration tests
# ---------------------------------------------------------------------------

class TestConfirmCLI:
    def test_confirm_full_flow(self, tmp_path: Path, capsys: Any) -> None:
        """Full flow: done + drop + priority change."""
        report_code, idx = _setup_generation(tmp_path)
        deps = _deps(tmp_path)
        text = f"確認待辦 {report_code}\n完成：1\n刪除：2\n改優先：3→P0\n其餘保留"
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is True
        assert obj["report_code"] == report_code
        assert len(obj["results"]) == 3
        actions = {r["action"] for r in obj["results"]}
        assert actions == {"done", "dismissed", "priority"}
        assert obj["unchanged_count"] == 1  # item 4 unchanged

    def test_confirm_all_keep(self, tmp_path: Path, capsys: Any) -> None:
        report_code, idx = _setup_generation(tmp_path)
        deps = _deps(tmp_path)
        text = f"確認待辦 {report_code}\n全部保留"
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is True
        assert obj["results"] == []
        assert obj["unchanged_count"] == len(idx)

    def test_confirm_stale_report(self, tmp_path: Path, capsys: Any) -> None:
        _setup_generation(tmp_path)
        deps = _deps(tmp_path)
        text = "確認待辦 R-OLD\n全部保留"
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is False
        assert obj["code"] == "STALE_REPORT"

    def test_confirm_no_code_when_state_has_one(self, tmp_path: Path, capsys: Any) -> None:
        """Missing report code in text → STALE_REPORT when state has one."""
        _setup_generation(tmp_path)
        deps = _deps(tmp_path)
        text = "確認待辦\n全部保留"
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is False
        assert obj["code"] == "STALE_REPORT"

    def test_confirm_no_generation_exists(self, tmp_path: Path, capsys: Any) -> None:
        """No generation at all → STALE_REPORT (state.report_code is None)."""
        state.accept_consent(tmp_path, NOW)
        deps = _deps(tmp_path)
        text = "確認待辦\n全部保留"
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is False
        assert obj["code"] == "STALE_REPORT"

    def test_confirm_invalid_unknown_number(self, tmp_path: Path, capsys: Any) -> None:
        report_code, _ = _setup_generation(tmp_path)
        deps = _deps(tmp_path)
        text = f"確認待辦 {report_code}\n完成：99"
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is False
        assert obj["code"] == "CONFIRM_INVALID"
        assert isinstance(obj["detail"], list)
        assert obj["detail"][0]["no"] == 99

    def test_confirm_invalid_cross_conflict(self, tmp_path: Path, capsys: Any) -> None:
        report_code, _ = _setup_generation(tmp_path)
        deps = _deps(tmp_path)
        text = f"確認待辦 {report_code}\n完成：1\n刪除：1"
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is False
        assert obj["code"] == "CONFIRM_INVALID"

    def test_confirm_no_flag(self, tmp_path: Path, capsys: Any) -> None:
        """confirm without --from-stdin → ARG_ERROR."""
        deps = _deps(tmp_path)
        code, obj = _run_cli(["confirm"], deps, capsys)
        assert code == 2
        assert obj["code"] == "ARG_ERROR"

    def test_confirm_bad_flag(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["confirm", "--file", "x"], deps, capsys)
        assert code == 2
        assert obj["code"] == "ARG_ERROR"

    def test_confirm_byte_cap(self, tmp_path: Path, capsys: Any) -> None:
        """Input exceeding byte cap → ARG_ERROR."""
        _setup_generation(tmp_path)
        deps = _deps(tmp_path)
        text = "確認待辦 R-ABC\n" + "完成：1\n" * 3000  # large
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is False
        assert obj["code"] == "ARG_ERROR"

    def test_confirm_results_titles_fenced(self, tmp_path: Path, capsys: Any) -> None:
        """Titles in results should be fenced (contain UNTRUSTED marker)."""
        report_code, _ = _setup_generation(tmp_path)
        deps = _deps(tmp_path)
        text = f"確認待辦 {report_code}\n完成：1\n其餘保留"
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is True
        title = obj["results"][0]["title"]
        assert "UNTRUSTED" in title

    def test_confirm_ledger_persisted(self, tmp_path: Path, capsys: Any) -> None:
        """After confirm, ledger reflects the changes."""
        report_code, _ = _setup_generation(tmp_path)
        deps = _deps(tmp_path)
        text = f"確認待辦 {report_code}\n完成：1\n刪除：2\n改優先：3→P0\n其餘保留"
        _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)

        ldg = state.load_ledger(tmp_path)
        by_id = {t["id"]: t for t in ldg}
        assert by_id["T-0001"]["status"] == "done"
        assert by_id["T-0002"]["status"] == "dismissed"
        assert by_id["T-0003"]["priority"] == "P0"
        assert by_id["T-0004"]["status"] == "open"  # unchanged

    def test_unchanged_count_excludes_done(self, tmp_path: Path, capsys: Any) -> None:
        """unchanged_count only counts open/maybe_done, not done/dismissed."""
        state.accept_consent(tmp_path, NOW)
        st = state.load_state(tmp_path)
        st["report_code"] = "R-ABC"
        st["report_index"] = {"1": "T-0001", "2": "T-0002", "3": "T-0003"}
        ldg = [
            {"id": "T-0001", "title": "Open", "priority": "P1", "due": None,
             "status": "open", "src_ids": [], "created_at": NOW.isoformat(),
             "updated_at": NOW.isoformat(), "maybe_done_since": None,
             "maybe_done_reason": None, "history": []},
            {"id": "T-0002", "title": "Already done", "priority": "P1", "due": None,
             "status": "done", "src_ids": [], "created_at": NOW.isoformat(),
             "updated_at": NOW.isoformat(), "maybe_done_since": None,
             "maybe_done_reason": None, "history": []},
            {"id": "T-0003", "title": "Maybe done", "priority": "P1", "due": None,
             "status": "maybe_done", "src_ids": [], "created_at": NOW.isoformat(),
             "updated_at": NOW.isoformat(), "maybe_done_since": NOW.isoformat(),
             "maybe_done_reason": "AI", "history": []},
        ]
        staging = state.stage_generation(
            tmp_path, state=st, ledger=ldg, outputs={"md": "# test"},
            publish_targets=[],
        )
        state.commit_generation(tmp_path, staging)
        deps = _deps(tmp_path)
        # Mark item 1 done; item 2 is already done (not visible); item 3 is maybe_done (visible)
        text = "確認待辦 R-ABC\n完成：1\n其餘保留"
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is True
        # visible = T-0001 (open) + T-0003 (maybe_done) = 2; touched = 1 → unchanged = 1
        assert obj["unchanged_count"] == 1

    def test_apply_exception_returns_confirm_invalid(self, tmp_path: Path, capsys: Any) -> None:
        """If an apply-time exception leaks through validation, return CONFIRM_INVALID."""
        # Create a generation where item 1 is already dismissed but report_index
        # still references it (simulating a race/stale scenario).
        state.accept_consent(tmp_path, NOW)
        st = state.load_state(tmp_path)
        st["report_code"] = "R-ABC"
        st["report_index"] = {"1": "T-0001"}
        ldg = [{
            "id": "T-0001", "title": "Task 1", "priority": "P1", "due": None,
            "status": "dismissed", "src_ids": [], "created_at": NOW.isoformat(),
            "updated_at": NOW.isoformat(), "maybe_done_since": None,
            "maybe_done_reason": None, "history": [],
        }]
        staging = state.stage_generation(
            tmp_path, state=st, ledger=ldg, outputs={"md": "# test"},
            publish_targets=[],
        )
        state.commit_generation(tmp_path, staging)
        deps = _deps(tmp_path)
        # Try to mark a dismissed item done — validation checks _TRANSITIONS
        # so it should catch this, but test the apply-loop catch too
        text = "確認待辦 R-ABC\n完成：1\n其餘保留"
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is False
        assert obj["code"] == "CONFIRM_INVALID"

    def test_confirm_drop_maybe_done(self, tmp_path: Path, capsys: Any) -> None:
        """刪除 on a maybe_done item succeeds (user says 不追了)."""
        state.accept_consent(tmp_path, NOW)
        st = state.load_state(tmp_path)
        st["report_code"] = "R-ABC"
        st["report_index"] = {"1": "T-0001"}
        ldg = [{
            "id": "T-0001", "title": "Maybe done task", "priority": "P1",
            "due": None, "status": "maybe_done", "src_ids": ["mail:m1"],
            "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
            "maybe_done_since": NOW.isoformat(), "maybe_done_reason": "AI thinks done",
            "history": [],
        }]
        staging = state.stage_generation(
            tmp_path, state=st, ledger=ldg, outputs={"md": "# test"},
            publish_targets=[],
        )
        state.commit_generation(tmp_path, staging)

        deps = _deps(tmp_path)
        text = "確認待辦 R-ABC\n刪除：1\n其餘保留"
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is True
        assert obj["results"][0]["action"] == "dismissed"

        ldg_after = state.load_ledger(tmp_path)
        assert ldg_after[0]["status"] == "dismissed"

    def test_da_dismiss_maybe_done(self, tmp_path: Path, capsys: Any) -> None:
        """DA dismiss --todo on maybe_done succeeds (same rule as confirm)."""
        state.accept_consent(tmp_path, NOW)
        st = state.load_state(tmp_path)
        st["report_code"] = "R-ABC"
        ldg = [{
            "id": "T-0001", "title": "Maybe done task", "priority": "P1",
            "due": None, "status": "maybe_done", "src_ids": ["mail:m1"],
            "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
            "maybe_done_since": NOW.isoformat(), "maybe_done_reason": "AI thinks done",
            "history": [],
        }]
        staging = state.stage_generation(
            tmp_path, state=st, ledger=ldg, outputs={"md": "# test"},
            publish_targets=[],
        )
        state.commit_generation(tmp_path, staging)

        deps = _deps(tmp_path)
        code, obj = _run_cli(["dismiss", "--todo", "T-0001"], deps, capsys)
        assert obj["ok"] is True
        assert obj["new_status"] == "dismissed"

        ldg_after = state.load_ledger(tmp_path)
        assert ldg_after[0]["status"] == "dismissed"


# ---------------------------------------------------------------------------
# Round-trip test: text produced by digest_script.py → parser → apply
# ---------------------------------------------------------------------------

class TestRoundTrip:
    """Build text in Python matching the JS join logic, verify the parser
    accepts it, and verify the JS source uses the same literals."""

    # The JS uses these Unicode literals to build the text.
    _JS_HEADER = "確認待辦"          # 確認待辦
    _JS_DONE = "完成"                # 完成
    _JS_DROP = "刪除"                # 刪除
    _JS_PRI = "改優先"               # 改優先
    _JS_COLON = "："                 # ：
    _JS_COMMA = "、"                 # 、
    _JS_ARROW = "→"                  # →
    _JS_REST = "其餘保留"            # 其餘保留
    _JS_ALL = "全部保留"             # 全部保留

    def _build_text(
        self,
        report_code: str = "R-ABC",
        done: list[int] | None = None,
        drop: list[int] | None = None,
        pri: list[tuple[int, str]] | None = None,
    ) -> str:
        """Build confirmation text matching the JS logic."""
        lines = [f"{self._JS_HEADER} {report_code}"]
        if done:
            lines.append(f"{self._JS_DONE}{self._JS_COLON}{self._JS_COMMA.join(str(n) for n in done)}")
        if drop:
            lines.append(f"{self._JS_DROP}{self._JS_COLON}{self._JS_COMMA.join(str(n) for n in drop)}")
        if pri:
            parts = [f"{n}{self._JS_ARROW}{p}" for n, p in pri]
            lines.append(f"{self._JS_PRI}{self._JS_COLON}{self._JS_COMMA.join(parts)}")
        if done or drop or pri:
            lines.append(self._JS_REST)
        else:
            lines.append(self._JS_ALL)
        return "\n".join(lines)

    def test_js_literals_present_in_digest_script(self) -> None:
        """The JS source must contain the same unicode escape sequences."""
        # Map each Python literal to its JS escape form.
        checks = {
            "header": ("\\u78ba\\u8a8d\\u5f85\\u8fa6", self._JS_HEADER),
            "done": ("\\u5b8c\\u6210", self._JS_DONE),
            "drop": ("\\u522a\\u9664", self._JS_DROP),
            "pri": ("\\u6539\\u512a\\u5148", self._JS_PRI),
            "colon": ("\\uff1a", self._JS_COLON),
            "comma": ("\\u3001", self._JS_COMMA),
            "arrow": ("\\u2192", self._JS_ARROW),
            "rest": ("\\u5176\\u9918\\u4fdd\\u7559", self._JS_REST),
            "all": ("\\u5168\\u90e8\\u4fdd\\u7559", self._JS_ALL),
        }
        for label, (js_esc, _py) in checks.items():
            assert js_esc in DIGEST_SCRIPT, (
                f"JS escape for '{label}' ({js_esc}) not found in DIGEST_SCRIPT — "
                f"the page and parser would produce different text"
            )

    def test_round_trip_full(self, tmp_path: Path, capsys: Any) -> None:
        """Text built from the same format as the JS → parse → apply → success."""
        report_code, idx = _setup_generation(tmp_path)
        deps = _deps(tmp_path)
        text = self._build_text(
            report_code=report_code,
            done=[1],
            drop=[2],
            pri=[(3, "P0")],
        )
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is True, f"confirm failed: {obj}"
        assert len(obj["results"]) == 3

    def test_round_trip_all_keep(self, tmp_path: Path, capsys: Any) -> None:
        report_code, _ = _setup_generation(tmp_path)
        deps = _deps(tmp_path)
        text = self._build_text(report_code=report_code)
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is True
        assert obj["results"] == []

    def test_round_trip_done_only(self, tmp_path: Path, capsys: Any) -> None:
        report_code, _ = _setup_generation(tmp_path)
        deps = _deps(tmp_path)
        text = self._build_text(report_code=report_code, done=[1, 3])
        code, obj = _run_cli(["confirm", "--from-stdin"], deps, capsys, stdin_text=text)
        assert obj["ok"] is True
        assert len(obj["results"]) == 2
        assert all(r["action"] == "done" for r in obj["results"])
