"""Tests for the ``run`` command pipeline."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from daily_agenda import criteria, state
from daily_agenda.analyze import AnalyzerResult
from daily_agenda.cli import main
from daily_agenda.cli._common import Deps
from tests.conftest import FakeGraph, FakeLine, make_line_chat, make_line_message, make_mail_item, make_calendar_item

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 8, 9, 0, 0, tzinfo=TAIPEI)


def _setup_home(tmp_path: Path) -> Path:
    """Create a minimal configured home."""
    state.accept_consent(tmp_path, NOW)
    cfg = state.load_config(tmp_path)
    cfg["line"]["enabled"] = False
    cfg["mail"]["enabled"] = True
    cfg["output"]["dir"] = str(tmp_path / "output")
    cfg["output"]["formats"] = ["md"]
    state.save_config(tmp_path, cfg)

    # Save a criteria version
    criteria.save(tmp_path, "# Test criteria\nAccept all items as P1.")

    return tmp_path


def _fake_analyzer_result() -> dict[str, Any]:
    return {
        "schema_version": 1,
        "items": [
            {
                "action": "new",
                "title": "Follow up on meeting",
                "priority": "P1",
                "due": None,
                "src_ids": ["mail:AAMk-test-001"],
                "reason": "Boss asked to follow up",
            }
        ],
    }


def _deps(tmp_path: Path, *, with_line: bool = False) -> Deps:
    fake_graph = FakeGraph(
        mail_items=[make_mail_item()],
        calendar_items=[make_calendar_item()],
    )

    line_factory = None
    if with_line:
        fake_line = FakeLine(
            chats=[make_line_chat()],
            messages=[make_line_message()],
        )
        line_factory = lambda **kw: fake_line

    return Deps(
        home=tmp_path,
        now_factory=lambda: NOW,
        plugin_root=Path(__file__).resolve().parents[2],
        graph_factory=lambda: fake_graph,
        line_source_factory=line_factory,
    )


def _run(argv: list[str], deps: Deps, capsys: Any) -> tuple[int, dict]:
    code = main(argv, deps=deps)
    captured = capsys.readouterr()
    lines = captured.out.strip().split("\n")
    obj = json.loads(lines[-1])
    return code, obj


class TestRunPipeline:
    def test_consent_required(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run(["run"], deps, capsys)
        assert obj["code"] == "CONSENT_REQUIRED"

    def test_successful_run(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        deps = _deps(h)

        with patch("daily_agenda.analyze.run_analyzer") as mock_analyzer:
            mock_analyzer.return_value = AnalyzerResult(output=_fake_analyzer_result(), ms=0)
            code, obj = _run(["run"], deps, capsys)

        assert obj["ok"] is True
        assert "report_code" in obj
        assert obj["report_code"].startswith("R-")
        assert len(obj["outputs"]) > 0
        assert "sources" in obj
        assert "counts" in obj
        assert isinstance(obj["rejected_items"], int)
        assert "needs" in obj

    def test_run_creates_generation(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        deps = _deps(h)

        with patch("daily_agenda.analyze.run_analyzer") as mock_analyzer:
            mock_analyzer.return_value = AnalyzerResult(output=_fake_analyzer_result(), ms=0)
            _run(["run"], deps, capsys)

        assert state.current_generation(h) == 1
        gen_dir = h / "gen" / "1"
        assert gen_dir.exists()
        assert (gen_dir / "state.json").exists()
        assert (gen_dir / "ledger.jsonl").exists()

    def test_run_no_raw_content(self, tmp_path: Path, capsys: Any) -> None:
        """Run output must not contain raw source content (non-★ rule)."""
        h = _setup_home(tmp_path)
        SENTINEL = "SENTINEL_PRIVATE_DATA_xyz"

        fake_graph = FakeGraph(
            mail_items=[make_mail_item(subject=SENTINEL)],
            mail_full={"AAMk-test-001": {
                "id": "AAMk-test-001",
                "subject": SENTINEL,
                "from": SENTINEL,
                "to": "me@test.com",
                "received_at": NOW.isoformat(),
                "internet_message_id": "<test@test.com>",
                "body": SENTINEL,
            }},
        )
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: fake_graph,
        )

        with patch("daily_agenda.analyze.run_analyzer") as mock_analyzer:
            mock_analyzer.return_value = AnalyzerResult(output={"schema_version": 1, "items": []}, ms=0)
            code, obj = _run(["run"], deps, capsys)

        # The JSON output must not contain the sentinel
        output_str = json.dumps(obj, ensure_ascii=False)
        assert SENTINEL not in output_str

    def test_analyzer_failure(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        deps = _deps(h)

        with patch("daily_agenda.analyze.run_analyzer") as mock_analyzer:
            from daily_agenda.analyze import AnalyzerError
            mock_analyzer.side_effect = AnalyzerError("ANALYZER_TIMEOUT")
            code, obj = _run(["run"], deps, capsys)

        assert obj["ok"] is False
        assert obj["code"] == "ANALYZER_TIMEOUT"

    def test_noninteractive_line_unverified(self, tmp_path: Path, capsys: Any) -> None:
        """Noninteractive with unverified LINE: mail still collected, LINE skipped."""
        h = _setup_home(tmp_path)
        cfg = state.load_config(h)
        cfg["line"]["enabled"] = True
        cfg["line"]["main_database"] = "test.edb"
        state.save_config(h, cfg)

        # LINE source that returns LINE_BUILD_UNVERIFIED
        fake_line = FakeLine(probe_ok=False, probe_code="LINE_BUILD_UNVERIFIED")
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(
                mail_items=[make_mail_item()],
                calendar_items=[make_calendar_item()],
            ),
            line_source_factory=lambda **kw: fake_line,
            noninteractive=True,
        )

        with patch("daily_agenda.analyze.run_analyzer") as mock_analyzer:
            mock_analyzer.return_value = AnalyzerResult(output={"schema_version": 1, "items": []}, ms=0)
            code, obj = _run(["run", "--noninteractive"], deps, capsys)

        assert obj["ok"] is True
        assert obj["needs"]["line_trust"] is True
        assert obj["sources"]["line"]["status"] == "failed"

    def test_interactive_line_unverified_early_return(self, tmp_path: Path, capsys: Any) -> None:
        """Interactive with unverified LINE: early return with needs.line_trust."""
        h = _setup_home(tmp_path)
        cfg = state.load_config(h)
        cfg["line"]["enabled"] = True
        cfg["line"]["main_database"] = "test.edb"
        state.save_config(h, cfg)

        fake_line = FakeLine(probe_ok=False, probe_code="LINE_BUILD_UNVERIFIED")
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(),
            line_source_factory=lambda **kw: fake_line,
        )

        code, obj = _run(["run"], deps, capsys)
        assert obj["ok"] is False
        assert obj["code"] == "LINE_BUILD_UNVERIFIED"
        assert obj["needs"]["line_trust"] is True

    def test_locked(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        deps = _deps(h)

        # Create a non-stale lock using the real current time
        lock_dir = h / "runs"
        lock_dir.mkdir(parents=True, exist_ok=True)
        real_now = datetime.now().astimezone().isoformat()
        lock_data = {"pid": 99999, "at": real_now}
        (lock_dir / "lock").write_text(json.dumps(lock_data), encoding="utf-8")

        code, obj = _run(["run"], deps, capsys)
        assert obj["code"] == "LOCKED"


# ---------------------------------------------------------------------------
# Retry + digest merge tests (C1, C2)
# ---------------------------------------------------------------------------

class TestRetryAndDigestMerge:
    def test_single_batch_retry_on_parse_failure(self, tmp_path: Path, capsys: Any) -> None:
        """MODEL_OUTPUT_INVALID on first attempt → retry once → succeed."""
        h = _setup_home(tmp_path)
        deps = _deps(h)

        good_result = {
            "schema_version": 2,
            "items": [],
            "chat_digests": [],
            "mail_digests": [],
        }

        call_count = 0

        def _side_effect(*args: Any, **kwargs: Any) -> AnalyzerResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                from daily_agenda.analyze import AnalyzerError
                raise AnalyzerError("MODEL_OUTPUT_INVALID")
            return AnalyzerResult(output=good_result, ms=0)

        with patch("daily_agenda.analyze.run_analyzer", side_effect=_side_effect):
            code, obj = _run(["run"], deps, capsys)

        assert obj["ok"] is True
        assert call_count == 2  # First attempt + one retry

    def test_retry_not_on_other_errors(self, tmp_path: Path, capsys: Any) -> None:
        """Non-parse errors (e.g. ANALYZER_TIMEOUT) should NOT retry."""
        h = _setup_home(tmp_path)
        deps = _deps(h)

        call_count = 0

        def _side_effect(*args: Any, **kwargs: Any) -> AnalyzerResult:
            nonlocal call_count
            call_count += 1
            from daily_agenda.analyze import AnalyzerError
            raise AnalyzerError("ANALYZER_TIMEOUT")

        with patch("daily_agenda.analyze.run_analyzer", side_effect=_side_effect):
            code, obj = _run(["run"], deps, capsys)

        assert obj["ok"] is False
        assert obj["code"] == "ANALYZER_TIMEOUT"
        assert call_count == 1  # No retry

    def test_retry_both_fail(self, tmp_path: Path, capsys: Any) -> None:
        """Both attempts fail → should return error."""
        h = _setup_home(tmp_path)
        deps = _deps(h)

        def _side_effect(*args: Any, **kwargs: Any) -> AnalyzerResult:
            from daily_agenda.analyze import AnalyzerError
            raise AnalyzerError("MODEL_OUTPUT_INVALID")

        with patch("daily_agenda.analyze.run_analyzer", side_effect=_side_effect):
            code, obj = _run(["run"], deps, capsys)

        assert obj["ok"] is False
        assert obj["code"] == "MODEL_OUTPUT_INVALID"


class TestDigestMerge:
    """Test C2 merge functions directly."""

    def test_merge_chat_digests_single_batch(self) -> None:
        from daily_agenda.cli.cmd_run import _merge_chat_digests
        batches = [[
            {"chat_ref": "chat:A", "bullets": ["b1"], "needs_reply": [], "quiet": False},
        ]]
        result = _merge_chat_digests(batches)
        assert len(result) == 1
        assert result[0]["bullets"] == ["b1"]

    def test_merge_chat_digests_multi_batch(self) -> None:
        from daily_agenda.cli.cmd_run import _merge_chat_digests
        batches = [
            [{"chat_ref": "chat:A", "bullets": ["b1", "b2"], "needs_reply": [{"src_id": "s1"}], "quiet": True}],
            [{"chat_ref": "chat:A", "bullets": ["b3", "b4", "b5", "b6"], "needs_reply": [{"src_id": "s1"}, {"src_id": "s2"}], "quiet": True}],
        ]
        result = _merge_chat_digests(batches)
        assert len(result) == 1
        # Bullets concatenated then truncated to 5
        assert len(result[0]["bullets"]) == 5
        assert result[0]["bullets"] == ["b1", "b2", "b3", "b4", "b5"]
        # needs_reply dedup by src_id
        assert len(result[0]["needs_reply"]) == 2
        # quiet = all True
        assert result[0]["quiet"] is True

    def test_merge_chat_digests_quiet_false_if_any(self) -> None:
        from daily_agenda.cli.cmd_run import _merge_chat_digests
        batches = [
            [{"chat_ref": "chat:A", "bullets": [], "needs_reply": [], "quiet": True}],
            [{"chat_ref": "chat:A", "bullets": [], "needs_reply": [], "quiet": False}],
        ]
        result = _merge_chat_digests(batches)
        assert result[0]["quiet"] is False

    def test_merge_mail_digests_dedup_last_wins(self) -> None:
        from daily_agenda.cli.cmd_run import _merge_mail_digests
        batches = [
            [{"src_id": "mail:A", "category": "fyi", "summary": "old"}],
            [{"src_id": "mail:A", "category": "reply", "summary": "new"}],
        ]
        result = _merge_mail_digests(batches)
        assert len(result) == 1
        assert result[0]["summary"] == "new"
        assert result[0]["category"] == "reply"

    def test_merge_mail_digests_different_ids(self) -> None:
        from daily_agenda.cli.cmd_run import _merge_mail_digests
        batches = [
            [{"src_id": "mail:A", "category": "reply", "summary": "a"}],
            [{"src_id": "mail:B", "category": "action", "summary": "b"}],
        ]
        result = _merge_mail_digests(batches)
        assert len(result) == 2

    def test_merge_chat_digests_null_fields_no_crash(self) -> None:
        """HIGH-2: null/non-list bullets or needs_reply must not crash."""
        from daily_agenda.cli.cmd_run import _merge_chat_digests
        batches = [
            [{"chat_ref": "chat:A", "bullets": None, "needs_reply": None, "quiet": False}],
            [{"chat_ref": "chat:A", "bullets": "bad", "needs_reply": "bad", "quiet": True}],
        ]
        result = _merge_chat_digests(batches)
        assert len(result) == 1
        assert isinstance(result[0]["bullets"], list)
        assert isinstance(result[0]["needs_reply"], list)

    def test_merge_chat_digests_non_dict_entries_dropped(self) -> None:
        """HIGH-2: non-dict entries in needs_reply and non-str bullets dropped."""
        from daily_agenda.cli.cmd_run import _merge_chat_digests
        batches = [
            [{"chat_ref": "chat:A", "bullets": [123, "ok", None], "needs_reply": ["bad", {"src_id": "s1"}], "quiet": False}],
        ]
        result = _merge_chat_digests(batches)
        assert result[0]["bullets"] == ["ok"]
        assert result[0]["needs_reply"] == [{"src_id": "s1"}]

    def test_merge_unexpected_exception_degrades_to_empty(self) -> None:
        """HIGH-2: unexpected error in merge → empty digests, no crash."""
        from daily_agenda.cli.cmd_run import _merge_chat_digests, _merge_mail_digests
        # A truly bizarre input that might cause issues
        result_chat = _merge_chat_digests([None, "bad", 42])  # type: ignore
        assert result_chat == []
        result_mail = _merge_mail_digests([None, "bad"])  # type: ignore
        assert result_mail == []

    def test_items_not_list_retried(self, tmp_path: Path, capsys: Any) -> None:
        """MEDIUM-1: items not a list should be inside retry logic."""
        h = _setup_home(tmp_path)
        deps = _deps(h)

        call_count = 0

        def _side_effect(*args: Any, **kwargs: Any) -> AnalyzerResult:
            nonlocal call_count
            call_count += 1
            if call_count == 1:
                # First call: valid JSON but items is a string
                return AnalyzerResult(output={"schema_version": 2, "items": "bad"}, ms=0)
            return AnalyzerResult(output={
                "schema_version": 2, "items": [],
                "chat_digests": [], "mail_digests": [],
            }, ms=0)

        with patch("daily_agenda.analyze.run_analyzer", side_effect=_side_effect):
            code, obj = _run(["run"], deps, capsys)

        assert obj["ok"] is True
        assert call_count == 2  # First attempt failed, retry succeeded
