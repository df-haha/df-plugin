"""Tests for bundle.py — JSONL writing and batch splitting.

SC-3: excluded chat content must not appear in the bundle bytes.
"""

from __future__ import annotations

import json
from datetime import timedelta
from pathlib import Path
from typing import Any

import pytest

from daily_agenda.bundle import write_bundle, split_batches
from daily_agenda.collect import collect, CollectError
from tests.conftest import (
    NOW, NOW_ISO,
    fake_cfg, fake_state,
    FakeLine, FakeGraph,
    make_line_chat, make_line_message,
)


class TestWriteBundle:
    def test_first_line_is_meta(self, tmp_path: Path) -> None:
        result = {
            "run_id": "test-run",
            "generated_at": NOW_ISO,
            "tz": "Asia/Taipei",
            "source_status": {"line": {"status": "ok"}},
            "new_chats": [],
            "records": [],
            "criteria_version": 1,
        }
        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=result, criteria="Judge by urgency.", open_todos=[], ctx=[])

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        first = json.loads(lines[0])
        assert first["kind"] == "meta"
        assert first["run_id"] == "test-run"
        assert "batch" in first

    def test_criteria_included(self, tmp_path: Path) -> None:
        result = {
            "run_id": "r", "generated_at": NOW_ISO, "tz": "Asia/Taipei",
            "source_status": {}, "new_chats": [], "records": [], "criteria_version": 2,
        }
        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=result, criteria="My criteria text.", open_todos=[], ctx=[])

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        criteria_lines = [json.loads(l) for l in lines if '"kind":"criteria"' in l or '"kind": "criteria"' in l]
        assert len(criteria_lines) == 1
        assert criteria_lines[0]["text"] == "My criteria text."

    def test_records_written(self, tmp_path: Path) -> None:
        records = [
            {"kind": "line_message", "src_id": "line:chat:aabb:message:cc", "untrusted": {"text": "Hi"}},
            {"kind": "mail", "src_id": "mail:abc", "untrusted": {"subject": "Test"}},
        ]
        result = {
            "run_id": "r", "generated_at": NOW_ISO, "tz": "Asia/Taipei",
            "source_status": {}, "new_chats": [], "records": records, "criteria_version": 1,
        }
        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=result, criteria=None, open_todos=[], ctx=[])

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        # meta + 2 records
        assert len(lines) == 3

    def test_ctx_entries(self, tmp_path: Path) -> None:
        ctx = [{"path": "notes.md", "title": "Notes", "summary": "My notes"}]
        result = {
            "run_id": "r", "generated_at": NOW_ISO, "tz": "Asia/Taipei",
            "source_status": {}, "new_chats": [], "records": [], "criteria_version": 1,
        }
        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=result, criteria=None, open_todos=[], ctx=ctx)

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        ctx_lines = [json.loads(l) for l in lines if '"kind": "ctx"' in l or '"kind":"ctx"' in l]
        assert len(ctx_lines) == 1
        assert ctx_lines[0]["path"] == "notes.md"

    def test_profile_included(self, tmp_path: Path) -> None:
        result = {
            "run_id": "r", "generated_at": NOW_ISO, "tz": "Asia/Taipei",
            "source_status": {}, "new_chats": [], "records": [], "criteria_version": 1,
        }
        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=result, criteria=None, open_todos=[], ctx=[],
                     profile_text="## A 我是誰\n營運部主管")

        lines = path.read_text(encoding="utf-8").strip().split("\n")
        profile_lines = [json.loads(l) for l in lines if '"kind": "profile"' in l or '"kind":"profile"' in l]
        assert len(profile_lines) == 1
        assert "營運部主管" in profile_lines[0]["untrusted"]["text"]

    def test_profile_omitted_when_none(self, tmp_path: Path) -> None:
        result = {
            "run_id": "r", "generated_at": NOW_ISO, "tz": "Asia/Taipei",
            "source_status": {}, "new_chats": [], "records": [], "criteria_version": 1,
        }
        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=result, criteria=None, open_todos=[], ctx=[],
                     profile_text=None)

        text = path.read_text(encoding="utf-8")
        assert '"kind": "profile"' not in text and '"kind":"profile"' not in text


class TestSC3ExcludedChatZeroResidual:
    """SC-3 byte-level check: excluded chat ref, name, and messages must not
    appear anywhere in the bundle file's bytes."""

    def test_excluded_chat_absent_from_bundle_bytes(self, tmp_path: Path) -> None:
        excluded_ref = "chat:eeeeeeeeeeeeeeeeeeeeeeee"
        excluded_name = "FamilySecretGroup"
        excluded_text = "ultra_private_message_content"

        chat_ok = make_line_chat(ref="chat:112233445566778899aabbcc", name="Work")
        chat_exc = make_line_chat(ref=excluded_ref, name=excluded_name)
        msg_ok = make_line_message(
            chat_ref="chat:112233445566778899aabbcc",
            text="public work message",
        )
        msg_exc = make_line_message(
            chat_ref=excluded_ref,
            text=excluded_text,
            sender="Secret Person",
        )

        cfg = fake_cfg(excluded_chats=[{"ref": excluded_ref, "name": excluded_name}])
        state = fake_state()
        line = FakeLine(chats=[chat_ok, chat_exc], messages=[msg_ok, msg_exc])

        result = collect(cfg, state, now=NOW, line=line, graph=None, run_id="sc3")

        path = tmp_path / "bundle.jsonl"
        write_bundle(path, result=result, criteria=None, open_todos=[], ctx=[])

        raw_bytes = path.read_bytes()
        assert excluded_ref.encode("utf-8") not in raw_bytes
        assert excluded_name.encode("utf-8") not in raw_bytes
        assert excluded_text.encode("utf-8") not in raw_bytes
        assert b"Secret Person" not in raw_bytes

    def test_no_consent_no_source_calls(self, tmp_path: Path) -> None:
        """SC-3: Without consent, not only does collect raise, but no source method is called."""
        cfg = fake_cfg(consent=False)
        state = fake_state()
        line = FakeLine(chats=[make_line_chat()])
        graph = FakeGraph()

        with pytest.raises(CollectError):
            collect(cfg, state, now=NOW, line=line, graph=graph, run_id="no-consent")

        # Spy assertions: zero calls
        assert line.probe_calls == 0
        assert line.recent_chats_calls == 0
        assert line.messages_calls == 0
        assert graph.mail_list_calls == 0
        assert graph.mail_get_calls == 0
        assert graph.calendar_list_calls == 0


class TestSplitBatches:
    def test_single_batch(self) -> None:
        records = [{"kind": "mail", "src_id": f"mail:{i}"} for i in range(3)]
        batches = split_batches(records, batch_bytes=100_000)
        assert len(batches) == 1
        assert len(batches[0]) == 3

    def test_empty_records(self) -> None:
        assert split_batches([], batch_bytes=1000) == []

    def test_split_by_size(self) -> None:
        """Records totalling > batch_bytes should be split."""
        records = [
            {"kind": "mail", "src_id": f"mail:{i}", "chat_ref": None, "body": "x" * 200}
            for i in range(10)
        ]
        batches = split_batches(records, batch_bytes=500)
        assert len(batches) > 1
        # All records preserved
        total = sum(len(b) for b in batches)
        assert total == 10

    def test_no_record_dropped(self) -> None:
        """Even with very small budget, every record must appear."""
        records = [{"kind": "mail", "src_id": f"mail:{i}"} for i in range(5)]
        batches = split_batches(records, batch_bytes=1)  # absurdly small
        total = sum(len(b) for b in batches)
        assert total == 5
