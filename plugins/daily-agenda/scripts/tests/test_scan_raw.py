"""Tests for the scan_raw module."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daily_agenda.scan_raw import (
    write_raw_sample, resolve_raw_for_todo,
    save_records, load_records, save_source_status, load_source_status,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------


def _make_line_record(
    chat_ref: str = "chat:aabb",
    sender: str = "Taro",
    text: str = "Hello",
    ts: str = "2026-10-15T09:00:00+08:00",
    src_id: str = "line:chat:aabb:message:1111",
) -> dict:
    return {
        "kind": "line_message",
        "src_id": src_id,
        "chat_ref": chat_ref,
        "ts": ts,
        "untrusted": {"chat_name": "Test Group", "sender": sender, "text": text},
    }


def _make_mail_record(
    folder: str = "inbox",
    sender: str = "boss@example.com",
    subject: str = "Test Mail",
    body: str = "Hello body",
    ts: str = "2026-10-15T09:00:00+08:00",
    src_id: str = "mail:AAMk-1",
) -> dict:
    return {
        "kind": "mail",
        "src_id": src_id,
        "folder": folder,
        "received_at": ts,
        "untrusted": {"from": sender, "subject": subject, "body": body},
    }


# ---------------------------------------------------------------------------
# write_raw_sample
# ---------------------------------------------------------------------------


class TestWriteRawSample:
    def test_basic_line(self, tmp_path: Path) -> None:
        records = [_make_line_record()]
        result = write_raw_sample(records, tmp_path / "raw")
        assert result["ok"] is True
        assert result["line_chats"] == 1
        assert len(result["files"]) >= 2  # at least 1 chat file + stats

    def test_basic_mail(self, tmp_path: Path) -> None:
        records = [_make_mail_record()]
        result = write_raw_sample(records, tmp_path / "raw")
        assert result["ok"] is True
        assert result["mail_folders"] == 1

    def test_mixed(self, tmp_path: Path) -> None:
        records = [_make_line_record(), _make_mail_record()]
        result = write_raw_sample(records, tmp_path / "raw")
        assert result["line_chats"] == 1
        assert result["mail_folders"] == 1

    def test_top_chats_cap(self, tmp_path: Path) -> None:
        records = []
        for i in range(30):
            records.append(_make_line_record(
                chat_ref=f"chat:{i:04d}",
                src_id=f"line:chat:{i:04d}:message:0001",
            ))
        result = write_raw_sample(records, tmp_path / "raw")
        assert result["line_chats"] == 20  # capped at TOP_CHATS

    def test_total_chars_cap(self, tmp_path: Path) -> None:
        long_text = "x" * 600
        records = []
        for i in range(500):
            records.append(_make_line_record(
                chat_ref="chat:big",
                text=long_text,
                src_id=f"line:chat:big:message:{i:04d}",
            ))
        result = write_raw_sample(records, tmp_path / "raw")
        assert result["total_chars"] <= 200_000

    def test_message_truncation(self, tmp_path: Path) -> None:
        long_text = "x" * 1000
        records = [_make_line_record(text=long_text)]
        result = write_raw_sample(records, tmp_path / "raw")
        # Read the output file
        for f in result["files"]:
            if "line" in f:
                content = Path(f).read_text(encoding="utf-8")
                # Should be truncated (not all 1000 chars of x)
                assert len(content) < 1000 + 200  # metadata overhead

    def test_safe_filenames(self, tmp_path: Path) -> None:
        """Chat names with path separators must not create subdirectories."""
        records = [_make_line_record(chat_ref="chat:../../etc/passwd")]
        result = write_raw_sample(records, tmp_path / "raw")
        # All output files should be directly under the raw dir
        for f in result["files"]:
            assert Path(f).parent == tmp_path / "raw"

    def test_stats_file(self, tmp_path: Path) -> None:
        records = [_make_line_record(), _make_mail_record()]
        result = write_raw_sample(records, tmp_path / "raw")
        stats_path = tmp_path / "raw" / "stats.json"
        assert stats_path.exists()
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        assert "total_chars" in stats
        assert "message_counts_per_chat" in stats

    def test_per_sender_counts(self, tmp_path: Path) -> None:
        records = [
            _make_line_record(sender="Alice", src_id="line:chat:a:m:1"),
            _make_line_record(sender="Alice", src_id="line:chat:a:m:2"),
            _make_line_record(sender="Bob", src_id="line:chat:a:m:3"),
            _make_mail_record(sender="charlie@example.com", src_id="mail:m1"),
        ]
        result = write_raw_sample(records, tmp_path / "raw")
        stats_path = tmp_path / "raw" / "stats.json"
        stats = json.loads(stats_path.read_text(encoding="utf-8"))
        per_sender = stats["message_counts_per_sender"]
        assert per_sender["Alice"] == 2
        assert per_sender["Bob"] == 1
        assert per_sender["charlie@example.com"] == 1

    def test_empty_records(self, tmp_path: Path) -> None:
        result = write_raw_sample([], tmp_path / "raw")
        assert result["ok"] is True
        assert result["line_chats"] == 0
        assert result["mail_folders"] == 0


# ---------------------------------------------------------------------------
# resolve_raw_for_todo
# ---------------------------------------------------------------------------


class TestResolveRawForTodo:
    def test_todo_not_found(self, tmp_path: Path) -> None:
        result = resolve_raw_for_todo("T-9999", [], tmp_path / "runs")
        assert result["ok"] is False
        assert result["code"] == "TODO_NOT_FOUND"

    def test_no_src_ids(self, tmp_path: Path) -> None:
        ledger = [{"id": "T-0001", "src_ids": []}]
        result = resolve_raw_for_todo("T-0001", ledger, tmp_path / "runs")
        assert result["ok"] is False
        assert result["code"] == "NO_SRC_IDS"

    def test_found_in_bundle(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "20261015-0900-ab12"
        run_dir.mkdir(parents=True)
        bundle = run_dir / "bundle-1.jsonl"
        rec = {"kind": "mail", "src_id": "mail:AAMk-1", "untrusted": {"subject": "Test"}}
        bundle.write_text(json.dumps(rec) + "\n", encoding="utf-8")

        ledger = [{"id": "T-0001", "src_ids": ["mail:AAMk-1"]}]
        result = resolve_raw_for_todo("T-0001", ledger, runs_dir)
        assert result["ok"] is True
        assert result["found"] == 1
        assert len(result["missing"]) == 0
        assert len(result["records"]) == 1
        # Records should be fenced
        assert "UNTRUSTED-" in result["records"][0]

    def test_partial_found(self, tmp_path: Path) -> None:
        runs_dir = tmp_path / "runs"
        run_dir = runs_dir / "20261015-0900-ab12"
        run_dir.mkdir(parents=True)
        bundle = run_dir / "bundle-1.jsonl"
        rec = {"kind": "mail", "src_id": "mail:AAMk-1", "untrusted": {"subject": "Test"}}
        bundle.write_text(json.dumps(rec) + "\n", encoding="utf-8")

        ledger = [{"id": "T-0001", "src_ids": ["mail:AAMk-1", "mail:AAMk-2"]}]
        result = resolve_raw_for_todo("T-0001", ledger, runs_dir)
        assert result["ok"] is True
        assert result["found"] == 1
        assert result["missing"] == ["mail:AAMk-2"]

    def test_no_runs_dir(self, tmp_path: Path) -> None:
        ledger = [{"id": "T-0001", "src_ids": ["mail:AAMk-1"]}]
        result = resolve_raw_for_todo("T-0001", ledger, tmp_path / "nonexistent")
        assert result["ok"] is True
        assert result["found"] == 0
        assert len(result["missing"]) == 1


# ---------------------------------------------------------------------------
# Records persistence (for criteria draft --from-run)
# ---------------------------------------------------------------------------


class TestRecordsPersistence:
    def test_save_and_load_records(self, tmp_path: Path) -> None:
        records = [_make_line_record(), _make_mail_record()]
        path = save_records(records, tmp_path)
        assert path.exists()
        loaded = load_records(tmp_path)
        assert len(loaded) == 2
        assert loaded[0]["kind"] == "line_message"
        assert loaded[1]["kind"] == "mail"

    def test_load_missing_records(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_records(tmp_path)

    def test_save_empty_records(self, tmp_path: Path) -> None:
        save_records([], tmp_path)
        loaded = load_records(tmp_path)
        assert loaded == []

    def test_save_and_load_source_status(self, tmp_path: Path) -> None:
        status = {"line": {"status": "ok", "chats": 10}, "mail": {"status": "failed", "code": "AUTH"}}
        save_source_status(status, tmp_path)
        loaded = load_source_status(tmp_path)
        assert loaded == status

    def test_load_missing_source_status(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            load_source_status(tmp_path)
