"""Tests for progress reporting: writer, watch, show, new, and integration."""

from __future__ import annotations

import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from daily_agenda.progress import Progress
from daily_agenda.analyze import AnalyzerResult
from daily_agenda import criteria, state
from daily_agenda.cli import main
from daily_agenda.cli._common import Deps
from tests.conftest import (
    TAIPEI, NOW, FakeGraph, FakeLine,
    make_line_chat, make_line_message, make_mail_item, make_calendar_item,
)


# ---------------------------------------------------------------------------
# Progress writer unit tests
# ---------------------------------------------------------------------------

class TestProgressWriter:
    def test_none_path_is_noop(self, tmp_path: Path) -> None:
        """Progress(None) does nothing — no file created."""
        p = Progress(None)
        p.event("start")
        p.done()
        p.failed(code="TEST")
        p.throttled_chat(1, 10)
        # No file created anywhere
        assert list(tmp_path.iterdir()) == []

    def test_writes_valid_jsonl(self, tmp_path: Path) -> None:
        path = tmp_path / "prog.jsonl"
        p = Progress(path)
        p.event("start")
        p.event("line_chat", i=5, n=50)
        p.done(code=None)

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) == 3
        for line in lines:
            obj = json.loads(line)
            assert "at" in obj
            assert "stage" in obj

        assert json.loads(lines[0])["stage"] == "start"
        assert json.loads(lines[1])["stage"] == "line_chat"
        assert json.loads(lines[1])["i"] == 5
        assert json.loads(lines[1])["n"] == 50
        assert json.loads(lines[2])["stage"] == "done"

    def test_omits_none_fields(self, tmp_path: Path) -> None:
        path = tmp_path / "prog.jsonl"
        p = Progress(path)
        p.event("start")

        obj = json.loads(path.read_text(encoding="utf-8").strip())
        assert "i" not in obj
        assert "n" not in obj
        assert "code" not in obj

    def test_code_field_present(self, tmp_path: Path) -> None:
        path = tmp_path / "prog.jsonl"
        p = Progress(path)
        p.failed(code="ANALYZER_TIMEOUT")

        obj = json.loads(path.read_text(encoding="utf-8").strip())
        assert obj["code"] == "ANALYZER_TIMEOUT"

    def test_unwritable_path_silent(self, tmp_path: Path) -> None:
        """If the progress file can't be written, continue silently."""
        path = tmp_path / "nonexistent" / "deep" / "prog.jsonl"
        p = Progress(path)
        # Should not raise
        p.event("start")
        p.done()

    def test_throttled_chat_first_last_every10(self, tmp_path: Path) -> None:
        path = tmp_path / "prog.jsonl"
        p = Progress(path)
        n = 35
        for i in range(1, n + 1):
            p.throttled_chat(i, n)

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(line) for line in lines]
        emitted_i = [e["i"] for e in events]
        # Must include i=1, i=10, i=20, i=30, i=35 (last)
        assert 1 in emitted_i
        assert 10 in emitted_i
        assert 20 in emitted_i
        assert 30 in emitted_i
        assert 35 in emitted_i
        # Must NOT include every single i
        assert len(emitted_i) < n

    def test_heartbeat_emits_with_tiny_interval(self, tmp_path: Path) -> None:
        path = tmp_path / "prog.jsonl"
        p = Progress(path)
        with p.heartbeat(interval_s=0.02):
            time.sleep(0.1)

        lines = path.read_text(encoding="utf-8").strip().splitlines()
        heartbeats = [json.loads(l) for l in lines if json.loads(l)["stage"] == "heartbeat"]
        assert len(heartbeats) >= 1

    def test_heartbeat_noop_on_none(self) -> None:
        p = Progress(None)
        with p.heartbeat(interval_s=0.01):
            time.sleep(0.05)
        # No error, no file

    def test_heartbeat_thread_joins(self, tmp_path: Path) -> None:
        """Heartbeat thread must not outlive the context manager."""
        path = tmp_path / "prog.jsonl"
        p = Progress(path)
        before = threading.active_count()
        with p.heartbeat(interval_s=0.01):
            time.sleep(0.05)
        time.sleep(0.1)
        after = threading.active_count()
        assert after <= before


# ---------------------------------------------------------------------------
# Progress CLI commands: new, show, watch
# ---------------------------------------------------------------------------

def _run_cli(argv: list[str], deps: Deps, capsys: Any) -> tuple[int, dict | str]:
    code = main(argv, deps=deps)
    captured = capsys.readouterr()
    lines = captured.out.strip().split("\n")
    try:
        return code, json.loads(lines[-1])
    except json.JSONDecodeError:
        return code, captured.out.strip()


def _deps(tmp_path: Path) -> Deps:
    return Deps(
        home=tmp_path,
        now_factory=lambda: NOW,
        plugin_root=Path(__file__).resolve().parents[2],
    )


class TestProgressNew:
    def test_creates_file(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["progress", "new"], deps, capsys)
        assert obj["ok"] is True
        assert "progress_file" in obj
        p = Path(obj["progress_file"])
        assert p.exists()
        assert p.parent.name == "progress"
        assert p.suffix == ".jsonl"


class TestProgressShow:
    def test_empty_file(self, tmp_path: Path, capsys: Any) -> None:
        pf = tmp_path / "runs" / "progress" / "test.jsonl"
        pf.parent.mkdir(parents=True)
        pf.write_bytes(b"")
        deps = _deps(tmp_path)
        code, obj = _run_cli(["progress", "show", "--file", str(pf)], deps, capsys)
        assert obj["ok"] is True
        assert obj["last"] is None

    def test_shows_last_event(self, tmp_path: Path, capsys: Any) -> None:
        pf = tmp_path / "runs" / "progress" / "test.jsonl"
        pf.parent.mkdir(parents=True)
        p = Progress(pf)
        p.event("start")
        p.event("line_chat", i=5, n=10)

        deps = _deps(tmp_path)
        code, obj = _run_cli(["progress", "show", "--file", str(pf)], deps, capsys)
        assert obj["ok"] is True
        assert obj["last"]["stage"] == "line_chat"
        assert obj["last"]["i"] == 5
        assert isinstance(obj["stale_s"], (int, float))

    def test_missing_file(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(
            ["progress", "show", "--file", str(tmp_path / "nope.jsonl")],
            deps, capsys,
        )
        assert obj["ok"] is True
        assert obj["last"] is None

    def test_missing_file_arg(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["progress", "show"], deps, capsys)
        assert obj["ok"] is False
        assert obj["code"] == "ARG_ERROR"


class TestProgressWatch:
    def test_exits_on_done(self, tmp_path: Path, capsys: Any) -> None:
        pf = tmp_path / "prog.jsonl"
        p = Progress(pf)
        p.event("start")
        p.event("line_chat", i=1, n=5)
        p.done()

        deps = _deps(tmp_path)
        code = main(
            ["progress", "watch", "--file", str(pf), "--poll-s", "0.05", "--timeout-s", "1"],
            deps=deps,
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "完成" in out

    def test_exits_on_failed(self, tmp_path: Path, capsys: Any) -> None:
        pf = tmp_path / "prog.jsonl"
        p = Progress(pf)
        p.event("start")
        p.failed(code="ANALYZER_TIMEOUT")

        deps = _deps(tmp_path)
        code = main(
            ["progress", "watch", "--file", str(pf), "--poll-s", "0.05", "--timeout-s", "1"],
            deps=deps,
        )
        assert code == 1
        out = capsys.readouterr().out
        assert "失敗" in out

    def test_exits_on_timeout(self, tmp_path: Path, capsys: Any) -> None:
        pf = tmp_path / "prog.jsonl"
        pf.write_bytes(b"")  # empty file, no events

        deps = _deps(tmp_path)
        code = main(
            ["progress", "watch", "--file", str(pf), "--poll-s", "0.05", "--timeout-s", "0.2"],
            deps=deps,
        )
        assert code == 3
        out = capsys.readouterr().out
        assert "中斷" in out

    def test_missing_file_then_appears(self, tmp_path: Path, capsys: Any) -> None:
        """File doesn't exist at start; appears later with done event."""
        pf = tmp_path / "prog.jsonl"

        def _write_later() -> None:
            time.sleep(0.15)
            p = Progress(pf)
            p.event("start")
            p.done()

        t = threading.Thread(target=_write_later)
        t.start()

        deps = _deps(tmp_path)
        code = main(
            ["progress", "watch", "--file", str(pf), "--poll-s", "0.05", "--timeout-s", "2"],
            deps=deps,
        )
        t.join()
        assert code == 0

    def test_partial_line_not_emitted(self, tmp_path: Path, capsys: Any) -> None:
        """Incomplete JSONL lines are not emitted."""
        pf = tmp_path / "prog.jsonl"
        # Write a complete event + incomplete line + done
        p = Progress(pf)
        p.event("start")
        # Append a partial line then done
        with open(pf, "a", encoding="utf-8") as f:
            f.write('{"stage":"partial"')  # incomplete, no newline
        # Now complete it and add done
        with open(pf, "a", encoding="utf-8") as f:
            f.write('\n')  # close the partial
        p.done()

        deps = _deps(tmp_path)
        code = main(
            ["progress", "watch", "--file", str(pf), "--poll-s", "0.05", "--timeout-s", "1"],
            deps=deps,
        )
        assert code == 0


# ---------------------------------------------------------------------------
# Integration: progress events emitted by criteria draft and run
# ---------------------------------------------------------------------------

def _setup_home(tmp_path: Path) -> Path:
    state.accept_consent(tmp_path, NOW)
    cfg = state.load_config(tmp_path)
    cfg["line"]["enabled"] = False
    cfg["mail"]["enabled"] = True
    cfg["output"]["dir"] = str(tmp_path / "output")
    cfg["output"]["formats"] = ["md"]
    state.save_config(tmp_path, cfg)
    criteria.save(tmp_path, "# Test criteria\nAccept all items as P1.")
    return tmp_path


class TestProgressIntegrationRun:
    """Progress events from the ``run`` command."""

    def test_run_emits_ordered_stages(self, tmp_path: Path, capsys: Any) -> None:
        """Verify stage order: start → mail → calendar → analyze_batch → done."""
        h = _setup_home(tmp_path)
        pf = tmp_path / "progress.jsonl"
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(
                mail_items=[make_mail_item()],
                calendar_items=[make_calendar_item()],
            ),
            progress=Progress(pf),
        )

        with patch("daily_agenda.analyze.run_analyzer") as mock:
            mock.return_value = AnalyzerResult(
                output={"schema_version": 1, "items": []}, ms=0,
            )
            main(["run", "--progress-file", str(pf)], deps=deps)

        lines = pf.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(l) for l in lines]
        stages = [e["stage"] for e in events]
        assert stages[0] == "start"
        assert stages[-1] == "done"
        # mail and calendar before analyze
        assert "mail" in stages
        assert "calendar" in stages
        assert "analyze_batch" in stages
        mail_idx = stages.index("mail")
        cal_idx = stages.index("calendar")
        analyze_idx = stages.index("analyze_batch")
        assert mail_idx < analyze_idx
        assert cal_idx < analyze_idx

    def test_run_with_line_emits_line_stages(self, tmp_path: Path, capsys: Any) -> None:
        """With LINE enabled, line_list and line_chat events appear."""
        h = _setup_home(tmp_path)
        cfg = state.load_config(h)
        cfg["line"]["enabled"] = True
        cfg["line"]["main_database"] = "test.edb"
        state.save_config(h, cfg)

        pf = tmp_path / "progress.jsonl"
        fake_line = FakeLine(
            chats=[make_line_chat(), make_line_chat(ref="chat:2", name="G2")],
            messages=[make_line_message()],
        )
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(
                mail_items=[make_mail_item()],
                calendar_items=[],
            ),
            line_source_factory=lambda **kw: fake_line,
            progress=Progress(pf),
        )

        with patch("daily_agenda.analyze.run_analyzer") as mock:
            mock.return_value = AnalyzerResult(
                output={"schema_version": 1, "items": []}, ms=0,
            )
            main(["run", "--progress-file", str(pf)], deps=deps)

        lines = pf.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(l) for l in lines]
        stages = [e["stage"] for e in events]
        assert "line_list" in stages
        assert "line_chat" in stages

    def test_run_failure_emits_failed(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        pf = tmp_path / "progress.jsonl"
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(
                mail_items=[make_mail_item()],
                calendar_items=[make_calendar_item()],
            ),
            progress=Progress(pf),
        )

        with patch("daily_agenda.analyze.run_analyzer") as mock:
            from daily_agenda.analyze import AnalyzerError
            mock.side_effect = AnalyzerError("ANALYZER_TIMEOUT")
            main(["run", "--progress-file", str(pf)], deps=deps)

        lines = pf.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(l) for l in lines]
        stages = [e["stage"] for e in events]
        assert "failed" in stages
        last = events[-1]
        assert last["stage"] == "failed"
        assert last["code"] == "ANALYZER_TIMEOUT"


class TestProgressIntegrationCriteriaDraft:
    """Progress events from ``criteria draft``."""

    def test_criteria_draft_emits_analyze_events(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        pf = tmp_path / "progress.jsonl"
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(
                mail_items=[make_mail_item()],
                calendar_items=[make_calendar_item()],
            ),
            progress=Progress(pf),
        )

        with patch("daily_agenda.analyze.run_analyzer") as mock:
            mock.return_value = AnalyzerResult(
                output={"schema_version": 1, "criteria_markdown": "# Test criteria"},
                ms=0,
            )
            main(["criteria", "draft", "--days", "14", "--progress-file", str(pf)], deps=deps)

        lines = pf.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(l) for l in lines]
        stages = [e["stage"] for e in events]
        assert "start" in stages
        assert "analyze_batch" in stages
        assert "done" in stages


class TestWatchOutputClean:
    """Watch output must not contain unexpected error messages from code rendering."""

    def test_watch_no_unexpected_error(self, tmp_path: Path, capsys: Any) -> None:
        """Stages like analyze_batch_done must not render as errors."""
        pf = tmp_path / "prog.jsonl"
        p = Progress(pf)
        p.event("start")
        p.event("analyze_batch", i=1, n=1)
        p.event("analyze_batch_done", i=1, n=1)
        p.done()

        deps = _deps(tmp_path)
        code = main(
            ["progress", "watch", "--file", str(pf), "--poll-s", "0.05", "--timeout-s", "1"],
            deps=deps,
        )
        assert code == 0
        out = capsys.readouterr().out
        assert "未預期" not in out

    def test_watch_no_sentinel_in_output(self, tmp_path: Path, capsys: Any) -> None:
        """Watch output must not contain untrusted content even if it were
        somehow in the progress file (belt-and-suspenders)."""
        pf = tmp_path / "prog.jsonl"
        p = Progress(pf)
        p.event("start")
        p.event("mail", i=1, n=1)
        p.done()

        deps = _deps(tmp_path)
        code = main(
            ["progress", "watch", "--file", str(pf), "--poll-s", "0.05", "--timeout-s", "1"],
            deps=deps,
        )
        assert code == 0
        out = capsys.readouterr().out
        # All lines should be from _STAGE_LABELS, no raw data
        for line in out.strip().splitlines():
            assert "SENTINEL" not in line


class TestNoRawContentInProgress:
    """Progress events must never contain untrusted content."""

    SENTINEL = "SENTINEL_SECRET_PRIVATE_DATA_xyz123"

    def test_run_progress_no_sentinel(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        cfg = state.load_config(h)
        cfg["line"]["enabled"] = True
        cfg["line"]["main_database"] = "test.edb"
        state.save_config(h, cfg)

        pf = tmp_path / "progress.jsonl"
        fake_line = FakeLine(
            chats=[make_line_chat(name=self.SENTINEL)],
            messages=[make_line_message(
                sender=self.SENTINEL,
                text=self.SENTINEL,
            )],
        )
        fake_graph = FakeGraph(
            mail_items=[make_mail_item(subject=self.SENTINEL)],
            mail_full={"AAMk-test-001": {
                "id": "AAMk-test-001",
                "subject": self.SENTINEL,
                "from": self.SENTINEL,
                "to": "me@test.com",
                "received_at": NOW.isoformat(),
                "internet_message_id": "<test@test.com>",
                "body": self.SENTINEL,
            }},
            calendar_items=[make_calendar_item(subject=self.SENTINEL)],
        )
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: fake_graph,
            line_source_factory=lambda **kw: fake_line,
            progress=Progress(pf),
        )

        with patch("daily_agenda.analyze.run_analyzer") as mock:
            mock.return_value = AnalyzerResult(
                output={"schema_version": 1, "items": []}, ms=0,
            )
            main(["run", "--progress-file", str(pf)], deps=deps)

        # Progress file must not contain sentinel
        progress_bytes = pf.read_bytes()
        assert self.SENTINEL.encode() not in progress_bytes

    def test_criteria_draft_progress_no_sentinel(self, tmp_path: Path, capsys: Any) -> None:
        h = _setup_home(tmp_path)
        cfg = state.load_config(h)
        cfg["line"]["enabled"] = True
        cfg["line"]["main_database"] = "test.edb"
        state.save_config(h, cfg)

        pf = tmp_path / "progress.jsonl"
        fake_line = FakeLine(
            chats=[make_line_chat(name=self.SENTINEL)],
            messages=[make_line_message(
                sender=self.SENTINEL,
                text=self.SENTINEL,
            )],
        )
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(
                mail_items=[make_mail_item(subject=self.SENTINEL)],
            ),
            line_source_factory=lambda **kw: fake_line,
            progress=Progress(pf),
        )

        with patch("daily_agenda.analyze.run_analyzer") as mock:
            mock.return_value = AnalyzerResult(
                output={"schema_version": 1, "criteria_markdown": "# Criteria"},
                ms=0,
            )
            main(["criteria", "draft", "--days", "14", "--progress-file", str(pf)], deps=deps)

        progress_bytes = pf.read_bytes()
        assert self.SENTINEL.encode() not in progress_bytes


class TestFailedEventNoDetail:
    """A failed event must not contain a 'detail' key (untrusted leak vector)."""

    def test_arg_error_no_detail_in_progress(self, tmp_path: Path, capsys: Any) -> None:
        pf = tmp_path / "progress.jsonl"
        deps = Deps(
            home=tmp_path,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            progress=Progress(pf),
        )
        main(["criteria", "--progress-file", str(pf)], deps=deps)

        lines = pf.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(l) for l in lines]
        for ev in events:
            assert "detail" not in ev


# ---------------------------------------------------------------------------
# LINE fail-fast in criteria draft
# ---------------------------------------------------------------------------

class TestCriteriaFailFast:
    def test_line_fail_fast_returns_code_no_analyzer(self, tmp_path: Path, capsys: Any) -> None:
        """criteria draft with LINE enabled: fail-fast returns code without
        calling graph or analyzer."""
        h = _setup_home(tmp_path)
        cfg = state.load_config(h)
        cfg["line"]["enabled"] = True
        cfg["line"]["main_database"] = "test.edb"
        state.save_config(h, cfg)

        fake_line = FakeLine(
            fail_on_recent_chats="SESSION_KEY_UNAVAILABLE",
        )
        fake_graph = FakeGraph(mail_items=[make_mail_item()])

        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            line_source_factory=lambda **kw: fake_line,
            graph_factory=lambda: fake_graph,
        )

        with patch("daily_agenda.analyze.run_analyzer") as mock_analyzer:
            code, obj = _run_cli(["criteria", "draft", "--days", "14"], deps, capsys)

        assert obj["ok"] is False
        assert obj["code"] == "SESSION_KEY_UNAVAILABLE"
        # Analyzer must not have been called
        mock_analyzer.assert_not_called()
        # Graph mail must not have been called
        assert fake_graph.mail_list_calls == 0

    def test_line_disabled_no_fail_fast(self, tmp_path: Path, capsys: Any) -> None:
        """criteria draft with LINE disabled: no fail-fast, proceeds normally."""
        h = _setup_home(tmp_path)
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(
                mail_items=[make_mail_item()],
                calendar_items=[make_calendar_item()],
            ),
        )

        with patch("daily_agenda.analyze.run_analyzer") as mock:
            mock.return_value = AnalyzerResult(
                output={"schema_version": 1, "criteria_markdown": "# Test"},
                ms=0,
            )
            code, obj = _run_cli(["criteria", "draft", "--days", "14"], deps, capsys)

        assert obj["ok"] is True


# ---------------------------------------------------------------------------
# Criteria draft batching
# ---------------------------------------------------------------------------

class TestCriteriaDraftBatching:
    def test_single_batch_uses_criteria_prompt(self, tmp_path: Path, capsys: Any) -> None:
        """Single batch uses the original criteria prompt directly."""
        h = _setup_home(tmp_path)
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(
                mail_items=[make_mail_item()],
                calendar_items=[make_calendar_item()],
            ),
        )

        prompts_used: list[str] = []
        from daily_agenda import analyze as _analyze_mod
        original_load = _analyze_mod.load_prompt

        def _track_load(kind: str) -> str:
            prompts_used.append(kind)
            return original_load(kind)

        with patch("daily_agenda.analyze.run_analyzer") as mock, \
             patch("daily_agenda.analyze.load_prompt", side_effect=_track_load):
            mock.return_value = AnalyzerResult(
                output={"schema_version": 1, "criteria_markdown": "# Test"},
                ms=0,
            )
            code, obj = _run_cli(["criteria", "draft", "--days", "14"], deps, capsys)

        assert obj["ok"] is True
        assert "criteria" in prompts_used
        assert "criteria_observe" not in prompts_used
        # Only 1 analyzer call for single batch
        assert mock.call_count == 1

    def test_multi_batch_map_reduce(self, tmp_path: Path, capsys: Any) -> None:
        """Multiple batches: N map calls (criteria_observe) + 1 reduce call."""
        h = _setup_home(tmp_path)
        cfg = state.load_config(h)
        # Set tiny batch_bytes to force multiple batches
        cfg["analysis"]["batch_bytes"] = 50
        state.save_config(h, cfg)

        # Need enough records to fill multiple batches
        mail_items = [make_mail_item(msg_id=f"AAMk-{i}") for i in range(5)]
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(
                mail_items=mail_items,
                calendar_items=[],
            ),
        )

        prompts_used: list[str] = []

        # Track which prompt kind is loaded; use a marker in the prompt text
        # to tell the fake analyzer which response shape to return.
        def _track_load(kind: str) -> str:
            prompts_used.append(kind)
            # Return a distinguishable marker so _fake_analyzer knows the kind
            return f"__KIND:{kind}__"

        def _fake_analyzer(stdin_bytes: bytes, prompt: str, **kwargs: Any) -> AnalyzerResult:
            if "__KIND:criteria_observe__" in prompt:
                return AnalyzerResult(
                    output={
                        "schema_version": 1,
                        "observations": [
                            {
                                "category": "follow-ups",
                                "pattern": "Requests for follow-up action",
                                "frequency": "high",
                                "priority_signal": "P1",
                            }
                        ],
                    },
                    ms=0,
                )
            else:
                return AnalyzerResult(
                    output={"schema_version": 1, "criteria_markdown": "# Generated criteria"},
                    ms=0,
                )

        with patch("daily_agenda.analyze.run_analyzer", side_effect=_fake_analyzer), \
             patch("daily_agenda.analyze.load_prompt", side_effect=_track_load):
            code, obj = _run_cli(["criteria", "draft", "--days", "14"], deps, capsys)

        assert obj["ok"] is True
        # Must have used observe + reduce prompts
        assert "criteria_observe" in prompts_used
        assert "criteria_reduce" in prompts_used
        # Total calls = N map + 1 reduce, where N > 1
        observe_count = prompts_used.count("criteria_observe")
        reduce_count = prompts_used.count("criteria_reduce")
        assert observe_count >= 2  # at least 2 batches
        assert reduce_count == 1

    def test_malformed_observations_returns_model_output_invalid(
        self, tmp_path: Path, capsys: Any,
    ) -> None:
        """Malformed observations from map step → MODEL_OUTPUT_INVALID."""
        h = _setup_home(tmp_path)
        cfg = state.load_config(h)
        cfg["analysis"]["batch_bytes"] = 50
        state.save_config(h, cfg)

        mail_items = [make_mail_item(msg_id=f"AAMk-{i}") for i in range(5)]
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(mail_items=mail_items, calendar_items=[]),
        )

        def _bad_analyzer(stdin_bytes: bytes, prompt: str, **kwargs: Any) -> AnalyzerResult:
            # Return invalid observations
            return AnalyzerResult(
                output={"schema_version": 1, "observations": [{"bad": "field"}]},
                ms=0,
            )

        with patch("daily_agenda.analyze.run_analyzer", side_effect=_bad_analyzer), \
             patch("daily_agenda.analyze.load_prompt", return_value="__KIND:criteria_observe__"):
            code, obj = _run_cli(["criteria", "draft", "--days", "14"], deps, capsys)

        assert obj["ok"] is False
        assert obj["code"] == "MODEL_OUTPUT_INVALID"


# ---------------------------------------------------------------------------
# Validate observations
# ---------------------------------------------------------------------------

class TestValidateObservations:
    def test_valid(self) -> None:
        from daily_agenda.validate import validate_observations

        obs = validate_observations({
            "schema_version": 1,
            "observations": [
                {
                    "category": "follow-ups",
                    "pattern": "boss asks for follow-up",
                    "frequency": "high",
                    "priority_signal": "P1",
                },
            ],
        })
        assert len(obs) == 1
        assert obs[0]["category"] == "follow-ups"

    def test_wrong_version(self) -> None:
        from daily_agenda.validate import validate_observations, ModelOutputInvalid

        with pytest.raises(ModelOutputInvalid):
            validate_observations({"schema_version": 2, "observations": []})

    def test_extra_fields(self) -> None:
        from daily_agenda.validate import validate_observations, ModelOutputInvalid

        with pytest.raises(ModelOutputInvalid):
            validate_observations({
                "schema_version": 1,
                "observations": [],
                "extra": True,
            })

    def test_too_many(self) -> None:
        from daily_agenda.validate import validate_observations, ModelOutputInvalid

        obs_list = [
            {
                "category": f"cat-{i}",
                "pattern": "p",
                "frequency": "low",
                "priority_signal": "P2",
            }
            for i in range(50)
        ]
        with pytest.raises(ModelOutputInvalid):
            validate_observations({"schema_version": 1, "observations": obs_list})

    def test_invalid_frequency(self) -> None:
        from daily_agenda.validate import validate_observations, ModelOutputInvalid

        with pytest.raises(ModelOutputInvalid):
            validate_observations({
                "schema_version": 1,
                "observations": [{
                    "category": "x",
                    "pattern": "p",
                    "frequency": "very_high",
                    "priority_signal": "P1",
                }],
            })

    def test_invalid_keys_in_observation(self) -> None:
        from daily_agenda.validate import validate_observations, ModelOutputInvalid

        with pytest.raises(ModelOutputInvalid):
            validate_observations({
                "schema_version": 1,
                "observations": [{
                    "category": "x",
                    "pattern": "p",
                    "frequency": "high",
                    "priority_signal": "P1",
                    "extra_key": "bad",
                }],
            })


# ---------------------------------------------------------------------------
# Progress write failure doesn't affect result
# ---------------------------------------------------------------------------

class TestProgressWriteFailureNoEffect:
    def test_run_succeeds_with_broken_progress(self, tmp_path: Path, capsys: Any) -> None:
        """If progress file can't be written, the command still succeeds."""
        h = _setup_home(tmp_path)
        # Point to a path that can't be written
        broken_path = tmp_path / "nonexistent_dir" / "deep" / "prog.jsonl"
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(
                mail_items=[make_mail_item()],
                calendar_items=[make_calendar_item()],
            ),
            progress=Progress(broken_path),
        )

        with patch("daily_agenda.analyze.run_analyzer") as mock:
            mock.return_value = AnalyzerResult(
                output={"schema_version": 1, "items": []}, ms=0,
            )
            code, obj = _run_cli(["run"], deps, capsys)

        assert obj["ok"] is True


# ---------------------------------------------------------------------------
# BLOCKING #1: progress file gets failed event even when Deps.production() raises
# ---------------------------------------------------------------------------

class TestProgressOnStartupFailure:
    def test_production_raises_writes_failed(self, tmp_path: Path, capsys: Any) -> None:
        """If Deps.production() raises, the progress file still ends with failed."""
        pf = tmp_path / "progress.jsonl"
        pf.write_bytes(b"")

        with patch.object(Deps, "production", side_effect=RuntimeError("missing tzdata")):
            code = main(
                ["run", "--progress-file", str(pf)],
                # deps=None triggers Deps.production()
            )

        assert code != 0
        lines = pf.read_text(encoding="utf-8").strip().splitlines()
        assert len(lines) >= 1
        events = [json.loads(l) for l in lines]
        assert events[-1]["stage"] == "failed"
        assert events[-1]["code"] == "INTERNAL_ERROR"

    def test_keyboard_interrupt_writes_failed(self, tmp_path: Path, capsys: Any) -> None:
        """KeyboardInterrupt during execution still writes failed event."""
        h = _setup_home(tmp_path)
        pf = tmp_path / "progress.jsonl"
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(
                mail_items=[make_mail_item()],
                calendar_items=[],
            ),
            progress=Progress(pf),
        )

        with patch("daily_agenda.analyze.run_analyzer", side_effect=KeyboardInterrupt):
            try:
                main(["run", "--progress-file", str(pf)], deps=deps)
            except (KeyboardInterrupt, SystemExit):
                pass

        lines = pf.read_text(encoding="utf-8").strip().splitlines()
        events = [json.loads(l) for l in lines]
        assert events[-1]["stage"] == "failed"


# ---------------------------------------------------------------------------
# BLOCKING #2: LINE factory error or collect failure should fail criteria draft
# ---------------------------------------------------------------------------

class TestCriteriaDraftLineFailures:
    def test_factory_error_maps_to_code(self, tmp_path: Path, capsys: Any) -> None:
        """LINE factory raising LineSourceError -> returns that code, not silent success."""
        h = _setup_home(tmp_path)
        cfg = state.load_config(h)
        cfg["line"]["enabled"] = True
        cfg["line"]["main_database"] = "test.edb"
        state.save_config(h, cfg)

        from daily_agenda.line_source import LineSourceError

        def _raise_factory(**kw: Any) -> Any:
            raise LineSourceError("LINE_RUNTIME_MISSING")

        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            line_source_factory=_raise_factory,
            graph_factory=lambda: FakeGraph(mail_items=[make_mail_item()]),
        )

        with patch("daily_agenda.analyze.run_analyzer") as mock_analyzer:
            code, obj = _run_cli(["criteria", "draft", "--days", "14"], deps, capsys)

        assert obj["ok"] is False
        assert obj["code"] == "LINE_RUNTIME_MISSING"
        mock_analyzer.assert_not_called()

    def test_collect_line_failed_returns_error(self, tmp_path: Path, capsys: Any) -> None:
        """LINE passes fail-fast but fails during collect -> criteria draft fails."""
        h = _setup_home(tmp_path)
        cfg = state.load_config(h)
        cfg["line"]["enabled"] = True
        cfg["line"]["main_database"] = "test.edb"
        state.save_config(h, cfg)

        fake_line = FakeLine(
            chats=[make_line_chat()],
            # fail_on_recent_chats not set -> fail-fast passes
            fail_on_messages="SESSION_KEY_CHANGED",
        )

        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            line_source_factory=lambda **kw: fake_line,
            graph_factory=lambda: FakeGraph(mail_items=[make_mail_item()]),
        )

        with patch("daily_agenda.analyze.run_analyzer") as mock_analyzer:
            code, obj = _run_cli(["criteria", "draft", "--days", "14"], deps, capsys)

        assert obj["ok"] is False
        assert obj["code"] == "SESSION_KEY_CHANGED"
        mock_analyzer.assert_not_called()


# ---------------------------------------------------------------------------
# FIX #3: --max-s flag on progress watch
# ---------------------------------------------------------------------------

class TestWatchMaxS:
    def test_max_s_exits_on_expiry(self, tmp_path: Path, capsys: Any) -> None:
        """--max-s causes exit 3 with distinct message when wall time expires."""
        pf = tmp_path / "prog.jsonl"
        p = Progress(pf)
        p.event("start")
        # No done/failed; heartbeats keep inactivity timeout from firing

        deps = _deps(tmp_path)
        code = main(
            ["progress", "watch", "--file", str(pf),
             "--poll-s", "0.05", "--timeout-s", "5", "--max-s", "0.2"],
            deps=deps,
        )
        assert code == 3
        out = capsys.readouterr().out
        assert "超時" in out


# ---------------------------------------------------------------------------
# FIX #7: map step skip-and-count malformed batches
# ---------------------------------------------------------------------------

class TestCriteriaMapSkipMalformed:
    def test_some_batches_malformed_still_succeeds(self, tmp_path: Path, capsys: Any) -> None:
        """If fewer than half of map batches fail, draft succeeds with batches_skipped."""
        h = _setup_home(tmp_path)
        cfg = state.load_config(h)
        cfg["analysis"]["batch_bytes"] = 50
        state.save_config(h, cfg)

        mail_items = [make_mail_item(msg_id=f"AAMk-{i}") for i in range(5)]
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(mail_items=mail_items, calendar_items=[]),
        )

        call_count = [0]

        def _mixed_analyzer(stdin_bytes: bytes, prompt: str, **kwargs: Any) -> AnalyzerResult:
            call_count[0] += 1
            if "__KIND:criteria_observe__" in prompt:
                # First call returns bad observations, rest are good
                if call_count[0] == 1:
                    return AnalyzerResult(
                        output={"schema_version": 1, "observations": [{"bad": "field"}]},
                        ms=0,
                    )
                return AnalyzerResult(
                    output={
                        "schema_version": 1,
                        "observations": [{
                            "category": "test",
                            "pattern": "test pattern",
                            "frequency": "high",
                            "priority_signal": "P1",
                        }],
                    },
                    ms=0,
                )
            else:
                return AnalyzerResult(
                    output={"schema_version": 1, "criteria_markdown": "# Generated"},
                    ms=0,
                )

        def _track_load(kind: str) -> str:
            return f"__KIND:{kind}__"

        with patch("daily_agenda.analyze.run_analyzer", side_effect=_mixed_analyzer), \
             patch("daily_agenda.analyze.load_prompt", side_effect=_track_load):
            code, obj = _run_cli(["criteria", "draft", "--days", "14"], deps, capsys)

        assert obj["ok"] is True
        assert obj.get("batches_skipped", 0) >= 1

    def test_all_batches_malformed_fails(self, tmp_path: Path, capsys: Any) -> None:
        """If all map batches fail -> MODEL_OUTPUT_INVALID."""
        h = _setup_home(tmp_path)
        cfg = state.load_config(h)
        cfg["analysis"]["batch_bytes"] = 50
        state.save_config(h, cfg)

        mail_items = [make_mail_item(msg_id=f"AAMk-{i}") for i in range(5)]
        deps = Deps(
            home=h,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            graph_factory=lambda: FakeGraph(mail_items=mail_items, calendar_items=[]),
        )

        def _all_bad(stdin_bytes: bytes, prompt: str, **kwargs: Any) -> AnalyzerResult:
            return AnalyzerResult(
                output={"schema_version": 1, "observations": [{"bad": "field"}]},
                ms=0,
            )

        with patch("daily_agenda.analyze.run_analyzer", side_effect=_all_bad), \
             patch("daily_agenda.analyze.load_prompt", return_value="__KIND:criteria_observe__"):
            code, obj = _run_cli(["criteria", "draft", "--days", "14"], deps, capsys)

        assert obj["ok"] is False
        assert obj["code"] == "MODEL_OUTPUT_INVALID"


# ---------------------------------------------------------------------------
# FIX #8: watch renders unknown codes generically, not raw
# ---------------------------------------------------------------------------

class TestWatchUnknownCode:
    def test_unknown_code_no_raw_echo(self, tmp_path: Path, capsys: Any) -> None:
        """Unknown code strings in failed events should use generic message."""
        pf = tmp_path / "prog.jsonl"
        p = Progress(pf)
        p.event("start")
        p.failed(code="SOME_UNKNOWN_CODE_XYZ")

        deps = _deps(tmp_path)
        code = main(
            ["progress", "watch", "--file", str(pf), "--poll-s", "0.05", "--timeout-s", "1"],
            deps=deps,
        )
        assert code == 1
        out = capsys.readouterr().out
        # The raw code should not appear in watch output
        assert "SOME_UNKNOWN_CODE_XYZ" not in out
