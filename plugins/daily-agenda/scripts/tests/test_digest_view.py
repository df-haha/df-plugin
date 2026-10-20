"""Tests for digest view model (C3) — build_view new keys: chats, quiet_chats, mail_folders, other_todos."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone
from typing import Any

import pytest

from daily_agenda.render import build_view

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 8, 9, 0, 0, tzinfo=TZ)
CHAT_A = "chat:aabbccdd11223344aabbcc01"
CHAT_B = "chat:aabbccdd11223344aabbcc02"


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bundle_meta(
    line_from: str = "2026-10-07T09:00:00+08:00",
    line_to: str = "2026-10-08T09:00:00+08:00",
) -> dict:
    return {
        "sources": {
            "line": {"status": "ok", "from": line_from, "to": line_to},
            "mail": {"status": "ok", "from": "2026-10-07T09:00:00+08:00",
                     "to": "2026-10-08T09:00:00+08:00"},
            "calendar": {"status": "ok"},
        },
        "batch": {"index": 1, "total": 1},
    }


def _ledger_item(
    tid: str = "T-0001",
    title: str = "Test todo",
    status: str = "open",
    priority: str = "P1",
    src_ids: list[str] | None = None,
) -> dict:
    return {
        "id": tid,
        "title": title,
        "priority": priority,
        "due": None,
        "status": status,
        "src_ids": src_ids or ["mail:AAA"],
        "created_at": (NOW - timedelta(days=1)).isoformat(),
        "updated_at": NOW.isoformat(),
        "maybe_done_since": None,
        "maybe_done_reason": None,
        "history": [],
    }


def _line_record(
    chat_ref: str = CHAT_A,
    src_id: str = "line:chat:aa:message:m1",
    ts: str = "2026-10-08T08:00:00+08:00",
    chat_name: str = "Test Group",
    chat_type: str = "group",
    sender: str = "Taro",
    is_self: bool = False,
) -> dict:
    return {
        "kind": "line_message",
        "src_id": src_id,
        "chat_ref": chat_ref,
        "chat_type": chat_type,
        "ts": ts,
        "is_self": is_self,
        "untrusted": {"chat_name": chat_name, "sender": sender, "text": "hello"},
    }


def _mail_record(
    src_id: str = "mail:AAA",
    folder: str = "inbox",
    sender: str = "boss@example.com",
    subject: str = "Test Mail",
    received_at: str = "2026-10-08T08:00:00+08:00",
) -> dict:
    return {
        "kind": "mail",
        "src_id": src_id,
        "folder": folder,
        "received_at": received_at,
        "untrusted": {"from": sender, "to": "me@test.com", "subject": subject, "body": "..."},
    }


def _chat_digest(
    chat_ref: str = CHAT_A,
    bullets: list[str] | None = None,
    needs_reply: list[dict] | None = None,
    quiet: bool = False,
) -> dict:
    return {
        "chat_ref": chat_ref,
        "bullets": bullets or ["point 1", "point 2"],
        "needs_reply": needs_reply or [],
        "quiet": quiet,
    }


def _mail_digest(
    src_id: str = "mail:AAA",
    category: str = "reply",
    summary: str = "Needs reply",
) -> dict:
    return {
        "src_id": src_id,
        "category": category,
        "summary": summary,
    }


def _make_view(**overrides: Any) -> dict:
    defaults: dict[str, Any] = dict(
        state={"report_code": "R-7K2", "report_index": {}},
        ledger=[],
        bundle_meta=_bundle_meta(),
        events=[],
        new_chat_names=[],
        notices=[],
        now=NOW,
        report_code="R-7K2",
        records=[],
        chat_digests=[],
        mail_digests=[],
        folder_labels={},
        chat_history={},
    )
    defaults.update(overrides)
    return build_view(**defaults)


# ---------------------------------------------------------------------------
# Tests: new keys exist
# ---------------------------------------------------------------------------

class TestDigestViewBasicStructure:
    def test_new_keys_present(self) -> None:
        v = _make_view()
        assert "chats" in v
        assert "quiet_chats" in v
        assert "mail_folders" in v
        assert "other_todos" in v

    def test_old_keys_preserved(self) -> None:
        v = _make_view(ledger=[_ledger_item()])
        for key in ("report_code", "date", "generated_at", "sources",
                     "new_chats", "notices", "today", "maybe_done",
                     "collapsed", "events", "skipped", "coverage", "report_index"):
            assert key in v, f"missing old key: {key}"

    def test_today_still_populated(self) -> None:
        """Old 'today' key is still populated from ledger."""
        v = _make_view(ledger=[_ledger_item()])
        assert len(v["today"]) == 1


# ---------------------------------------------------------------------------
# Tests: chats & quiet_chats
# ---------------------------------------------------------------------------

class TestChatCards:
    def test_active_chat_card(self) -> None:
        records = [_line_record(chat_ref=CHAT_A)]
        digests = [_chat_digest(chat_ref=CHAT_A, needs_reply=[{"src_id": "line:chat:aa:message:m1", "ask": "help"}])]
        v = _make_view(records=records, chat_digests=digests)
        assert len(v["chats"]) == 1
        card = v["chats"][0]
        assert card["name"] == "Test Group"
        assert card["kind_label"] == "群組"
        assert card["msg_count"] == 1
        assert card["state"] == "needs"
        assert len(card["needs_reply"]) == 1
        assert "pulse" in card

    def test_quiet_chat(self) -> None:
        records = [_line_record(chat_ref=CHAT_A)]
        digests = [_chat_digest(chat_ref=CHAT_A, quiet=True, bullets=["just chitchat"])]
        v = _make_view(records=records, chat_digests=digests)
        assert len(v["quiet_chats"]) == 1
        qc = v["quiet_chats"][0]
        assert qc["name"] == "Test Group"
        assert "just chitchat" in qc["summary"]

    def test_direct_chat_kind_label(self) -> None:
        records = [_line_record(chat_ref=CHAT_A, chat_type="direct")]
        digests = [_chat_digest(chat_ref=CHAT_A)]
        v = _make_view(records=records, chat_digests=digests)
        assert v["chats"][0]["kind_label"] == "一對一"

    def test_unknown_chat_type(self) -> None:
        records = [_line_record(chat_ref=CHAT_A, chat_type="unknown")]
        digests = [_chat_digest(chat_ref=CHAT_A)]
        v = _make_view(records=records, chat_digests=digests)
        assert v["chats"][0]["kind_label"] == "對話"

    def test_chat_with_todo_attribution(self) -> None:
        """Todo whose src_ids matches a line_message chat_ref should be in the card."""
        records = [_line_record(chat_ref=CHAT_A, src_id="line:chat:aa:message:m1")]
        digests = [_chat_digest(chat_ref=CHAT_A)]
        ledger = [_ledger_item(tid="T-0001", src_ids=["line:chat:aa:message:m1"])]
        v = _make_view(records=records, chat_digests=digests, ledger=ledger)
        assert len(v["chats"]) == 1
        assert len(v["chats"][0]["todos"]) == 1
        assert v["chats"][0]["todos"][0]["todo_id"] == "T-0001"

    def test_chat_sort_order(self) -> None:
        """Chats with needs_reply sort before those with only todos."""
        records = [
            _line_record(chat_ref=CHAT_A, src_id="line:chat:aa:message:m1"),
            _line_record(chat_ref=CHAT_B, src_id="line:chat:bb:message:m3"),
        ]
        digests = [
            _chat_digest(chat_ref=CHAT_A, needs_reply=[]),
            _chat_digest(chat_ref=CHAT_B, needs_reply=[{"src_id": "line:chat:bb:message:m3", "ask": "q"}]),
        ]
        ledger = [_ledger_item(tid="T-0001", src_ids=["line:chat:aa:message:m1"])]
        v = _make_view(records=records, chat_digests=digests, ledger=ledger)
        # CHAT_B has needs_reply, should come first
        assert v["chats"][0]["name"] == "Test Group"  # Both are "Test Group" by default
        # Check by chat_ref in order — the one with needs_reply first
        refs = [c["_chat_ref"] for c in v["chats"]]
        assert refs[0] == CHAT_B

    def test_quiet_chat_with_todo_becomes_active(self) -> None:
        """A quiet chat that has an attributed todo should be in chats, not quiet_chats."""
        records = [_line_record(chat_ref=CHAT_A, src_id="line:chat:aa:message:m1")]
        digests = [_chat_digest(chat_ref=CHAT_A, quiet=True)]
        ledger = [_ledger_item(tid="T-0001", src_ids=["line:chat:aa:message:m1"])]
        v = _make_view(records=records, chat_digests=digests, ledger=ledger)
        assert len(v["chats"]) == 1
        assert len(v["quiet_chats"]) == 0

    def test_chat_history(self) -> None:
        records = [_line_record(chat_ref=CHAT_A)]
        digests = [_chat_digest(chat_ref=CHAT_A)]
        history = {
            CHAT_A: [
                {"created_at": "2026-10-07T09:00:00+08:00", "bullets": ["past point"]},
            ],
        }
        v = _make_view(records=records, chat_digests=digests, chat_history=history)
        assert len(v["chats"][0]["history"]) == 1
        assert v["chats"][0]["history"][0]["bullets"] == ["past point"]


# ---------------------------------------------------------------------------
# Tests: pulse
# ---------------------------------------------------------------------------

class TestPulse:
    def test_pulse_dots_computed(self) -> None:
        records = [
            _line_record(chat_ref=CHAT_A, src_id="line:m1", ts="2026-10-07T09:00:00+08:00"),
            _line_record(chat_ref=CHAT_A, src_id="line:m2", ts="2026-10-08T09:00:00+08:00"),
        ]
        digests = [_chat_digest(chat_ref=CHAT_A)]
        v = _make_view(records=records, chat_digests=digests)
        pulse = v["chats"][0]["pulse"]
        assert len(pulse["dots"]) == 2
        # First message at from → x ≈ 0
        assert pulse["dots"][0]["x"] == pytest.approx(0.0, abs=1.0)
        # Last message at to → x ≈ 800
        assert pulse["dots"][1]["x"] == pytest.approx(800.0, abs=1.0)
        assert isinstance(pulse["dots"][0]["x"], float)

    def test_pulse_reply_flag(self) -> None:
        records = [_line_record(chat_ref=CHAT_A, src_id="line:m1")]
        digests = [_chat_digest(
            chat_ref=CHAT_A,
            needs_reply=[{"src_id": "line:m1", "ask": "help"}],
        )]
        v = _make_view(records=records, chat_digests=digests)
        assert v["chats"][0]["pulse"]["dots"][0]["reply"] is True


# ---------------------------------------------------------------------------
# Tests: mail_folders
# ---------------------------------------------------------------------------

class TestMailFolders:
    def test_mail_folder_grouping(self) -> None:
        records = [
            _mail_record(src_id="mail:AAA", folder="inbox"),
            _mail_record(src_id="mail:BBB", folder="inbox"),
        ]
        digests = [
            _mail_digest(src_id="mail:AAA", category="reply"),
            _mail_digest(src_id="mail:BBB", category="fyi"),
        ]
        v = _make_view(records=records, mail_digests=digests,
                        folder_labels={"inbox": "收件匣"})
        assert len(v["mail_folders"]) == 1
        assert v["mail_folders"][0]["folder_label"] == "收件匣"
        assert len(v["mail_folders"][0]["mails"]) == 2
        # reply should sort before fyi
        assert v["mail_folders"][0]["mails"][0]["category"] == "reply"

    def test_mail_folder_default_label(self) -> None:
        records = [_mail_record(src_id="mail:AAA", folder="inbox")]
        digests = [_mail_digest(src_id="mail:AAA")]
        v = _make_view(records=records, mail_digests=digests)
        assert v["mail_folders"][0]["folder_label"] == "收件匣"

    def test_mail_unknown_folder(self) -> None:
        records = [_mail_record(src_id="mail:AAA", folder="archive")]
        digests = [_mail_digest(src_id="mail:AAA")]
        v = _make_view(records=records, mail_digests=digests)
        # Falls back to folder ref
        assert v["mail_folders"][0]["folder_label"] == "archive"

    def test_mail_todo_attribution(self) -> None:
        records = [_mail_record(src_id="mail:AAA")]
        digests = [_mail_digest(src_id="mail:AAA")]
        ledger = [_ledger_item(tid="T-0001", src_ids=["mail:AAA"])]
        v = _make_view(records=records, mail_digests=digests, ledger=ledger)
        assert len(v["mail_folders"][0]["mails"][0]["todos"]) == 1

    def test_mail_sort_order(self) -> None:
        records = [
            _mail_record(src_id="mail:AAA", folder="inbox"),
            _mail_record(src_id="mail:BBB", folder="inbox"),
            _mail_record(src_id="mail:CCC", folder="inbox"),
        ]
        digests = [
            _mail_digest(src_id="mail:AAA", category="fyi"),
            _mail_digest(src_id="mail:BBB", category="action"),
            _mail_digest(src_id="mail:CCC", category="reply"),
        ]
        v = _make_view(records=records, mail_digests=digests)
        cats = [m["category"] for m in v["mail_folders"][0]["mails"]]
        assert cats == ["reply", "action", "fyi"]


# ---------------------------------------------------------------------------
# Tests: other_todos
# ---------------------------------------------------------------------------

class TestOtherTodos:
    def test_unattributed_todo_in_other(self) -> None:
        """A todo whose src_ids don't match any chat/mail goes to other_todos."""
        ledger = [_ledger_item(tid="T-0001", src_ids=["event:ev1"])]
        v = _make_view(ledger=ledger)
        assert len(v["other_todos"]) == 1
        assert v["other_todos"][0]["todo_id"] == "T-0001"

    def test_attributed_todo_not_in_other(self) -> None:
        records = [_line_record(chat_ref=CHAT_A, src_id="line:m1")]
        digests = [_chat_digest(chat_ref=CHAT_A)]
        ledger = [_ledger_item(tid="T-0001", src_ids=["line:m1"])]
        v = _make_view(records=records, chat_digests=digests, ledger=ledger)
        assert len(v["other_todos"]) == 0

    def test_maybe_done_excluded_from_other(self) -> None:
        """maybe_done items go to maybe_done list, not other_todos."""
        ledger = [_ledger_item(tid="T-0001", status="maybe_done", src_ids=["event:ev1"])]
        v = _make_view(ledger=ledger)
        assert len(v["other_todos"]) == 0
        assert len(v["maybe_done"]) == 1


# ---------------------------------------------------------------------------
# Tests: chat without digest (model missed it)
# ---------------------------------------------------------------------------

class TestChatWithoutDigest:
    def test_chat_with_messages_but_no_digest_goes_to_quiet(self) -> None:
        """US-2: a chat with messages but no digest must not vanish — goes to quiet_chats."""
        records = [_line_record(chat_ref=CHAT_A)]
        v = _make_view(records=records, chat_digests=[])
        assert len(v["chats"]) == 0
        assert len(v["quiet_chats"]) == 1
        qc = v["quiet_chats"][0]
        assert qc["missing"] is True
        assert qc["msg_count"] == 1
        assert "1 則訊息" in qc["summary"]
        assert qc["name"] == "Test Group"

    def test_missing_chat_multiple_messages(self) -> None:
        records = [
            _line_record(chat_ref=CHAT_A, src_id="line:m1", ts="2026-10-08T08:00:00+08:00"),
            _line_record(chat_ref=CHAT_A, src_id="line:m2", ts="2026-10-08T08:30:00+08:00"),
            _line_record(chat_ref=CHAT_A, src_id="line:m3", ts="2026-10-08T09:00:00+08:00"),
        ]
        v = _make_view(records=records, chat_digests=[])
        assert v["quiet_chats"][0]["msg_count"] == 3
        assert "3 則訊息" in v["quiet_chats"][0]["summary"]

    def test_digested_chat_not_duplicated_in_missing(self) -> None:
        """A chat with a digest should NOT also appear as missing."""
        records = [_line_record(chat_ref=CHAT_A)]
        digests = [_chat_digest(chat_ref=CHAT_A)]
        v = _make_view(records=records, chat_digests=digests)
        assert len(v["chats"]) == 1
        missing_in_quiet = [q for q in v["quiet_chats"] if q.get("missing")]
        assert len(missing_in_quiet) == 0

    def test_mixed_digested_and_missing(self) -> None:
        """One chat has digest, another doesn't."""
        records = [
            _line_record(chat_ref=CHAT_A, src_id="line:m1"),
            _line_record(chat_ref=CHAT_B, src_id="line:m2", chat_name="Other Group"),
        ]
        digests = [_chat_digest(chat_ref=CHAT_A)]
        v = _make_view(records=records, chat_digests=digests)
        assert len(v["chats"]) == 1
        assert len(v["quiet_chats"]) == 1
        assert v["quiet_chats"][0]["missing"] is True
        assert v["quiet_chats"][0]["name"] == "Other Group"

    def test_missing_digest_chat_with_todos_becomes_active(self) -> None:
        """HIGH-1: missing-digest chat with attributed todos → active card, not quiet."""
        records = [_line_record(chat_ref=CHAT_A, src_id="line:m1")]
        ledger = [_ledger_item(tid="T-0001", src_ids=["line:m1"])]
        v = _make_view(records=records, chat_digests=[], ledger=ledger)
        # Should be in chats as active card, not quiet_chats
        assert len(v["chats"]) == 1
        assert v["chats"][0]["state"] == "todo"
        assert v["chats"][0]["missing"] is True
        assert len(v["chats"][0]["todos"]) == 1
        assert len(v["quiet_chats"]) == 0

    def test_attributed_todo_not_lost_when_card_not_emitted(self) -> None:
        """HIGH-1: todo attributed to a chat with digest but quiet+no-todos → falls to other."""
        records = [
            _line_record(chat_ref=CHAT_A, src_id="line:m1"),
            _line_record(chat_ref=CHAT_B, src_id="line:m2", chat_name="Group B"),
        ]
        # CHAT_A gets a non-quiet digest (will have a card)
        # CHAT_B gets a quiet digest (no todos → quiet_chats)
        digests = [
            _chat_digest(chat_ref=CHAT_A),
            _chat_digest(chat_ref=CHAT_B, quiet=True),
        ]
        # Todo attributed to CHAT_B's message
        ledger = [_ledger_item(tid="T-0001", src_ids=["line:m2"])]
        v = _make_view(records=records, chat_digests=digests, ledger=ledger)
        # The quiet chat with todo should become active
        chat_refs = [c.get("_chat_ref") for c in v["chats"]]
        assert CHAT_B in chat_refs
        b_card = [c for c in v["chats"] if c.get("_chat_ref") == CHAT_B][0]
        assert len(b_card["todos"]) == 1
        # Should NOT be in other_todos
        assert len(v["other_todos"]) == 0

    def test_mail_attributed_todo_not_lost_without_mail_digest(self) -> None:
        """HIGH-1: todo attributed to mail with no digest → falls to other_todos."""
        records = [_mail_record(src_id="mail:AAA")]
        # No mail digests
        ledger = [_ledger_item(tid="T-0001", src_ids=["mail:AAA"])]
        v = _make_view(records=records, mail_digests=[], ledger=ledger)
        # Should be in other_todos since no mail row was emitted
        assert len(v["other_todos"]) == 1
        assert v["other_todos"][0]["todo_id"] == "T-0001"
