"""Tests for collect.py — collection orchestrator."""

from __future__ import annotations

from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from daily_agenda.collect import collect, probe, list_chats, CollectError
from tests.conftest import (
    TAIPEI, NOW, NOW_ISO,
    fake_cfg, fake_state,
    FakeLine, FakeGraph,
    make_line_chat, make_line_message,
    make_mail_item, make_calendar_item,
)


class TestConsentGate:
    """SC-3: Without consent, no source is touched."""

    def test_no_consent_raises(self) -> None:
        cfg = fake_cfg(consent=False)
        state = fake_state()
        line = FakeLine()
        graph = FakeGraph()

        with pytest.raises(CollectError) as exc_info:
            collect(cfg, state, now=NOW, line=line, graph=graph, run_id="test-1")

        assert exc_info.value.code == "CONSENT_REQUIRED"
        assert line.probe_calls == 0
        assert line.recent_chats_calls == 0
        assert line.messages_calls == 0
        assert graph.mail_list_calls == 0
        assert graph.mail_get_calls == 0
        assert graph.calendar_list_calls == 0


class TestLineCollection:
    def test_basic_collection(self) -> None:
        chat = make_line_chat()
        msg = make_line_message()
        cfg = fake_cfg()
        state = fake_state()
        line = FakeLine(chats=[chat], messages=[msg])
        graph = FakeGraph()

        result = collect(cfg, state, now=NOW, line=line, graph=graph, run_id="r1")

        assert result.source_status["line"]["status"] == "ok"
        line_records = [r for r in result.records if r["kind"] == "line_message"]
        assert len(line_records) == 1
        assert line_records[0]["src_id"].startswith("line:chat:")
        assert line_records[0]["untrusted"]["text"] == "Hello from test"

    def test_excluded_chats_filtered(self) -> None:
        """SC-3: Excluded chats must not appear in records."""
        excluded_ref = "chat:eeeeeeeeeeeeeeeeeeeeeeee"
        chat_ok = make_line_chat(ref="chat:112233445566778899aabbcc", name="Work")
        chat_excluded = make_line_chat(ref=excluded_ref, name="Family Group")
        msg_ok = make_line_message(chat_ref="chat:112233445566778899aabbcc", text="work msg")
        msg_excluded = make_line_message(chat_ref=excluded_ref, text="secret family msg")

        cfg = fake_cfg(excluded_chats=[{"ref": excluded_ref, "name": "Family Group"}])
        state = fake_state()
        line = FakeLine(chats=[chat_ok, chat_excluded], messages=[msg_ok, msg_excluded])

        result = collect(cfg, state, now=NOW, line=line, graph=None, run_id="r2")

        # No record should reference the excluded chat
        for rec in result.records:
            assert rec.get("chat_ref") != excluded_ref
            assert rec.get("untrusted", {}).get("chat_name") != "Family Group"

    def test_new_chat_detection(self) -> None:
        chat = make_line_chat(ref="chat:aabbccddee112233aabbccdd", name="New Chat")
        msg = make_line_message(chat_ref="chat:aabbccddee112233aabbccdd")
        cfg = fake_cfg()
        state = fake_state(known_chats=[{"ref": "chat:000000000000000000000000", "name": "Old"}])
        line = FakeLine(chats=[chat], messages=[msg])

        result = collect(cfg, state, now=NOW, line=line, graph=None, run_id="r3")

        assert len(result.new_chats) == 1
        assert result.new_chats[0]["ref"] == "chat:aabbccddee112233aabbccdd"

    def test_first_run_reports_no_new_chats(self) -> None:
        """No baseline on the first run → every chat is 'known', none is 'new'
        (otherwise the skill re-asks the exclusion question right after setup)."""
        chats = [make_line_chat(ref=f"chat:{i:024x}", name=f"G{i}") for i in range(3)]
        msg = make_line_message(chat_ref=chats[0]["chatRef"])
        state = fake_state(known_chats=[])
        assert not state.get("last_run")
        line = FakeLine(chats=chats, messages=[msg])

        result = collect(fake_cfg(), state, now=NOW, line=line, graph=None, run_id="r4")

        assert result.new_chats == []
        assert {c["ref"] for c in result.known_chats} == {c["chatRef"] for c in chats}

    def test_checkpoint_not_advanced_on_failure(self) -> None:
        """SC-9: Failed source must not advance checkpoint."""
        cfg = fake_cfg()
        old_cp = (NOW - timedelta(hours=5)).isoformat()
        state = fake_state(line_checkpoint=old_cp)
        line = FakeLine(
            chats=[make_line_chat()],
            fail_on_messages="SESSION_KEY_CHANGED",
        )

        result = collect(cfg, state, now=NOW, line=line, graph=None, run_id="r4")

        assert result.source_status["line"]["status"] == "failed"
        # Checkpoint should NOT be updated
        assert result.new_checkpoints.get("line") is None or result.new_checkpoints.get("line") == old_cp

    def test_dedup_by_src_id(self) -> None:
        """Same message appearing twice (overlap) should be deduped."""
        chat = make_line_chat()
        msg1 = make_line_message(source_ref="message:aabb11223344aabb11223344")
        msg2 = make_line_message(source_ref="message:aabb11223344aabb11223344")
        cfg = fake_cfg()
        state = fake_state()
        line = FakeLine(chats=[chat], messages=[msg1, msg2])

        result = collect(cfg, state, now=NOW, line=line, graph=None, run_id="r5")

        line_records = [r for r in result.records if r["kind"] == "line_message"]
        assert len(line_records) == 1

    def test_disabled_line_skipped(self) -> None:
        cfg = fake_cfg(line_enabled=False)
        state = fake_state()
        line = FakeLine(chats=[make_line_chat()])

        result = collect(cfg, state, now=NOW, line=line, graph=None, run_id="r6")
        assert result.source_status["line"]["status"] == "skipped"
        assert line.recent_chats_calls == 0


class TestMailCollection:
    def test_basic_mail(self) -> None:
        item = make_mail_item()
        cfg = fake_cfg()
        state = fake_state()
        graph = FakeGraph(mail_items=[item])

        result = collect(cfg, state, now=NOW, line=None, graph=graph, run_id="r10")

        assert result.source_status["mail"]["status"] == "ok"
        mail_records = [r for r in result.records if r["kind"] == "mail"]
        assert len(mail_records) == 1
        assert mail_records[0]["src_id"].startswith("mail:")

    def test_sc11_dedup_by_internet_message_id(self) -> None:
        """SC-11: Same internetMessageId with different id → no duplicate."""
        item1 = make_mail_item(msg_id="old-id-001", internet_message_id="<unique@example.com>")
        item2 = make_mail_item(msg_id="new-id-after-move", internet_message_id="<unique@example.com>")
        cfg = fake_cfg()
        # Simulate: first run already indexed this internetMessageId
        state = fake_state(mail_index={"<unique@example.com>": "mail:old-id-001"})
        graph = FakeGraph(mail_items=[item1, item2])

        result = collect(cfg, state, now=NOW, line=None, graph=graph, run_id="r11")

        mail_records = [r for r in result.records if r["kind"] == "mail"]
        # Should produce at most 1 record for this internetMessageId
        src_ids = [r["src_id"] for r in mail_records]
        # The src_id should be the old canonical one
        assert src_ids.count("mail:old-id-001") <= 1
        assert "mail:new-id-after-move" not in src_ids

    def test_mail_failure_independent(self) -> None:
        """Mail failure should not block calendar."""
        cfg = fake_cfg()
        state = fake_state()
        cal = make_calendar_item()
        graph = FakeGraph(fail_on_mail="DF_GRAPH_AUTH", calendar_items=[cal])

        result = collect(cfg, state, now=NOW, line=None, graph=graph, run_id="r12")

        assert result.source_status["mail"]["status"] == "failed"
        assert result.source_status["calendar"]["status"] == "ok"


class TestCalendarCollection:
    def test_basic_calendar(self) -> None:
        cal = make_calendar_item()
        cfg = fake_cfg()
        state = fake_state()
        graph = FakeGraph(calendar_items=[cal])

        result = collect(cfg, state, now=NOW, line=None, graph=graph, run_id="r20")

        assert result.source_status["calendar"]["status"] == "ok"
        events = [r for r in result.records if r["kind"] == "event"]
        assert len(events) == 1
        assert events[0]["src_id"] == "event:evt-001"
        assert events[0]["untrusted"]["subject"] == "Team Meeting"


class TestProbe:
    def test_probe_both(self) -> None:
        line = FakeLine()
        graph = FakeGraph(mail_items=[make_mail_item()], calendar_items=[make_calendar_item()])
        cfg = fake_cfg()
        result = probe(cfg, line=line, graph=graph)
        assert result["line"]["ok"] is True
        assert result["mail"]["ok"] is True
        assert result["calendar"]["ok"] is True

    def test_probe_line_failure(self) -> None:
        line = FakeLine(probe_ok=False, probe_code="SESSION_KEY_UNAVAILABLE")
        result = probe(fake_cfg(), line=line)
        assert result["line"]["ok"] is False
        assert result["line"]["code"] == "SESSION_KEY_UNAVAILABLE"


class TestListChats:
    def test_list_chats(self) -> None:
        chats = [
            make_line_chat(ref=f"chat:{'aa' * 12}", name="Chat A"),
            make_line_chat(ref=f"chat:{'bb' * 12}", name="Chat B"),
        ]
        line = FakeLine(chats=chats)
        result = list_chats(fake_cfg(), line, days=30)
        assert len(result) == 2
        assert result[0]["name"] == "Chat A"
        assert "ref" in result[0]
