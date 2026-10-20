"""Chat digest history stored in local SQLite (C4 contract).

Stores AI-generated digest summaries per chat per run — never stores
original message text.  The database lives at ``<home>/history.sqlite``
and is ``chmod 0600`` on non-Windows systems.

All SQL uses parameterised queries.
"""

from __future__ import annotations

import json
import os
import sqlite3
from pathlib import Path
from typing import Any


# ---------------------------------------------------------------------------
# Schema
# ---------------------------------------------------------------------------

_CREATE_TABLE = """\
CREATE TABLE IF NOT EXISTS chat_digest (
    chat_ref        TEXT    NOT NULL,
    run_id          TEXT    NOT NULL,
    created_at      TEXT    NOT NULL,
    period_from     TEXT,
    period_to       TEXT,
    chat_name       TEXT,
    bullets_json    TEXT    NOT NULL,
    needs_reply_json TEXT   NOT NULL,
    quiet           INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (chat_ref, run_id)
)
"""


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _db_path(home: Path) -> Path:
    return home / "history.sqlite"


def _connect(home: Path) -> sqlite3.Connection:
    """Open (or create) the history database and ensure the table exists.

    On non-Windows, creates the file with mode 0600 *before* sqlite3 opens it
    so that WAL/journal sidecars inherit the restrictive permissions.
    """
    path = _db_path(home)
    # Pre-create with restrictive permissions so sidecars inherit them
    if os.name != "nt":
        try:
            fd = os.open(str(path), os.O_CREAT | os.O_RDWR, 0o600)
            os.close(fd)
            path.chmod(0o600)  # Also fix pre-existing files
        except OSError:
            pass
    conn = sqlite3.connect(str(path))
    conn.execute(_CREATE_TABLE)
    conn.commit()
    return conn


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def insert_digests(
    home: Path,
    run_id: str,
    created_at: str,
    digests: list[dict[str, Any]],
    *,
    records: list[dict[str, Any]] | None = None,
) -> int:
    """Insert digest rows for one run.  Returns count inserted.

    *records* (bundle records) is used to derive ``period_from``/``period_to``
    per chat_ref from message timestamps.
    """
    if not digests:
        return 0

    # Build per-chat timestamp ranges from records
    chat_ts: dict[str, list[str]] = {}
    for rec in (records or []):
        if rec.get("kind") == "line_message":
            cref = rec.get("chat_ref", "")
            ts = rec.get("ts", "")
            if cref and ts:
                chat_ts.setdefault(cref, []).append(ts)

    conn = _connect(home)
    try:
        count = 0
        for d in digests:
            chat_ref = d.get("chat_ref", "")
            if not chat_ref:
                continue
            bullets = d.get("bullets", [])
            needs_reply = d.get("needs_reply", [])
            quiet = 1 if d.get("quiet", False) else 0
            chat_name = d.get("chat_name", "")

            # Derive period from/to
            ts_list = sorted(chat_ts.get(chat_ref, []))
            period_from = ts_list[0] if ts_list else None
            period_to = ts_list[-1] if ts_list else None

            conn.execute(
                "INSERT OR REPLACE INTO chat_digest "
                "(chat_ref, run_id, created_at, period_from, period_to, "
                " chat_name, bullets_json, needs_reply_json, quiet) "
                "VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    chat_ref,
                    run_id,
                    created_at,
                    period_from,
                    period_to,
                    chat_name,
                    json.dumps(bullets, ensure_ascii=False),
                    json.dumps(needs_reply, ensure_ascii=False),
                    quiet,
                ),
            )
            count += 1
        conn.commit()
        return count
    finally:
        conn.close()


def read_last(
    home: Path,
    chat_ref: str,
    *,
    n: int = 3,
    exclude_run_id: str | None = None,
) -> list[dict[str, Any]]:
    """Return the last *n* digest entries for *chat_ref*, newest first.

    Excludes *exclude_run_id* (the current run) if given.
    """
    path = _db_path(home)
    if not path.exists():
        return []

    conn = _connect(home)
    try:
        if exclude_run_id:
            rows = conn.execute(
                "SELECT run_id, created_at, period_from, period_to, "
                "       bullets_json, needs_reply_json, quiet "
                "FROM chat_digest "
                "WHERE chat_ref = ? AND run_id != ? "
                "ORDER BY created_at DESC LIMIT ?",
                (chat_ref, exclude_run_id, n),
            ).fetchall()
        else:
            rows = conn.execute(
                "SELECT run_id, created_at, period_from, period_to, "
                "       bullets_json, needs_reply_json, quiet "
                "FROM chat_digest "
                "WHERE chat_ref = ? "
                "ORDER BY created_at DESC LIMIT ?",
                (chat_ref, n),
            ).fetchall()

        result: list[dict[str, Any]] = []
        for row in rows:
            result.append({
                "run_id": row[0],
                "created_at": row[1],
                "period_from": row[2],
                "period_to": row[3],
                "bullets": json.loads(row[4]) if row[4] else [],
                "needs_reply": json.loads(row[5]) if row[5] else [],
                "quiet": bool(row[6]),
            })
        return result
    finally:
        conn.close()


def delete_by_chat_ref(home: Path, chat_ref: str) -> int:
    """Delete all rows for *chat_ref*.  Returns rows deleted."""
    path = _db_path(home)
    if not path.exists():
        return 0

    conn = _connect(home)
    try:
        cur = conn.execute(
            "DELETE FROM chat_digest WHERE chat_ref = ?", (chat_ref,)
        )
        conn.commit()
        return cur.rowcount
    finally:
        conn.close()


def remove_database(home: Path) -> bool:
    """Remove the database file and WAL/journal sidecars.

    Returns True only if the main database file existed and was removed.
    """
    path = _db_path(home)
    existed = path.exists()
    for suffix in ("", "-journal", "-wal", "-shm"):
        p = path.parent / (path.name + suffix)
        try:
            p.unlink(missing_ok=True)
        except OSError:
            pass
    return existed and not path.exists()
