"""Shared fixtures. The network guard is autouse — no test in this suite may make real HTTP requests."""

from __future__ import annotations

import json
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any
from unittest.mock import patch
from zoneinfo import ZoneInfo

import pytest

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 8, 9, 0, 0, tzinfo=TAIPEI)
NOW_ISO = NOW.isoformat()


@pytest.fixture(autouse=True)
def _block_network():
    """Prevent any test from accidentally reaching the internet via urllib."""

    def _blocked(*args, **kwargs):
        raise RuntimeError("Network access is blocked in tests — inject a fake fetch instead")

    with patch("urllib.request.urlopen", side_effect=_blocked):
        yield


# ---------------------------------------------------------------------------
# Config / state helpers
# ---------------------------------------------------------------------------

def fake_cfg(
    *,
    consent: bool = True,
    line_enabled: bool = True,
    mail_enabled: bool = True,
    excluded_chats: list[dict[str, str]] | None = None,
    tz: str = "Asia/Taipei",
) -> dict[str, Any]:
    """Build a minimal config dict."""
    return {
        "schema_version": 1,
        "tz": tz,
        "consent": {"accepted_at": NOW_ISO, "text_version": 1} if consent else None,
        "line": {
            "enabled": line_enabled,
            "main_database": "test123.edb",
            "excluded_chats": excluded_chats or [],
        },
        "mail": {"enabled": mail_enabled, "folders": ["inbox"]},
        "output": {"dir": "/tmp/daily-agenda-test", "formats": ["html"]},
        "refine": {"every_runs": 10, "every_days": 14},
        "ctx": {"dir": None},
        "schedule": {"enabled": False, "time": None},
        "analysis": {"batch_bytes": 300000, "model": None, "timeout_s": 600},
    }


def fake_state(
    *,
    line_checkpoint: str | None = None,
    mail_checkpoint: str | None = None,
    known_chats: list[dict[str, Any]] | None = None,
    mail_index: dict[str, str] | None = None,
) -> dict[str, Any]:
    return {
        "schema_version": 1,
        "generation": 0,
        "checkpoints": {"line": line_checkpoint, "mail": mail_checkpoint},
        "known_chats": known_chats or [],
        "mail_index": mail_index or {},
        "runs_since_refine": 0,
        "last_refine_at": None,
        "last_refine_applied_at": None,
        "criteria_version": 1,
        "report_code": None,
    }


# ---------------------------------------------------------------------------
# Fake sources
# ---------------------------------------------------------------------------

class FakeLine:
    """Fake LINE source returning canned data."""

    def __init__(
        self,
        *,
        chats: list[dict[str, Any]] | None = None,
        messages: list[dict[str, Any]] | None = None,
        probe_ok: bool = True,
        probe_code: str | None = None,
        fail_on_messages: str | None = None,
        fail_on_recent_chats: str | None = None,
    ) -> None:
        self._chats = chats or []
        self._messages = messages or []
        self._probe_ok = probe_ok
        self._probe_code = probe_code
        self._fail_on_messages = fail_on_messages
        self._fail_on_recent_chats = fail_on_recent_chats
        self.probe_calls = 0
        self.recent_chats_calls = 0
        self.messages_calls = 0

    def probe(self) -> dict[str, Any]:
        self.probe_calls += 1
        if self._probe_ok:
            return {"ok": True, "code": None, "ms": 42}
        return {"ok": False, "code": self._probe_code or "LOCAL_READER_FAILED", "ms": -1}

    def recent_chats(self, *, days: int, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        self.recent_chats_calls += 1
        if self._fail_on_recent_chats:
            from daily_agenda.line_source import LineSourceError
            raise LineSourceError(self._fail_on_recent_chats)
        page = self._chats[offset:offset + limit]
        return {
            "ok": True,
            "chats": page,
            "hasMore": offset + limit < len(self._chats),
            "checkedAt": NOW_ISO,
            "ownSenderRef": "sender:aabbccdd11223344aabbccdd",
            "dateFrom": "2026-09-08",
            "dateTo": "2026-10-08",
        }

    def messages(self, *, chat_name: str, chat_ref: str, chat_type: str = "auto",
                 date_from: str, date_to: str, cursor: str | None = None,
                 message_limit: int = 200) -> dict[str, Any]:
        self.messages_calls += 1
        if self._fail_on_messages:
            from daily_agenda.line_source import LineSourceError
            raise LineSourceError(self._fail_on_messages)
        msgs = [m for m in self._messages if m.get("_chat_ref") == chat_ref]
        return {
            "ok": True,
            "chatRef": chat_ref,
            "chatName": chat_name,
            "chatType": chat_type,
            "ownSenderRef": "sender:aabbccdd11223344aabbccdd",
            "count": len(msgs),
            "messages": msgs,
            "hasMore": False,
            "nextCursor": None,
        }


class FakeGraph:
    """Fake Graph source returning canned data."""

    def __init__(
        self,
        *,
        mail_items: list[dict[str, Any]] | None = None,
        mail_full: dict[str, dict[str, Any]] | None = None,
        calendar_items: list[dict[str, Any]] | None = None,
        fail_on_mail: str | None = None,
        fail_on_calendar: str | None = None,
    ) -> None:
        self._mail_items = mail_items or []
        self._mail_full = mail_full or {}
        self._calendar_items = calendar_items or []
        self._fail_on_mail = fail_on_mail
        self._fail_on_calendar = fail_on_calendar
        self.mail_list_calls = 0
        self.mail_get_calls = 0
        self.calendar_list_calls = 0

    def mail_list(self, *, days: int, folder: str = "inbox") -> list[dict[str, Any]]:
        self.mail_list_calls += 1
        if self._fail_on_mail:
            from daily_agenda.graph_client import GraphClientError
            raise GraphClientError(self._fail_on_mail)
        return self._mail_items

    def mail_get(self, message_id: str) -> dict[str, Any]:
        self.mail_get_calls += 1
        if message_id in self._mail_full:
            return self._mail_full[message_id]
        return {
            "id": message_id,
            "subject": f"Subject for {message_id}",
            "from": "sender@example.com",
            "to": "me@example.com",
            "received_at": NOW_ISO,
            "internet_message_id": f"<{message_id}@example.com>",
            "body": "Test body",
        }

    def calendar_list(self, *, days_ahead: int = 3, days_back: int = 1) -> list[dict[str, Any]]:
        self.calendar_list_calls += 1
        if self._fail_on_calendar:
            from daily_agenda.graph_client import GraphClientError
            raise GraphClientError(self._fail_on_calendar)
        return self._calendar_items


# ---------------------------------------------------------------------------
# Factory helpers
# ---------------------------------------------------------------------------

def make_line_chat(
    ref: str = "chat:112233445566778899aabbcc",
    name: str = "Test Group",
    chat_type: str = "group",
    last_ts: int | None = None,
) -> dict[str, Any]:
    ts = last_ts or int(NOW.timestamp() * 1000)
    return {
        "chatRef": ref,
        "chatName": name,
        "chatType": chat_type,
        "lastMessageAt": NOW_ISO,
        "lastMessageTimestamp": ts,
    }


def make_line_message(
    chat_ref: str = "chat:112233445566778899aabbcc",
    source_ref: str = "message:aabb11223344aabb11223344",
    ts: int | None = None,
    text: str = "Hello from test",
    sender: str = "Taro Yamada",
) -> dict[str, Any]:
    ts = ts or int(NOW.timestamp() * 1000)
    return {
        "_chat_ref": chat_ref,
        "sourceRef": source_ref,
        "sourceMessageId": "123456",
        "sourceTimestamp": ts,
        "date": "2026-10-08",
        "time": "09:00:00",
        "text": text,
        "senderRef": "sender:ddccbbaa44332211ddccbbaa",
        "sender": sender,
        "contentType": None,
    }


def make_mail_item(
    msg_id: str = "AAMk-test-001",
    subject: str = "Test Mail",
    date: str | None = None,
    internet_message_id: str = "",
) -> dict[str, Any]:
    return {
        "id": msg_id,
        "subject": subject,
        "from": "boss@example.com",
        "date": date or NOW_ISO,
        "is_read": False,
        "preview": "Preview text...",
        "internet_message_id": internet_message_id,
    }


def make_calendar_item(
    event_id: str = "evt-001",
    subject: str = "Team Meeting",
    start: str | None = None,
    end: str | None = None,
) -> dict[str, Any]:
    s = start or NOW.isoformat()
    e = end or (NOW + timedelta(hours=1)).isoformat()
    return {
        "id": event_id,
        "subject": subject,
        "start": s,
        "end": e,
        "location": "Room 101",
        "organizer": "organizer@example.com",
        "all_day": False,
        "web_link": "https://outlook.office.com/calendar/item/evt-001",
        "preview": "Agenda: review progress",
    }
