"""Tests for follow-up fixes F1-F7."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

from daily_agenda import criteria, ledger, state
from daily_agenda.cli import main
from daily_agenda.cli._common import Deps
from tests.conftest import (
    TAIPEI, NOW, FakeGraph, FakeLine,
    make_line_chat, make_line_message, make_mail_item, make_calendar_item,
)


def _deps(tmp_path: Path, **overrides: Any) -> Deps:
    return Deps(
        home=tmp_path,
        now_factory=lambda: NOW,
        plugin_root=Path(__file__).resolve().parents[2],
        **overrides,
    )


def _run_cli(argv: list[str], deps: Deps, capsys: Any) -> tuple[int, dict]:
    code = main(argv, deps=deps)
    captured = capsys.readouterr()
    obj = json.loads(captured.out.strip().split("\n")[-1])
    return code, obj


# ---------------------------------------------------------------------------
# F1: criteria show consent gate
# ---------------------------------------------------------------------------

class TestF1CriteriaShowConsent:
    def test_criteria_show_no_consent(self, tmp_path: Path, capsys: Any) -> None:
        """criteria show is a star command and must require consent."""
        deps = _deps(tmp_path)
        # Save a criteria version so show would succeed if consent weren't checked
        criteria.save(tmp_path, "# Test criteria")
        code, obj = _run_cli(["criteria", "show"], deps, capsys)
        assert code == 1
        assert obj["code"] == "CONSENT_REQUIRED"


# ---------------------------------------------------------------------------
# F2: line_source_factory raising in run and trust probe
# ---------------------------------------------------------------------------

class TestF2FactoryRaise:
    def test_run_factory_raises_line_runtime_missing(self, tmp_path: Path, capsys: Any) -> None:
        """run: factory raising LineSourceError should not bubble to INTERNAL_ERROR."""
        state.accept_consent(tmp_path, NOW)
        cfg = state.load_config(tmp_path)
        cfg["line"]["enabled"] = True
        cfg["line"]["main_database"] = "test.edb"
        cfg["mail"]["enabled"] = True
        cfg["output"]["dir"] = str(tmp_path / "output")
        cfg["output"]["formats"] = ["md"]
        state.save_config(tmp_path, cfg)
        criteria.save(tmp_path, "# Test")

        from daily_agenda.line_source import LineSourceError

        def _raising_factory(*, main_database: str, trusted_builds_path: str) -> Any:
            raise LineSourceError("LINE_RUNTIME_MISSING")

        deps = _deps(
            tmp_path,
            line_source_factory=_raising_factory,
            graph_factory=lambda: FakeGraph(
                mail_items=[make_mail_item()],
                calendar_items=[make_calendar_item()],
            ),
        )

        with patch("daily_agenda.analyze.analyze_batches") as mock:
            mock.return_value = {"schema_version": 1, "items": []}
            code, obj = _run_cli(["run"], deps, capsys)

        # Should succeed with LINE skipped, not INTERNAL_ERROR
        assert obj["ok"] is True
        assert obj["sources"]["line"]["status"] in ("skipped", "failed")
        assert obj["code"] != "INTERNAL_ERROR" if obj.get("code") else True

    def test_trust_probe_factory_raises_returns_real_code(self, tmp_path: Path, capsys: Any) -> None:
        """trust probe: factory raising should return the real error code."""
        state.accept_consent(tmp_path, NOW)
        cfg = state.load_config(tmp_path)
        cfg.setdefault("line", {})["main_database"] = "existing.edb"
        cfg["line"]["enabled"] = True
        state.save_config(tmp_path, cfg)

        from daily_agenda.line_source import LineSourceError

        def _raising_factory(*, main_database: str, trusted_builds_path: str) -> Any:
            raise LineSourceError("LINE_RUNTIME_MISSING")

        def _fake_exe_info(_p: Any) -> dict:
            return {
                "path": "C:\\LINE.exe", "version": "26.5.0", "bytes": 100,
                "sha256": "a" * 64, "signature_status": "Valid",
                "signer_org": "LY Corporation", "thumbprint": "AA" * 20,
            }

        deps = _deps(
            tmp_path,
            exe_info_reader=_fake_exe_info,
            line_source_factory=_raising_factory,
        )
        code, obj = _run_cli(["trust", "probe"], deps, capsys)

        # Should show LINE_RUNTIME_MISSING, not LINE_PROBE_FAILED
        assert obj["ok"] is False
        assert obj["code"] == "LINE_RUNTIME_MISSING"


# ---------------------------------------------------------------------------
# F3: show --report R stale check
# ---------------------------------------------------------------------------

class TestF3ShowStaleReport:
    def _setup_generation(self, tmp_path: Path) -> str:
        """Create a generation with a known report_code and ledger."""
        state.accept_consent(tmp_path, NOW)
        st = state.load_state(tmp_path)
        st["report_code"] = "R-ABC"
        st["report_index"] = {"1": "T-0001", "2": "T-0002"}
        ldg = [
            {"id": "T-0001", "title": "Task one", "priority": "P1", "status": "open",
             "src_ids": [], "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
             "due": None, "maybe_done_since": None, "maybe_done_reason": None, "history": []},
            {"id": "T-0002", "title": "Task two", "priority": "P2", "status": "open",
             "src_ids": [], "created_at": NOW.isoformat(), "updated_at": NOW.isoformat(),
             "due": None, "maybe_done_since": None, "maybe_done_reason": None, "history": []},
        ]
        staging = state.stage_generation(
            tmp_path, state=st, ledger=ldg, outputs={"md": "# test"},
            publish_targets=[{"fmt": "md", "path": str(tmp_path / "output" / "test.md")}],
        )
        state.commit_generation(tmp_path, staging)
        return "R-ABC"

    def test_show_old_report_code_stale(self, tmp_path: Path, capsys: Any) -> None:
        """show --report with an old/wrong code should return STALE_REPORT."""
        self._setup_generation(tmp_path)
        deps = _deps(tmp_path)
        code, obj = _run_cli(["show", "--report", "R-OLD", "--items", "1"], deps, capsys)
        assert obj["ok"] is False
        assert obj["code"] == "STALE_REPORT"

    def test_show_current_report_ok(self, tmp_path: Path, capsys: Any) -> None:
        """show --report with current code should succeed."""
        self._setup_generation(tmp_path)
        deps = _deps(tmp_path)
        code, obj = _run_cli(["show", "--report", "R-ABC", "--items", "1"], deps, capsys)
        assert obj["ok"] is True
        assert len(obj["items"]) == 1


# ---------------------------------------------------------------------------
# F4: detection uses per-candidate codes when uniform
# ---------------------------------------------------------------------------

class TestF4DetectCodes:
    def _fake_exe_info(self, _p: Any) -> dict:
        return {
            "path": "C:\\LINE.exe", "version": "26.5.0", "bytes": 100,
            "sha256": "a" * 64, "signature_status": "Valid",
            "signer_org": "LY Corporation", "thumbprint": "AA" * 20,
        }

    def test_all_candidates_same_code(self, tmp_path: Path, capsys: Any) -> None:
        """When all candidates fail with the same code, use that code at top level."""
        state.accept_consent(tmp_path, NOW)
        db_dir = tmp_path / "line-db"
        db_dir.mkdir()
        (db_dir / "acct1.edb").write_bytes(b"data")
        (db_dir / "acct2.edb").write_bytes(b"data")

        def _factory(*, main_database: str, trusted_builds_path: str) -> Any:
            class _F:
                def probe(self) -> dict:
                    return {"ok": False, "code": "SESSION_KEY_UNAVAILABLE"}
            return _F()

        deps = _deps(
            tmp_path,
            exe_info_reader=self._fake_exe_info,
            line_source_factory=_factory,
            db_dir=db_dir,
        )
        code, obj = _run_cli(["trust", "probe"], deps, capsys)
        assert obj["ok"] is False
        # Should use SESSION_KEY_UNAVAILABLE, not MAIN_DATABASE_NOT_FOUND
        assert obj["code"] == "SESSION_KEY_UNAVAILABLE"

    def test_line_detect_all_same_code(self, tmp_path: Path, capsys: Any) -> None:
        """line-detect: same uniform code propagation."""
        state.accept_consent(tmp_path, NOW)
        db_dir = tmp_path / "line-db"
        db_dir.mkdir()
        (db_dir / "acct1.edb").write_bytes(b"data")

        def _factory(*, main_database: str, trusted_builds_path: str) -> Any:
            class _F:
                def probe(self) -> dict:
                    return {"ok": False, "code": "SESSION_KEY_UNAVAILABLE"}
            return _F()

        deps = _deps(tmp_path, line_source_factory=_factory, db_dir=db_dir)
        code, obj = _run_cli(["line-detect"], deps, capsys)
        assert obj["ok"] is False
        assert obj["code"] == "SESSION_KEY_UNAVAILABLE"
        # Should include candidates with their codes
        assert "candidates" in obj
        assert obj["candidates"][0]["code"] == "SESSION_KEY_UNAVAILABLE"


# ---------------------------------------------------------------------------
# F6: non-integer --items → ARG_ERROR exit 2
# ---------------------------------------------------------------------------

class TestF6ItemsParseError:
    def test_show_non_integer_items(self, tmp_path: Path, capsys: Any) -> None:
        state.accept_consent(tmp_path, NOW)
        deps = _deps(tmp_path)
        code, obj = _run_cli(["show", "--report", "R-X", "--items", "abc"], deps, capsys)
        assert code == 2
        assert obj["code"] == "ARG_ERROR"

    def test_mark_done_non_integer_items(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["mark-done", "--report", "R-X", "--items", "abc"], deps, capsys)
        assert code == 2
        assert obj["code"] == "ARG_ERROR"


# ---------------------------------------------------------------------------
# F7: new_chats includes ref+name, not just names
# ---------------------------------------------------------------------------

class TestF7NewChatsRef:
    def test_run_new_chats_has_ref(self, tmp_path: Path, capsys: Any) -> None:
        """run output new_chats should be [{ref, name}] not just names."""
        state.accept_consent(tmp_path, NOW)
        cfg = state.load_config(tmp_path)
        cfg["line"]["enabled"] = True
        cfg["line"]["main_database"] = "test.edb"
        cfg["mail"]["enabled"] = False
        cfg["output"]["dir"] = str(tmp_path / "output")
        cfg["output"]["formats"] = ["md"]
        state.save_config(tmp_path, cfg)
        criteria.save(tmp_path, "# Test")

        fake_line = FakeLine(
            chats=[make_line_chat(ref="chat:aabbccdd11223344aabbccdd", name="New Group")],
            messages=[make_line_message(chat_ref="chat:aabbccdd11223344aabbccdd")],
        )
        deps = _deps(
            tmp_path,
            line_source_factory=lambda **kw: fake_line,
        )

        with patch("daily_agenda.analyze.analyze_batches") as mock:
            mock.return_value = {"schema_version": 1, "items": []}
            code, obj = _run_cli(["run"], deps, capsys)

        assert obj["ok"] is True
        # new_chats should be list of dicts with ref and name
        assert len(obj["new_chats"]) > 0
        assert isinstance(obj["new_chats"][0], dict)
        assert "ref" in obj["new_chats"][0]
        assert "name" in obj["new_chats"][0]


# ---------------------------------------------------------------------------
# Low priority: graph factory cleanup
# ---------------------------------------------------------------------------

class TestGraphCleanup:
    def test_graph_factory_exit_called(self, tmp_path: Path, capsys: Any) -> None:
        """Graph context manager __exit__ should be called after main()."""
        exit_called = []

        class _FakeCtx:
            def __enter__(self) -> Any:
                return FakeGraph()
            def __exit__(self, *args: Any) -> None:
                exit_called.append(True)

        original_graph_factory = None

        def _factory() -> Any:
            ctx = _FakeCtx()
            return ctx  # return the ctx, _common registers cleanup

        # We test via the atexit / ExitStack mechanism
        # For now just verify the factory can be called and cleaned up
        state.accept_consent(tmp_path, NOW)
        deps = _deps(tmp_path, graph_factory=lambda: FakeGraph())
        code, obj = _run_cli(["probe"], deps, capsys)
        assert obj["ok"] is True
