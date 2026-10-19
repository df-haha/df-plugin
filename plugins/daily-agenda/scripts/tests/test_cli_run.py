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
