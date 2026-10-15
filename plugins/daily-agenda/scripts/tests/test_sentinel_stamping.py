"""Tests for raw sentinel stamping in CLI output and on-disk files."""

from __future__ import annotations

import json
from pathlib import Path

import pytest

from daily_agenda.fence import RAW_SENTINEL
from daily_agenda.scan_raw import write_raw_sample


class TestRawSampleStamping:
    """Item 1: raw sample files (.md, stats.json) must carry the sentinel."""

    def _make_records(self) -> list[dict]:
        return [
            {
                "_raw": RAW_SENTINEL,
                "kind": "line_message",
                "src_id": "line:chat:abc:message:def",
                "chat_ref": "chat:abc",
                "chat_type": "group",
                "ts": "2026-10-16T09:00:00+08:00",
                "is_self": False,
                "untrusted": {
                    "chat_name": "Test Group",
                    "sender": "Alice",
                    "text": "Hello\nworld",
                },
            },
            {
                "_raw": RAW_SENTINEL,
                "kind": "mail",
                "src_id": "mail:immutable123",
                "internet_message_id": "<msg@example>",
                "received_at": "2026-10-16T08:00:00+08:00",
                "folder": "inbox",
                "untrusted": {
                    "from": "bob@example.com",
                    "to": "me@example.com",
                    "subject": "Test subject",
                    "body": "Line 1\nLine 2\nLine 3",
                },
            },
        ]

    def test_line_md_every_line_has_sentinel(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "raw"
        write_raw_sample(self._make_records(), out_dir)
        md_files = list(out_dir.glob("line-*.md"))
        assert len(md_files) >= 1
        for md in md_files:
            content = md.read_text(encoding="utf-8")
            for line in content.splitlines():
                if line.strip():
                    assert RAW_SENTINEL in line, \
                        f"Line missing sentinel: {line!r}"

    def test_mail_md_every_line_has_sentinel(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "raw"
        write_raw_sample(self._make_records(), out_dir)
        md_files = list(out_dir.glob("mail-*.md"))
        assert len(md_files) >= 1
        for md in md_files:
            content = md.read_text(encoding="utf-8")
            for line in content.splitlines():
                if line.strip():
                    assert RAW_SENTINEL in line, \
                        f"Line missing sentinel: {line!r}"

    def test_stats_json_has_raw_first_key(self, tmp_path: Path) -> None:
        out_dir = tmp_path / "raw"
        write_raw_sample(self._make_records(), out_dir)
        stats = json.loads((out_dir / "stats.json").read_text(encoding="utf-8"))
        assert "_raw" in stats
        assert stats["_raw"] == RAW_SENTINEL
        # Verify _raw is the first key (JSON may be pretty-printed)
        raw_text = (out_dir / "stats.json").read_text(encoding="utf-8")
        first_key = raw_text.split('"')[1]  # first quoted string
        assert first_key == "_raw", \
            f"_raw must be the first key in stats.json, got {first_key!r}"

    def test_multiline_body_stamped_per_line(self, tmp_path: Path) -> None:
        """Multi-line message text must have sentinel on EVERY line."""
        records = [{
            "_raw": RAW_SENTINEL,
            "kind": "line_message",
            "src_id": "line:chat:x:message:y",
            "chat_ref": "chat:x",
            "chat_type": "direct",
            "ts": "2026-10-16T09:00:00+08:00",
            "is_self": False,
            "untrusted": {
                "chat_name": "Multi",
                "sender": "Eve",
                "text": "line one\nline two\nline three",
            },
        }]
        out_dir = tmp_path / "raw"
        write_raw_sample(records, out_dir)
        md = next(out_dir.glob("line-*.md"))
        content = md.read_text(encoding="utf-8")
        for line in content.splitlines():
            if line.strip():
                assert RAW_SENTINEL in line


class TestRecordRawKeyFirst:
    """Item 2: _raw must be the first key so head -c N still contains it."""

    def test_line_message_raw_first_in_jsonl(self) -> None:
        from daily_agenda.fsutil import jsonl_line
        rec = {
            "_raw": RAW_SENTINEL,
            "kind": "line_message",
            "src_id": "x",
            "untrusted": {"text": "a" * 5000},
        }
        line = jsonl_line(rec)
        # First 50 bytes must contain the sentinel
        assert RAW_SENTINEL in line[:50], \
            f"Sentinel not in first 50 bytes: {line[:50]!r}"

    def test_mail_raw_first_in_jsonl(self) -> None:
        from daily_agenda.fsutil import jsonl_line
        rec = {
            "_raw": RAW_SENTINEL,
            "kind": "mail",
            "src_id": "x",
            "untrusted": {"body": "b" * 10000},
        }
        line = jsonl_line(rec)
        assert RAW_SENTINEL in line[:50]

    def test_event_raw_first_in_jsonl(self) -> None:
        from daily_agenda.fsutil import jsonl_line
        rec = {
            "_raw": RAW_SENTINEL,
            "kind": "event",
            "src_id": "x",
            "untrusted": {"subject": "c" * 5000},
        }
        line = jsonl_line(rec)
        assert RAW_SENTINEL in line[:50]


class TestRunOutputNoSentinel:
    """Guard the deliberate asymmetry: run output must never contain the sentinel."""

    def test_run_json_keys_no_sentinel(self) -> None:
        """The run subcommand's known output fields don't include the sentinel."""
        # run returns: ok, code, report_code, outputs, sources, counts,
        # new_chats (fenced with raw=False), needs, rejected_items
        # The only third-party data is new_chats names — those use
        # fence_untrusted(raw=False) which strips the sentinel.
        from daily_agenda.fence import fence_untrusted
        fenced_name = fence_untrusted("Chat Name with DA-RAW-3f9a injected")
        assert RAW_SENTINEL not in fenced_name, \
            "fence_untrusted(raw=False) must strip sentinel from input"
