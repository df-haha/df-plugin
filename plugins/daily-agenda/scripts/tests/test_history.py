"""Tests for daily_agenda.history — SQLite chat digest history (C4)."""

from __future__ import annotations

import os
import stat
from pathlib import Path
from typing import Any

import pytest

from daily_agenda.history import (
    delete_by_chat_ref,
    insert_digests,
    read_last,
    remove_database,
)


# ---------------------------------------------------------------------------
# Fixtures
# ---------------------------------------------------------------------------

CHAT_A = "chat:aabbccdd11223344aabbcc01"
CHAT_B = "chat:aabbccdd11223344aabbcc02"
RUN_1 = "20261008-0900-ab12"
RUN_2 = "20261009-0900-cd34"
RUN_3 = "20261010-0900-ef56"
NOW_ISO = "2026-10-08T09:00:00+08:00"


def _digest(
    chat_ref: str = CHAT_A,
    bullets: list[str] | None = None,
    needs_reply: list[dict[str, str]] | None = None,
    quiet: bool = False,
    chat_name: str = "Test Group",
) -> dict[str, Any]:
    return {
        "chat_ref": chat_ref,
        "bullets": bullets or ["bullet 1", "bullet 2"],
        "needs_reply": needs_reply or [],
        "quiet": quiet,
        "chat_name": chat_name,
    }


def _records_for_chat(
    chat_ref: str = CHAT_A,
    timestamps: list[str] | None = None,
) -> list[dict[str, Any]]:
    ts_list = timestamps or [
        "2026-10-08T08:00:00+08:00",
        "2026-10-08T09:00:00+08:00",
    ]
    return [
        {
            "kind": "line_message",
            "chat_ref": chat_ref,
            "ts": ts,
            "src_id": f"line:{chat_ref}:message:msg{i}",
        }
        for i, ts in enumerate(ts_list)
    ]


# ---------------------------------------------------------------------------
# Insert & read
# ---------------------------------------------------------------------------

class TestInsertAndRead:
    def test_insert_and_read_back(self, tmp_path: Path) -> None:
        digests = [_digest()]
        records = _records_for_chat()
        count = insert_digests(tmp_path, RUN_1, NOW_ISO, digests, records=records)
        assert count == 1

        rows = read_last(tmp_path, CHAT_A)
        assert len(rows) == 1
        assert rows[0]["bullets"] == ["bullet 1", "bullet 2"]
        assert rows[0]["run_id"] == RUN_1
        assert rows[0]["period_from"] == "2026-10-08T08:00:00+08:00"
        assert rows[0]["period_to"] == "2026-10-08T09:00:00+08:00"

    def test_empty_digests(self, tmp_path: Path) -> None:
        count = insert_digests(tmp_path, RUN_1, NOW_ISO, [])
        assert count == 0

    def test_read_nonexistent_db(self, tmp_path: Path) -> None:
        rows = read_last(tmp_path, CHAT_A)
        assert rows == []

    def test_read_different_chat(self, tmp_path: Path) -> None:
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest(chat_ref=CHAT_A)])
        rows = read_last(tmp_path, CHAT_B)
        assert rows == []

    def test_multiple_runs_ordering(self, tmp_path: Path) -> None:
        insert_digests(tmp_path, RUN_1, "2026-10-08T09:00:00+08:00", [_digest()])
        insert_digests(tmp_path, RUN_2, "2026-10-09T09:00:00+08:00", [_digest()])
        insert_digests(tmp_path, RUN_3, "2026-10-10T09:00:00+08:00", [_digest()])

        rows = read_last(tmp_path, CHAT_A, n=2)
        assert len(rows) == 2
        # Newest first
        assert rows[0]["run_id"] == RUN_3
        assert rows[1]["run_id"] == RUN_2

    def test_limit_n(self, tmp_path: Path) -> None:
        for i, run in enumerate([RUN_1, RUN_2, RUN_3]):
            insert_digests(tmp_path, run, f"2026-10-0{8 + i}T09:00:00+08:00", [_digest()])
        rows = read_last(tmp_path, CHAT_A, n=3)
        assert len(rows) == 3

    def test_exclude_run_id(self, tmp_path: Path) -> None:
        insert_digests(tmp_path, RUN_1, "2026-10-08T09:00:00+08:00", [_digest()])
        insert_digests(tmp_path, RUN_2, "2026-10-09T09:00:00+08:00", [_digest()])

        rows = read_last(tmp_path, CHAT_A, exclude_run_id=RUN_2)
        assert len(rows) == 1
        assert rows[0]["run_id"] == RUN_1

    def test_no_records_no_period(self, tmp_path: Path) -> None:
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest()])
        rows = read_last(tmp_path, CHAT_A)
        assert rows[0]["period_from"] is None
        assert rows[0]["period_to"] is None

    def test_quiet_flag(self, tmp_path: Path) -> None:
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest(quiet=True)])
        rows = read_last(tmp_path, CHAT_A)
        assert rows[0]["quiet"] is True

    def test_upsert_same_chat_ref_run_id(self, tmp_path: Path) -> None:
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest(bullets=["old"])])
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest(bullets=["new"])])
        rows = read_last(tmp_path, CHAT_A)
        assert len(rows) == 1
        assert rows[0]["bullets"] == ["new"]


# ---------------------------------------------------------------------------
# Delete
# ---------------------------------------------------------------------------

class TestDelete:
    def test_delete_by_chat_ref(self, tmp_path: Path) -> None:
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest(chat_ref=CHAT_A)])
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest(chat_ref=CHAT_B)])

        deleted = delete_by_chat_ref(tmp_path, CHAT_A)
        assert deleted == 1

        assert read_last(tmp_path, CHAT_A) == []
        assert len(read_last(tmp_path, CHAT_B)) == 1

    def test_delete_nonexistent(self, tmp_path: Path) -> None:
        deleted = delete_by_chat_ref(tmp_path, CHAT_A)
        assert deleted == 0

    def test_delete_multiple_runs(self, tmp_path: Path) -> None:
        insert_digests(tmp_path, RUN_1, "2026-10-08T09:00:00+08:00", [_digest()])
        insert_digests(tmp_path, RUN_2, "2026-10-09T09:00:00+08:00", [_digest()])
        deleted = delete_by_chat_ref(tmp_path, CHAT_A)
        assert deleted == 2


# ---------------------------------------------------------------------------
# Remove database
# ---------------------------------------------------------------------------

class TestRemoveDatabase:
    def test_remove(self, tmp_path: Path) -> None:
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest()])
        assert (tmp_path / "history.sqlite").exists()
        removed = remove_database(tmp_path)
        assert removed is True
        assert not (tmp_path / "history.sqlite").exists()

    def test_remove_nonexistent(self, tmp_path: Path) -> None:
        """LOW-2: removing a non-existent database should return False."""
        removed = remove_database(tmp_path)
        assert removed is False

    def test_remove_existing_returns_true(self, tmp_path: Path) -> None:
        """LOW-2: removing an existing database should return True."""
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest()])
        assert (tmp_path / "history.sqlite").exists()
        removed = remove_database(tmp_path)
        assert removed is True


# ---------------------------------------------------------------------------
# File permissions (non-Windows)
# ---------------------------------------------------------------------------

class TestPermissions:
    @pytest.mark.skipif(os.name == "nt", reason="chmod not meaningful on Windows")
    def test_chmod_0600(self, tmp_path: Path) -> None:
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest()])
        db_path = tmp_path / "history.sqlite"
        mode = db_path.stat().st_mode
        # Owner read+write, no group/other
        assert mode & stat.S_IRWXG == 0
        assert mode & stat.S_IRWXO == 0
        assert mode & stat.S_IRUSR != 0
        assert mode & stat.S_IWUSR != 0


# ---------------------------------------------------------------------------
# Parameterised SQL safety
# ---------------------------------------------------------------------------

class TestUninstallHook:
    def test_uninstall_removes_history(self, tmp_path: Path) -> None:
        from daily_agenda.uninstall import uninstall
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest()])
        assert (tmp_path / "history.sqlite").exists()

        # Mock schedule.remove
        from unittest.mock import patch
        with patch("daily_agenda.schedule.remove", return_value={"ok": True}):
            result = uninstall(tmp_path, purge=False)

        assert result["history_removed"] is True
        assert not (tmp_path / "history.sqlite").exists()


class TestExcludeHook:
    def test_exclude_add_deletes_history(self, tmp_path: Path) -> None:
        """exclude --add should delete history rows for the excluded chat."""
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest(chat_ref=CHAT_A)])
        assert len(read_last(tmp_path, CHAT_A)) == 1

        deleted = delete_by_chat_ref(tmp_path, CHAT_A)
        assert deleted == 1
        assert read_last(tmp_path, CHAT_A) == []

    def test_exclude_set_deletes_history_for_all_refs(self, tmp_path: Path) -> None:
        """exclude --set should delete history rows for every ref in the set."""
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest(chat_ref=CHAT_A)])
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest(chat_ref=CHAT_B)])
        assert len(read_last(tmp_path, CHAT_A)) == 1
        assert len(read_last(tmp_path, CHAT_B)) == 1

        # Simulate what exclude --set does: delete for each ref
        for ref in [CHAT_A, CHAT_B]:
            delete_by_chat_ref(tmp_path, ref)

        assert read_last(tmp_path, CHAT_A) == []
        assert read_last(tmp_path, CHAT_B) == []


class TestSqlInjection:
    def test_chat_ref_with_sql_injection(self, tmp_path: Path) -> None:
        evil_ref = "chat:'; DROP TABLE chat_digest; --"
        insert_digests(tmp_path, RUN_1, NOW_ISO, [_digest(chat_ref=evil_ref)])
        # Table should still work
        rows = read_last(tmp_path, evil_ref)
        assert len(rows) == 1
        # And normal chats unaffected
        insert_digests(tmp_path, RUN_2, NOW_ISO, [_digest(chat_ref=CHAT_A)])
        assert len(read_last(tmp_path, CHAT_A)) == 1
