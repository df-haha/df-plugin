"""Tests for the profile, docs, scan, raw CLI commands."""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import pytest

from daily_agenda.cli import main
from daily_agenda.cli._common import Deps
from daily_agenda import profile, state
from daily_agenda.progress import Progress
from tests.conftest import fake_cfg, fake_state, NOW, TAIPEI, FakeLine, FakeGraph


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _deps(tmp_path: Path, cfg: dict | None = None) -> Deps:
    h = tmp_path / ".daily-agenda"
    h.mkdir(parents=True, exist_ok=True)
    c = cfg or fake_cfg()
    state.save_config(h, c)
    return Deps(
        home=h,
        now_factory=lambda: NOW,
        plugin_root=Path(__file__).resolve().parents[2],
    )


def _run(argv: list[str], deps: Deps) -> dict[str, Any]:
    import io
    import sys

    old_stdout = sys.stdout
    sys.stdout = io.StringIO()
    try:
        main(argv, deps=deps)
        output = sys.stdout.getvalue()
    finally:
        sys.stdout = old_stdout
    return json.loads(output)


# ---------------------------------------------------------------------------
# profile
# ---------------------------------------------------------------------------


class TestProfileCLI:
    def test_list_empty(self, tmp_path: Path) -> None:
        d = _deps(tmp_path)
        result = _run(["profile", "list"], d)
        assert result["ok"] is True
        assert result["versions"] == []

    def test_save_and_show(self, tmp_path: Path) -> None:
        d = _deps(tmp_path)
        # Write a draft file
        draft = d.home / "runs" / "test" / "profile-draft.md"
        draft.parent.mkdir(parents=True)
        draft.write_text("# Profile\n## A 我是誰\n...", encoding="utf-8")

        result = _run(["profile", "save", "--file", str(draft)], d)
        assert result["ok"] is True
        assert result["version"] == 1

        result = _run(["profile", "show"], d)
        assert result["ok"] is True
        assert result["version"] == 1
        # Show output is fenced
        assert "UNTRUSTED-" in result["text"]

    def test_show_no_profile(self, tmp_path: Path) -> None:
        d = _deps(tmp_path)
        result = _run(["profile", "show"], d)
        assert result["ok"] is False
        assert result["code"] == "PROFILE_MISSING"

    def test_restore(self, tmp_path: Path) -> None:
        d = _deps(tmp_path)
        profile.save(d.home, "V1")
        profile.save(d.home, "V2")

        result = _run(["profile", "restore", "--version", "1"], d)
        assert result["ok"] is True
        assert result["version"] == 3

    def test_coverage_save_and_read(self, tmp_path: Path) -> None:
        d = _deps(tmp_path)
        cov = {
            "dimensions": {dim: {"status": "missing", "sources": []} for dim in profile.DIM_IDS},
            "updated": "2026-10-15",
        }
        cov_file = d.home / "runs" / "test" / "coverage-draft.json"
        cov_file.parent.mkdir(parents=True)
        cov_file.write_text(json.dumps(cov), encoding="utf-8")

        result = _run(["profile", "coverage", "save", "--file", str(cov_file)], d)
        assert result["ok"] is True

        result = _run(["profile", "coverage"], d)
        assert result["ok"] is True
        assert "dimensions" in result["coverage"]

    def test_cadence(self, tmp_path: Path) -> None:
        d = _deps(tmp_path)
        result = _run(["profile", "cadence"], d)
        assert result["ok"] is True
        assert result["cadence"] == "later"

        result = _run(["profile", "cadence", "--set", "agenda"], d)
        assert result["ok"] is True
        assert result["cadence"] == "agenda"

    def test_next_question_no_coverage(self, tmp_path: Path) -> None:
        d = _deps(tmp_path)
        profile.save_cadence(d.home, "agenda")
        result = _run(["profile", "next-question"], d)
        assert result["ok"] is True
        assert result["question"] is None

    def test_next_question_rate_limit_via_cli(self, tmp_path: Path) -> None:
        """Calling next-question twice in the same generation returns null the second time."""
        d = _deps(tmp_path)
        profile.save_cadence(d.home, "agenda")
        cov = {
            "dimensions": {dim: {"status": "missing", "sources": []} for dim in profile.DIM_IDS},
            "updated": "2026-10-15",
        }
        profile.save_coverage(d.home, cov)
        # Create a generation so current_generation is not None
        from daily_agenda.state import _initial_state
        staging = state.stage_generation(
            d.home, state=_initial_state(), ledger=[],
            outputs={}, publish_targets=[],
        )
        state.commit_generation(d.home, staging)

        r1 = _run(["profile", "next-question"], d)
        assert r1["ok"] is True
        assert r1["question"] is not None

        r2 = _run(["profile", "next-question"], d)
        assert r2["ok"] is True
        assert r2["question"] is None  # rate-limited


# ---------------------------------------------------------------------------
# docs convert
# ---------------------------------------------------------------------------


class TestDocsCLI:
    def test_convert_txt(self, tmp_path: Path) -> None:
        d = _deps(tmp_path)
        src = tmp_path / "notes.txt"
        src.write_text("My notes content", encoding="utf-8")

        result = _run(["docs", "convert", "--path", str(src)], d)
        assert result["ok"] is True
        assert len(result["files"]) == 1
        assert result["files"][0]["original_name"] == "notes.txt"

    def test_convert_missing_path(self, tmp_path: Path) -> None:
        d = _deps(tmp_path)
        result = _run(["docs", "convert", "--path", "/nonexistent/path"], d)
        assert result["ok"] is False

    def test_convert_no_args(self, tmp_path: Path) -> None:
        d = _deps(tmp_path)
        result = _run(["docs", "convert"], d)
        assert result["ok"] is False
        assert result["code"] == "ARG_ERROR"

    def test_convert_no_consent(self, tmp_path: Path) -> None:
        d = _deps(tmp_path, cfg=fake_cfg(consent=False))
        result = _run(["docs", "convert", "--path", "/tmp"], d)
        assert result["ok"] is False
        assert result["code"] == "CONSENT_REQUIRED"


# ---------------------------------------------------------------------------
# raw show
# ---------------------------------------------------------------------------


class TestRawShowCLI:
    def test_raw_show_not_found(self, tmp_path: Path) -> None:
        d = _deps(tmp_path)
        result = _run(["raw", "show", "--todo", "T-9999"], d)
        assert result["ok"] is False
        assert result["code"] == "TODO_NOT_FOUND"

    def test_raw_show_no_args(self, tmp_path: Path) -> None:
        d = _deps(tmp_path)
        result = _run(["raw", "show"], d)
        assert result["ok"] is False
        assert result["code"] == "ARG_ERROR"


# ---------------------------------------------------------------------------
# criteria draft --from-run
# ---------------------------------------------------------------------------


class TestCriteriaDraftFromRun:
    def test_invalid_run_id(self, tmp_path: Path) -> None:
        d = _deps(tmp_path)
        result = _run(["criteria", "draft", "--from-run", "bad-id"], d)
        assert result["ok"] is False
        assert result["code"] == "ARG_ERROR"
        assert "invalid run_id" in result.get("detail", "")

    def test_missing_records(self, tmp_path: Path) -> None:
        d = _deps(tmp_path)
        result = _run(["criteria", "draft", "--from-run", "20261015-0900-ab12"], d)
        assert result["ok"] is False
        assert "no saved records" in result.get("detail", "")

    def test_line_failed_in_saved_status(self, tmp_path: Path) -> None:
        d = _deps(tmp_path, cfg=fake_cfg(line_enabled=True))
        run_dir = d.home / "runs" / "20261015-0900-ab12"
        run_dir.mkdir(parents=True)

        from daily_agenda.scan_raw import save_records, save_source_status
        save_records([], run_dir)
        save_source_status({"line": {"status": "failed", "code": "LOCAL_READER_FAILED"}}, run_dir)

        result = _run(["criteria", "draft", "--from-run", "20261015-0900-ab12"], d)
        assert result["ok"] is False
        assert result["code"] == "LOCAL_READER_FAILED"
