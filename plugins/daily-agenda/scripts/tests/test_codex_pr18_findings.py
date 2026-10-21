"""Regressions for the Codex pre-merge review of PR #18 (2026-10-21).

2. Ledger writers (confirm / mark-done / …) take the run lock → LOCKED while a run is in flight.
3. Cross-batch merge drops entries whose ids are not strings instead of emptying every digest.
4. needs_reply src_id unknown to the batch is dropped (not only wrong-chat ids).
5. exclude persists the exclusion before deleting history; deletion failures are reported; run re-sweeps.
6. DA open finds the receipt of an older generation after a ledger-only commit.
"""

from __future__ import annotations

import io
import json
import os
from pathlib import Path
from typing import Any
from unittest.mock import patch

from daily_agenda import history, state
from daily_agenda.cli import main
from daily_agenda.cli._common import Deps
from daily_agenda.cli.cmd_run import _merge_chat_digests, _merge_mail_digests, _sweep_excluded_history
from daily_agenda.validate import _validate_chat_digests
from tests.conftest import NOW
from tests.test_confirm import _setup_generation

REF_A = "chat:aaaaaaaaaaaaaaaaaaaaaaaa"


def _deps(tmp_path: Path, **overrides: Any) -> Deps:
    kw: dict[str, Any] = {"home": tmp_path, "now_factory": lambda: NOW,
                          "plugin_root": Path(__file__).resolve().parents[2]}
    kw.update(overrides)
    return Deps(**kw)


def _run(argv: list[str], deps: Deps, capsys: Any, stdin_text: str = "") -> tuple[int, dict]:
    with patch("sys.stdin", io.StringIO(stdin_text)):
        code = main(argv, deps=deps)
    lines = [ln for ln in capsys.readouterr().out.strip().split("\n") if ln.strip()]
    return code, json.loads(lines[-1])


# ---------------------------------------------------------------------------
# 2. lock
# ---------------------------------------------------------------------------

class TestLedgerWritersTakeRunLock:
    def test_confirm_is_locked_while_run_holds_lock(self, tmp_path: Path, capsys: Any) -> None:
        report_code, _ = _setup_generation(tmp_path)
        before = state.load_ledger(tmp_path)
        with state.acquire_lock(tmp_path):
            code, obj = _run(["confirm", "--from-stdin"], _deps(tmp_path), capsys,
                             stdin_text=f"確認待辦 {report_code}\n完成：1\n其餘保留")
        assert code == 1 and obj["code"] == "LOCKED"
        assert state.load_ledger(tmp_path) == before  # nothing committed

    def test_mark_done_is_locked_while_run_holds_lock(self, tmp_path: Path, capsys: Any) -> None:
        report_code, _ = _setup_generation(tmp_path)
        with state.acquire_lock(tmp_path):
            code, obj = _run(["mark-done", "--report", report_code, "--items", "1"], _deps(tmp_path), capsys)
        assert obj["code"] == "LOCKED"

    def test_confirm_releases_lock_after_commit(self, tmp_path: Path, capsys: Any) -> None:
        report_code, _ = _setup_generation(tmp_path)
        _, obj = _run(["confirm", "--from-stdin"], _deps(tmp_path), capsys,
                      stdin_text=f"確認待辦 {report_code}\n完成：1\n其餘保留")
        assert obj["ok"] is True
        with state.acquire_lock(tmp_path):  # must not raise Locked
            pass


# ---------------------------------------------------------------------------
# 3. merge with bad ids
# ---------------------------------------------------------------------------

class TestMergeDropsBadIdsOnly:
    def test_unhashable_needs_reply_src_id_does_not_empty_digests(self) -> None:
        good = {"chat_ref": REF_A, "bullets": ["a"], "needs_reply": [{"src_id": "line:x:1", "ask": "q"}]}
        bad = {"chat_ref": REF_A, "bullets": ["b"], "needs_reply": [{"src_id": {"nested": 1}, "ask": "q"}]}
        out = _merge_chat_digests([[good], [bad]])
        assert len(out) == 1
        assert out[0]["bullets"] == ["a", "b"]
        assert [n["src_id"] for n in out[0]["needs_reply"]] == ["line:x:1"]

    def test_non_string_chat_ref_dropped_not_fatal(self) -> None:
        good = {"chat_ref": REF_A, "bullets": ["a"]}
        bad = {"chat_ref": ["list"], "bullets": ["b"]}
        out = _merge_chat_digests([[bad, good]])
        assert [d["chat_ref"] for d in out] == [REF_A]

    def test_non_string_mail_src_id_dropped_not_fatal(self) -> None:
        out = _merge_mail_digests([[{"src_id": "mail:1", "summary": "s"}, {"src_id": 7, "summary": "t"}]])
        assert [d["src_id"] for d in out] == ["mail:1"]


# ---------------------------------------------------------------------------
# 4. unknown needs_reply src_id
# ---------------------------------------------------------------------------

class TestNeedsReplyUnknownSrcIdDropped:
    RECORDS = [
        {"kind": "line_message", "chat_ref": REF_A, "src_id": "line:a:1", "untrusted": {"is_self": False}},
        {"kind": "line_message", "chat_ref": "chat:bbbbbbbbbbbbbbbbbbbbbbbb", "src_id": "line:b:1",
         "untrusted": {"is_self": False}},
    ]

    def _nr(self, src_id: str) -> list[dict]:
        raw = [{"chat_ref": REF_A, "bullets": ["x"], "needs_reply": [{"src_id": src_id, "ask": "q"}]}]
        out = _validate_chat_digests(raw, self.RECORDS)
        return out[0]["needs_reply"] if out else []

    def test_known_same_chat_kept(self) -> None:
        assert [n["src_id"] for n in self._nr("line:a:1")] == ["line:a:1"]

    def test_wrong_chat_dropped(self) -> None:
        assert self._nr("line:b:1") == []

    def test_unknown_id_dropped(self) -> None:
        assert self._nr("line:a:does-not-exist") == []


# ---------------------------------------------------------------------------
# 5. exclude ordering / failure reporting / run sweep
# ---------------------------------------------------------------------------

class TestExcludeHistoryOrdering:
    def test_exclusion_persisted_even_if_history_delete_fails(self, tmp_path: Path, capsys: Any,
                                                              monkeypatch: Any) -> None:
        def _boom(home: Path, ref: str) -> int:
            raise RuntimeError("disk")
        monkeypatch.setattr(history, "delete_by_chat_ref", _boom)
        code, obj = _run(["exclude", "--set", REF_A], _deps(tmp_path), capsys)
        assert code == 0 and obj["ok"] is True
        assert obj["history_cleanup_failed"] == 1
        assert "舊摘要" in obj["message"]
        assert [c["ref"] for c in state.load_config(tmp_path)["line"]["excluded_chats"]] == [REF_A]

    def test_history_deleted_after_config_saved(self, tmp_path: Path, capsys: Any, monkeypatch: Any) -> None:
        order: list[str] = []
        real_save = state.save_config

        def _save(h: Path, cfg: dict) -> None:
            order.append("config")
            real_save(h, cfg)

        def _delete(home: Path, ref: str) -> int:
            order.append("history")
            return 0
        monkeypatch.setattr(state, "save_config", _save)
        monkeypatch.setattr(history, "delete_by_chat_ref", _delete)
        _, obj = _run(["exclude", "--add", REF_A], _deps(tmp_path), capsys)
        assert obj["ok"] is True and "history_cleanup_failed" not in obj
        assert order[:2] == ["config", "history"]

    def test_run_sweep_retries_excluded_history(self, tmp_path: Path, monkeypatch: Any) -> None:
        seen: list[str] = []
        monkeypatch.setattr(history, "delete_by_chat_ref", lambda home, ref: seen.append(ref) or 0)
        cfg = {"line": {"excluded_chats": [{"ref": REF_A, "name": ""}, {"ref": "chat:b", "name": ""}]}}
        _sweep_excluded_history(tmp_path, cfg)
        assert seen == [REF_A, "chat:b"]


# ---------------------------------------------------------------------------
# 6. DA open after a ledger-only generation
# ---------------------------------------------------------------------------

class TestOpenAfterConfirm:
    def test_open_walks_back_to_published_generation(self, tmp_path: Path, capsys: Any, monkeypatch: Any) -> None:
        report_code, _ = _setup_generation(tmp_path)
        # Simulate the run's publish receipt on generation 1.
        gen = state.current_generation(tmp_path)
        assert gen is not None
        out = tmp_path / "output" / "test.md"
        out.parent.mkdir(parents=True, exist_ok=True)
        out.write_text("# test", encoding="utf-8")
        (state._gen_dir(tmp_path, gen) / "published").write_text(
            json.dumps({"at": NOW.isoformat(), "targets": [{"path": str(out), "sha256": "x"}]}), encoding="utf-8")
        # confirm commits generation 2 without a receipt
        _, obj = _run(["confirm", "--from-stdin"], _deps(tmp_path), capsys,
                      stdin_text=f"確認待辦 {report_code}\n完成：1\n其餘保留")
        assert obj["ok"] is True
        assert state.current_generation(tmp_path) == gen + 1
        assert not (state._gen_dir(tmp_path, gen + 1) / "published").exists()

        opened: list[str] = []
        monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
        code, obj = _run(["open", "--format", "md"], _deps(tmp_path), capsys)
        assert code == 0 and obj["opened"] is True and opened == [str(out)]
