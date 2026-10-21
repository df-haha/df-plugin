"""Tests for Phase B: chat_history records in bundles (C4 §B).

chat_history records carry past AI digests as untrusted context for the
analyzer.  They must:
  - appear in the bundle only for chat_refs that have line_message records
  - contain no src_id (they must never become citeable sources)
  - not pollute result.records
  - respect batch_bytes when included in batch sizing
  - degrade gracefully when history DB is missing or corrupt
"""

from __future__ import annotations

import json
import sqlite3
from pathlib import Path
from typing import Any

import pytest

from daily_agenda.bundle import split_batches, write_bundle
from daily_agenda.fsutil import jsonl_line
from daily_agenda.history import insert_digests, read_last
from tests.conftest import NOW_ISO


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CHAT_A = "chat:aabbccdd11223344aabbcc01"
CHAT_B = "chat:aabbccdd11223344aabbcc02"
CHAT_C = "chat:aabbccdd11223344aabbcc03"
RUN_OLD_1 = "20261005-0900-aa11"
RUN_OLD_2 = "20261006-0900-bb22"
RUN_OLD_3 = "20261007-0900-cc33"
RUN_OLD_4 = "20261004-0900-dd44"
RUN_CURRENT = "20261008-0900-ee55"


def _base_result(**overrides: Any) -> dict[str, Any]:
    base = {
        "run_id": RUN_CURRENT,
        "generated_at": NOW_ISO,
        "tz": "Asia/Taipei",
        "source_status": {},
        "new_chats": [],
        "records": [],
        "criteria_version": 1,
    }
    base.update(overrides)
    return base


def _seed_history(
    home: Path,
    chat_ref: str = CHAT_A,
    runs: list[tuple[str, str, list[str]]] | None = None,
) -> None:
    """Seed the history DB with digest rows.

    *runs* is a list of (run_id, created_at, bullets).
    """
    if runs is None:
        runs = [
            (RUN_OLD_1, "2026-10-05T09:00:00+08:00", ["older bullet A", "older bullet B"]),
            (RUN_OLD_2, "2026-10-06T09:00:00+08:00", ["mid bullet"]),
            (RUN_OLD_3, "2026-10-07T09:00:00+08:00", ["newest bullet 1", "newest bullet 2", "newest bullet 3"]),
        ]
    for run_id, created_at, bullets in runs:
        insert_digests(home, run_id, created_at, [{
            "chat_ref": chat_ref,
            "bullets": bullets,
            "needs_reply": [{"src_id": "stale:old:src", "ask": "old question"}],
            "quiet": False,
            "chat_name": "Test Group",
        }])


def _load_chat_history_for_bundle(
    home: Path,
    records: list[dict[str, Any]],
    run_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Replicate the helper that cmd_run should use."""
    from daily_agenda.bundle import load_chat_history
    return load_chat_history(home, records, run_id)


# ---------------------------------------------------------------------------
# Tests: chat_history record shape in bundle
# ---------------------------------------------------------------------------


class TestChatHistoryInBundle:
    """chat_history records written into the bundle JSONL."""

    def test_history_record_emitted_for_chat_with_history(self, tmp_path: Path) -> None:
        _seed_history(tmp_path)
        records = [
            {"kind": "line_message", "src_id": "line:a:m1", "chat_ref": CHAT_A,
             "untrusted": {"text": "hello"}},
        ]
        ch = _load_chat_history_for_bundle(tmp_path, records, RUN_CURRENT)

        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=_base_result(records=records),
                     criteria=None, open_todos=[], ctx=[], chat_history=ch)

        lines = [json.loads(l) for l in path.read_text("utf-8").strip().splitlines()]
        hist_lines = [l for l in lines if l.get("kind") == "chat_history"]
        assert len(hist_lines) == 1
        h = hist_lines[0]
        assert h["chat_ref"] == CHAT_A
        assert "untrusted" in h
        entries = h["untrusted"]["entries"]
        assert len(entries) <= 3
        # newest first
        assert entries[0]["date"] == "2026-10-07"
        for e in entries:
            assert len(e["bullets"]) <= 5

    def test_no_history_record_when_chat_has_no_history(self, tmp_path: Path) -> None:
        # No history seeded
        records = [
            {"kind": "line_message", "src_id": "line:a:m1", "chat_ref": CHAT_A,
             "untrusted": {"text": "hello"}},
        ]
        ch = _load_chat_history_for_bundle(tmp_path, records, RUN_CURRENT)

        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=_base_result(records=records),
                     criteria=None, open_todos=[], ctx=[], chat_history=ch)

        lines = [json.loads(l) for l in path.read_text("utf-8").strip().splitlines()]
        hist_lines = [l for l in lines if l.get("kind") == "chat_history"]
        assert len(hist_lines) == 0

    def test_no_history_record_for_non_line_message(self, tmp_path: Path) -> None:
        _seed_history(tmp_path, chat_ref="mail:xyz")
        records = [
            {"kind": "mail", "src_id": "mail:xyz", "untrusted": {"subject": "test"}},
        ]
        ch = _load_chat_history_for_bundle(tmp_path, records, RUN_CURRENT)

        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=_base_result(records=records),
                     criteria=None, open_todos=[], ctx=[], chat_history=ch)

        lines = [json.loads(l) for l in path.read_text("utf-8").strip().splitlines()]
        hist_lines = [l for l in lines if l.get("kind") == "chat_history"]
        assert len(hist_lines) == 0

    def test_history_record_has_no_src_id(self, tmp_path: Path) -> None:
        """chat_history must never be citeable — no src_id field."""
        _seed_history(tmp_path)
        records = [
            {"kind": "line_message", "src_id": "line:a:m1", "chat_ref": CHAT_A,
             "untrusted": {"text": "hello"}},
        ]
        ch = _load_chat_history_for_bundle(tmp_path, records, RUN_CURRENT)

        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=_base_result(records=records),
                     criteria=None, open_todos=[], ctx=[], chat_history=ch)

        lines = [json.loads(l) for l in path.read_text("utf-8").strip().splitlines()]
        hist_lines = [l for l in lines if l.get("kind") == "chat_history"]
        assert len(hist_lines) == 1
        assert "src_id" not in hist_lines[0]

    def test_history_record_does_not_leak_needs_reply(self, tmp_path: Path) -> None:
        """Stale needs_reply (old src_ids) must not appear in the bundle."""
        _seed_history(tmp_path)
        records = [
            {"kind": "line_message", "src_id": "line:a:m1", "chat_ref": CHAT_A,
             "untrusted": {"text": "hello"}},
        ]
        ch = _load_chat_history_for_bundle(tmp_path, records, RUN_CURRENT)

        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=_base_result(records=records),
                     criteria=None, open_todos=[], ctx=[], chat_history=ch)

        raw = path.read_text("utf-8")
        assert "needs_reply" not in raw
        assert "stale:old:src" not in raw

    def test_history_excludes_current_run_id(self, tmp_path: Path) -> None:
        """History must not include the current run's digests."""
        _seed_history(tmp_path)
        # Also insert a digest for the "current" run
        insert_digests(tmp_path, RUN_CURRENT, NOW_ISO, [{
            "chat_ref": CHAT_A, "bullets": ["current run bullet"],
            "needs_reply": [], "quiet": False, "chat_name": "Test",
        }])

        records = [
            {"kind": "line_message", "src_id": "line:a:m1", "chat_ref": CHAT_A,
             "untrusted": {"text": "hello"}},
        ]
        ch = _load_chat_history_for_bundle(tmp_path, records, RUN_CURRENT)

        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=_base_result(records=records),
                     criteria=None, open_todos=[], ctx=[], chat_history=ch)

        raw = path.read_text("utf-8")
        assert "current run bullet" not in raw

    def test_max_3_entries_and_5_bullets(self, tmp_path: Path) -> None:
        """At most 3 entries × 5 bullets each."""
        runs = [
            (RUN_OLD_4, "2026-10-04T09:00:00+08:00", [f"b4-{i}" for i in range(7)]),
            (RUN_OLD_1, "2026-10-05T09:00:00+08:00", [f"b5-{i}" for i in range(7)]),
            (RUN_OLD_2, "2026-10-06T09:00:00+08:00", [f"b6-{i}" for i in range(7)]),
            (RUN_OLD_3, "2026-10-07T09:00:00+08:00", [f"b7-{i}" for i in range(7)]),
        ]
        _seed_history(tmp_path, runs=runs)

        records = [
            {"kind": "line_message", "src_id": "line:a:m1", "chat_ref": CHAT_A,
             "untrusted": {"text": "hello"}},
        ]
        ch = _load_chat_history_for_bundle(tmp_path, records, RUN_CURRENT)

        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=_base_result(records=records),
                     criteria=None, open_todos=[], ctx=[], chat_history=ch)

        lines = [json.loads(l) for l in path.read_text("utf-8").strip().splitlines()]
        hist_lines = [l for l in lines if l.get("kind") == "chat_history"]
        assert len(hist_lines) == 1
        entries = hist_lines[0]["untrusted"]["entries"]
        assert len(entries) == 3  # not 4
        for e in entries:
            assert len(e["bullets"]) <= 5

    def test_write_bundle_without_history_param(self, tmp_path: Path) -> None:
        """Backward compat: omitting chat_history still works."""
        records = [
            {"kind": "line_message", "src_id": "line:a:m1", "chat_ref": CHAT_A,
             "untrusted": {"text": "hello"}},
        ]
        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=_base_result(records=records),
                     criteria=None, open_todos=[], ctx=[])
        lines = [json.loads(l) for l in path.read_text("utf-8").strip().splitlines()]
        hist_lines = [l for l in lines if l.get("kind") == "chat_history"]
        assert len(hist_lines) == 0

    def test_multiple_chats_each_get_own_record(self, tmp_path: Path) -> None:
        _seed_history(tmp_path, chat_ref=CHAT_A)
        _seed_history(tmp_path, chat_ref=CHAT_B)
        records = [
            {"kind": "line_message", "src_id": "line:a:m1", "chat_ref": CHAT_A,
             "untrusted": {"text": "hi"}},
            {"kind": "line_message", "src_id": "line:b:m1", "chat_ref": CHAT_B,
             "untrusted": {"text": "hey"}},
        ]
        ch = _load_chat_history_for_bundle(tmp_path, records, RUN_CURRENT)

        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=_base_result(records=records),
                     criteria=None, open_todos=[], ctx=[], chat_history=ch)

        lines = [json.loads(l) for l in path.read_text("utf-8").strip().splitlines()]
        hist_lines = [l for l in lines if l.get("kind") == "chat_history"]
        assert len(hist_lines) == 2
        refs = {h["chat_ref"] for h in hist_lines}
        assert refs == {CHAT_A, CHAT_B}


# ---------------------------------------------------------------------------
# Tests: compute_history_sizes accuracy
# ---------------------------------------------------------------------------


class TestComputeHistorySizesAccuracy:
    """compute_history_sizes must match the actual bytes written by write_bundle."""

    def test_sizes_match_written_lines(self, tmp_path: Path) -> None:
        """The size reported by compute_history_sizes must equal the actual
        byte length of the chat_history line in the written bundle (including
        its trailing newline)."""
        from daily_agenda.bundle import compute_history_sizes

        _seed_history(tmp_path)
        records = [
            {"kind": "line_message", "src_id": "line:a:m1", "chat_ref": CHAT_A,
             "untrusted": {"text": "hello"}},
        ]
        ch = _load_chat_history_for_bundle(tmp_path, records, RUN_CURRENT)
        sizes = compute_history_sizes(ch)
        assert CHAT_A in sizes

        # Write a bundle and measure the actual chat_history line
        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=_base_result(records=records),
                     criteria=None, open_todos=[], ctx=[], chat_history=ch)

        raw_lines = path.read_text("utf-8").split("\n")
        hist_raw = [l for l in raw_lines if l and '"chat_history"' in l]
        assert len(hist_raw) == 1
        # +1 for the \n that separates/terminates each line in the file
        actual_size = len(hist_raw[0].encode("utf-8")) + 1
        assert sizes[CHAT_A] == actual_size

    def test_sizes_match_with_unicode_bullets(self, tmp_path: Path) -> None:
        """Verify accuracy with CJK characters (multi-byte UTF-8)."""
        from daily_agenda.bundle import compute_history_sizes

        runs = [
            (RUN_OLD_1, "2026-10-05T09:00:00+08:00",
             ["中文摘要一二三四五", "日本語テスト", "emoji test"]),
        ]
        _seed_history(tmp_path, runs=runs)
        records = [
            {"kind": "line_message", "src_id": "line:a:m1", "chat_ref": CHAT_A,
             "untrusted": {"text": "hello"}},
        ]
        ch = _load_chat_history_for_bundle(tmp_path, records, RUN_CURRENT)
        sizes = compute_history_sizes(ch)

        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=_base_result(records=records),
                     criteria=None, open_todos=[], ctx=[], chat_history=ch)

        raw_lines = path.read_text("utf-8").split("\n")
        hist_raw = [l for l in raw_lines if l and '"chat_history"' in l]
        assert len(hist_raw) == 1
        actual_size = len(hist_raw[0].encode("utf-8")) + 1
        assert sizes[CHAT_A] == actual_size


# ---------------------------------------------------------------------------
# Tests: batch sizing accounts for chat_history
# ---------------------------------------------------------------------------


class TestBatchSizingWithHistory:
    """split_batches must account for history_sizes so batches don't exceed budget."""

    def test_history_size_causes_split(self) -> None:
        """Two chats whose messages fit together, but messages+history don't."""
        rec_a = [{"kind": "line_message", "src_id": f"line:a:m{i}", "chat_ref": CHAT_A,
                   "untrusted": {"text": "x" * 50}} for i in range(3)]
        rec_b = [{"kind": "line_message", "src_id": f"line:b:m{i}", "chat_ref": CHAT_B,
                   "untrusted": {"text": "x" * 50}} for i in range(3)]

        records = rec_a + rec_b
        # Without history, should fit in one batch
        msg_size = sum(len(jsonl_line(r).encode("utf-8")) + 1 for r in records)
        # With history adding 500 bytes per chat
        history_sizes = {CHAT_A: 500, CHAT_B: 500}

        # Budget: fits messages + one chat's history, but not both
        budget = msg_size + 500 + 10  # tight

        batches = split_batches(records, budget, history_sizes=history_sizes)
        assert len(batches) == 2
        # All records preserved
        total = sum(len(b) for b in batches)
        assert total == 6

    def test_no_history_sizes_backward_compat(self) -> None:
        """Omitting history_sizes still works (no change to existing behavior)."""
        records = [{"kind": "mail", "src_id": f"mail:{i}"} for i in range(3)]
        batches = split_batches(records, batch_bytes=100_000)
        assert len(batches) == 1
        assert len(batches[0]) == 3


# ---------------------------------------------------------------------------
# Tests: resilience — corrupt/missing DB
# ---------------------------------------------------------------------------


class TestHistoryResilience:
    """Reading history must never fail the run."""

    def test_missing_db(self, tmp_path: Path) -> None:
        records = [
            {"kind": "line_message", "src_id": "line:a:m1", "chat_ref": CHAT_A,
             "untrusted": {"text": "hello"}},
        ]
        ch = _load_chat_history_for_bundle(tmp_path, records, RUN_CURRENT)
        assert ch == {}

    def test_corrupt_db(self, tmp_path: Path) -> None:
        db_path = tmp_path / "history.sqlite"
        db_path.write_bytes(b"this is not a sqlite database")

        records = [
            {"kind": "line_message", "src_id": "line:a:m1", "chat_ref": CHAT_A,
             "untrusted": {"text": "hello"}},
        ]
        ch = _load_chat_history_for_bundle(tmp_path, records, RUN_CURRENT)
        assert ch == {} or ch.get(CHAT_A) == []

    def test_empty_records(self, tmp_path: Path) -> None:
        ch = _load_chat_history_for_bundle(tmp_path, [], RUN_CURRENT)
        assert ch == {}


# ---------------------------------------------------------------------------
# Tests: validation / src_index ignore chat_history kind
# ---------------------------------------------------------------------------


class TestValidationIgnoresChatHistory:
    """chat_history records must not be treated as citeable sources."""

    def test_src_index_skips_chat_history(self) -> None:
        from daily_agenda.cli.cmd_run import _build_src_index
        records = [
            {"kind": "line_message", "src_id": "line:a:m1", "chat_ref": CHAT_A,
             "untrusted": {"chat_name": "G"}},
            {"kind": "chat_history", "chat_ref": CHAT_A,
             "untrusted": {"entries": [{"date": "2026-10-05", "bullets": ["b"]}]}},
        ]
        idx = _build_src_index(records)
        # chat_history has no src_id, so it should not appear
        assert "chat_history" not in str(idx)
        assert len(idx) == 1
        assert "line:a:m1" in idx
