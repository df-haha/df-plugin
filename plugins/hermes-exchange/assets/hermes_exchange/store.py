"""Profile-local durable state for Hermes Exchange."""

from __future__ import annotations

import json
import sqlite3
from contextlib import contextmanager
from datetime import UTC, datetime, timedelta
from pathlib import Path
from typing import Any, Iterator, Mapping, Sequence

from .models import (
    LEGAL_TRANSITIONS,
    ExchangeState,
    TERMINAL_STATES as MODEL_TERMINAL_STATES,
    can_transition,
)


class ExchangeStoreError(RuntimeError):
    """Base class for durable exchange-state failures."""


class StateConflictError(ExchangeStoreError):
    """The requested compare-and-set transition is stale or illegal."""


class ReplayConflictError(ExchangeStoreError):
    """An envelope ID was reused with different content."""


TERMINAL_STATES = {state.value for state in MODEL_TERMINAL_STATES}
EXPIRABLE_STATES = tuple(
    sorted(
        state.value
        for state, targets in LEGAL_TRANSITIONS.items()
        if ExchangeState.EXPIRED in targets
    )
)

_JSON_COLUMNS = {"constraints", "artifact_refs", "metadata"}
_MUTABLE_COLUMNS = {
    "owner_direction",
    "latest_result",
    "result_summary",
    "request_envelope_id",
    "result_envelope_id",
    "metadata",
    "expires_at",
}


def _now() -> str:
    return datetime.now(UTC).isoformat()


def _json(value: Any, fallback: Any) -> str:
    return json.dumps(fallback if value is None else value, ensure_ascii=False, sort_keys=True)


class ExchangeStore:
    """SQLite store with one immediate transaction per state change."""

    def __init__(self, path: str | Path) -> None:
        self.path = Path(path)
        self.path.parent.mkdir(parents=True, exist_ok=True)
        with self._connect() as connection:
            connection.execute("PRAGMA journal_mode=WAL")
            connection.execute("PRAGMA foreign_keys=ON")
            self._create_schema(connection)

    def _connect(self) -> sqlite3.Connection:
        connection = sqlite3.connect(self.path, timeout=10.0)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys=ON")
        return connection

    @contextmanager
    def _write(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    @staticmethod
    def _create_schema(connection: sqlite3.Connection) -> None:
        connection.executescript(
            """
            CREATE TABLE IF NOT EXISTS exchanges (
                exchange_id TEXT PRIMARY KEY,
                direction TEXT NOT NULL,
                peer TEXT NOT NULL,
                kind TEXT NOT NULL,
                policy TEXT NOT NULL,
                state TEXT NOT NULL,
                owner_chat_id TEXT NOT NULL,
                subject TEXT NOT NULL,
                summary TEXT NOT NULL,
                payload TEXT NOT NULL,
                constraints TEXT NOT NULL DEFAULT '[]',
                artifact_refs TEXT NOT NULL DEFAULT '[]',
                execution_hint TEXT,
                owner_direction TEXT,
                latest_result TEXT,
                result_summary TEXT,
                request_envelope_id TEXT,
                result_envelope_id TEXT,
                metadata TEXT NOT NULL DEFAULT '{}',
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                expires_at TEXT NOT NULL
            );

            CREATE INDEX IF NOT EXISTS idx_exchanges_state
                ON exchanges(state, updated_at);

            CREATE TABLE IF NOT EXISTS envelopes (
                envelope_id TEXT PRIMARY KEY,
                exchange_id TEXT NOT NULL REFERENCES exchanges(exchange_id),
                raw_hash TEXT NOT NULL,
                direction TEXT NOT NULL,
                message_type TEXT NOT NULL,
                processing_outcome TEXT NOT NULL,
                conflict_count INTEGER NOT NULL DEFAULT 0,
                last_conflict_hash TEXT,
                created_at TEXT NOT NULL,
                processed_at TEXT NOT NULL
            );

            CREATE TABLE IF NOT EXISTS approvals (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange_id TEXT NOT NULL REFERENCES exchanges(exchange_id),
                gate TEXT NOT NULL,
                actor_user_id TEXT NOT NULL,
                actor_chat_id TEXT NOT NULL,
                decision TEXT NOT NULL,
                note TEXT NOT NULL DEFAULT '',
                created_at TEXT NOT NULL,
                UNIQUE(exchange_id, gate)
            );

            CREATE TABLE IF NOT EXISTS deliveries (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange_id TEXT NOT NULL REFERENCES exchanges(exchange_id),
                envelope_id TEXT NOT NULL,
                peer TEXT NOT NULL,
                message_type TEXT NOT NULL,
                status TEXT NOT NULL,
                attempts INTEGER NOT NULL DEFAULT 0,
                last_error_code TEXT,
                retryable INTEGER NOT NULL DEFAULT 0,
                next_retry_at TEXT,
                created_at TEXT NOT NULL,
                updated_at TEXT NOT NULL,
                UNIQUE(envelope_id, message_type)
            );

            CREATE TABLE IF NOT EXISTS notifications (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                exchange_id TEXT NOT NULL REFERENCES exchanges(exchange_id),
                gate TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                message_id TEXT NOT NULL,
                created_at TEXT NOT NULL,
                UNIQUE(chat_id, message_id)
            );
            """
        )

    def create_exchange(self, exchange: Mapping[str, Any]) -> dict[str, Any]:
        required = {
            "exchange_id",
            "direction",
            "peer",
            "kind",
            "policy",
            "state",
            "owner_chat_id",
            "subject",
            "summary",
            "payload",
            "created_at",
            "updated_at",
            "expires_at",
        }
        missing = sorted(required - set(exchange))
        if missing:
            raise ValueError(f"missing exchange fields: {', '.join(missing)}")
        values = dict(exchange)
        values.setdefault("constraints", [])
        values.setdefault("artifact_refs", [])
        values.setdefault("metadata", {})
        columns = [
            "exchange_id", "direction", "peer", "kind", "policy", "state",
            "owner_chat_id", "subject", "summary", "payload", "constraints",
            "artifact_refs", "execution_hint", "owner_direction", "latest_result",
            "result_summary", "request_envelope_id", "result_envelope_id", "metadata",
            "created_at", "updated_at", "expires_at",
        ]
        encoded = [
            _json(values.get(column), [] if column != "metadata" else {})
            if column in _JSON_COLUMNS
            else values.get(column)
            for column in columns
        ]
        with self._write() as connection:
            try:
                connection.execute(
                    f"INSERT INTO exchanges ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
                    encoded,
                )
            except sqlite3.IntegrityError as exc:
                raise StateConflictError("exchange already exists") from exc
        return self.get_exchange(str(values["exchange_id"]))

    def get_exchange(self, exchange_id: str) -> dict[str, Any]:
        self.expire_due()
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM exchanges WHERE exchange_id = ?", (exchange_id,)
            ).fetchone()
        if row is None:
            raise KeyError(exchange_id)
        return self._exchange_row(row)

    def list_exchanges(
        self,
        *,
        states: Sequence[str] | None = None,
        limit: int = 20,
    ) -> list[dict[str, Any]]:
        self.expire_due()
        bounded = min(max(int(limit), 1), 50)
        query = "SELECT * FROM exchanges"
        parameters: list[Any] = []
        if states:
            placeholders = ",".join("?" for _ in states)
            query += f" WHERE state IN ({placeholders})"
            parameters.extend(states)
        query += " ORDER BY updated_at DESC LIMIT ?"
        parameters.append(bounded)
        with self._connect() as connection:
            rows = connection.execute(query, parameters).fetchall()
        return [self._exchange_row(row) for row in rows]

    def transition(
        self,
        exchange_id: str,
        *,
        expected_state: str,
        new_state: str,
        updates: Mapping[str, Any] | None = None,
    ) -> dict[str, Any]:
        if not self._legal_transition(expected_state, new_state):
            raise StateConflictError(
                f"illegal transition {expected_state!r} -> {new_state!r}"
            )
        assignments = ["state = ?", "updated_at = ?"]
        parameters: list[Any] = [new_state, _now()]
        for key, value in (updates or {}).items():
            if key not in _MUTABLE_COLUMNS:
                raise ValueError(f"unsupported exchange update: {key}")
            assignments.append(f"{key} = ?")
            parameters.append(_json(value, {}) if key == "metadata" else value)
        parameters.extend([exchange_id, expected_state])
        with self._write() as connection:
            cursor = connection.execute(
                f"UPDATE exchanges SET {', '.join(assignments)} WHERE exchange_id = ? AND state = ?",
                parameters,
            )
            if cursor.rowcount != 1:
                raise StateConflictError("exchange state changed or exchange is missing")
        return self.get_exchange(exchange_id)

    @staticmethod
    def _legal_transition(expected_state: str, new_state: str) -> bool:
        try:
            return can_transition(ExchangeState(expected_state), ExchangeState(new_state))
        except ValueError:
            return False

    def ingest_envelope(
        self,
        *,
        envelope_id: str,
        raw_hash: str,
        message_type: str,
        direction: str,
        exchange: Mapping[str, Any],
        processing_outcome: str = "accepted",
    ) -> str:
        conflict = False
        with self._write() as connection:
            existing = connection.execute(
                "SELECT raw_hash FROM envelopes WHERE envelope_id = ?", (envelope_id,)
            ).fetchone()
            if existing is not None:
                if existing["raw_hash"] != raw_hash:
                    connection.execute(
                        """
                        UPDATE envelopes
                        SET conflict_count = conflict_count + 1,
                            last_conflict_hash = ?, processed_at = ?
                        WHERE envelope_id = ?
                        """,
                        (raw_hash, _now(), envelope_id),
                    )
                    conflict = True
                else:
                    return "duplicate"

            if not conflict:
                exchange_id = str(exchange["exchange_id"])
                found_exchange = connection.execute(
                    "SELECT exchange_id FROM exchanges WHERE exchange_id = ?", (exchange_id,)
                ).fetchone()
                if found_exchange is None:
                    self._insert_exchange_in_transaction(connection, exchange)
                connection.execute(
                    """
                    INSERT INTO envelopes (
                        envelope_id, exchange_id, raw_hash, direction, message_type,
                        processing_outcome, created_at, processed_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        envelope_id,
                        exchange_id,
                        raw_hash,
                        direction,
                        message_type,
                        processing_outcome,
                        str(exchange.get("created_at") or _now()),
                        _now(),
                    ),
                )
        if conflict:
            raise ReplayConflictError("envelope ID was replayed with different content")
        return "inserted"

    def envelope_replay_status(self, envelope_id: str, raw_hash: str) -> str:
        """Classify an envelope ID before workflow state/correlation checks."""

        conflict = False
        with self._write() as connection:
            existing = connection.execute(
                "SELECT raw_hash FROM envelopes WHERE envelope_id = ?", (envelope_id,)
            ).fetchone()
        if existing is None:
            return "new"
        if existing["raw_hash"] != raw_hash:
            with self._write() as connection:
                connection.execute(
                    """
                    UPDATE envelopes
                    SET conflict_count = conflict_count + 1,
                        last_conflict_hash = ?, processed_at = ?
                    WHERE envelope_id = ?
                    """,
                    (raw_hash, _now(), envelope_id),
                )
            conflict = True
        if conflict:
            raise ReplayConflictError("envelope ID was replayed with different content")
        return "duplicate"

    def _insert_exchange_in_transaction(
        self, connection: sqlite3.Connection, exchange: Mapping[str, Any]
    ) -> None:
        values = dict(exchange)
        values.setdefault("constraints", [])
        values.setdefault("artifact_refs", [])
        values.setdefault("metadata", {})
        values.setdefault("updated_at", values.get("created_at") or _now())
        columns = [
            "exchange_id", "direction", "peer", "kind", "policy", "state",
            "owner_chat_id", "subject", "summary", "payload", "constraints",
            "artifact_refs", "execution_hint", "owner_direction", "latest_result",
            "result_summary", "request_envelope_id", "result_envelope_id", "metadata",
            "created_at", "updated_at", "expires_at",
        ]
        encoded = [
            _json(values.get(column), [] if column != "metadata" else {})
            if column in _JSON_COLUMNS
            else values.get(column)
            for column in columns
        ]
        connection.execute(
            f"INSERT INTO exchanges ({', '.join(columns)}) VALUES ({', '.join('?' for _ in columns)})",
            encoded,
        )

    def record_approval(
        self,
        *,
        exchange_id: str,
        gate: str,
        actor_user_id: str,
        actor_chat_id: str,
        decision: str,
        note: str,
        expected_state: str,
        new_state: str | None = None,
        updates: Mapping[str, Any] | None = None,
    ) -> bool:
        if new_state and not self._legal_transition(expected_state, new_state):
            raise StateConflictError(
                f"illegal transition {expected_state!r} -> {new_state!r}"
            )
        with self._write() as connection:
            row = connection.execute(
                "SELECT state FROM exchanges WHERE exchange_id = ?", (exchange_id,)
            ).fetchone()
            if row is None or row["state"] != expected_state:
                raise StateConflictError("approval does not match the current exchange state")
            try:
                connection.execute(
                    """
                    INSERT INTO approvals (
                        exchange_id, gate, actor_user_id, actor_chat_id,
                        decision, note, created_at
                    ) VALUES (?, ?, ?, ?, ?, ?, ?)
                    """,
                    (
                        exchange_id,
                        gate,
                        str(actor_user_id),
                        str(actor_chat_id),
                        decision,
                        note,
                        _now(),
                    ),
                )
            except sqlite3.IntegrityError:
                existing = connection.execute(
                    "SELECT * FROM approvals WHERE exchange_id = ? AND gate = ?",
                    (exchange_id, gate),
                ).fetchone()
                if (
                    existing is not None
                    and existing["actor_user_id"] == str(actor_user_id)
                    and existing["actor_chat_id"] == str(actor_chat_id)
                    and existing["decision"] == decision
                    and existing["note"] == note
                ):
                    return False
                raise StateConflictError("approval gate was already decided")

            if new_state or updates:
                assignments = ["updated_at = ?"]
                parameters: list[Any] = [_now()]
                if new_state:
                    assignments.insert(0, "state = ?")
                    parameters.insert(0, new_state)
                for key, value in (updates or {}).items():
                    if key not in _MUTABLE_COLUMNS:
                        raise ValueError(f"unsupported exchange update: {key}")
                    assignments.append(f"{key} = ?")
                    parameters.append(_json(value, {}) if key == "metadata" else value)
                parameters.extend([exchange_id, expected_state])
                changed = connection.execute(
                    f"UPDATE exchanges SET {', '.join(assignments)} WHERE exchange_id = ? AND state = ?",
                    parameters,
                )
                if changed.rowcount != 1:
                    raise StateConflictError("exchange changed during approval")
        return True

    def create_delivery(
        self,
        *,
        exchange_id: str,
        envelope_id: str,
        peer: str,
        message_type: str,
    ) -> dict[str, Any]:
        now = _now()
        with self._write() as connection:
            connection.execute(
                """
                INSERT OR IGNORE INTO deliveries (
                    exchange_id, envelope_id, peer, message_type, status,
                    attempts, retryable, created_at, updated_at
                ) VALUES (?, ?, ?, ?, 'pending', 0, 0, ?, ?)
                """,
                (exchange_id, envelope_id, peer, message_type, now, now),
            )
            row = connection.execute(
                "SELECT * FROM deliveries WHERE envelope_id = ? AND message_type = ?",
                (envelope_id, message_type),
            ).fetchone()
        return dict(row) if row is not None else {}

    def claim_delivery(
        self,
        envelope_id: str,
        message_type: str,
        *,
        max_attempts: int = 3,
        now: datetime | None = None,
        sending_timeout_seconds: int = 60,
    ) -> bool:
        if max_attempts < 1:
            raise ValueError("max_attempts must be positive")
        if sending_timeout_seconds < 1:
            raise ValueError("sending_timeout_seconds must be positive")
        current = (now or datetime.now(UTC)).astimezone(UTC)
        current_text = current.isoformat()
        stale_before = (current - timedelta(seconds=sending_timeout_seconds)).isoformat()
        with self._write() as connection:
            cursor = connection.execute(
                """
                UPDATE deliveries
                SET status = 'sending', attempts = attempts + 1,
                    next_retry_at = NULL, updated_at = ?
                WHERE envelope_id = ? AND message_type = ?
                  AND attempts < ?
                  AND (
                    status = 'pending'
                    OR (
                      status = 'retryable'
                      AND (next_retry_at IS NULL OR next_retry_at <= ?)
                    )
                    OR (status = 'sending' AND updated_at <= ?)
                  )
                """,
                (
                    current_text,
                    envelope_id,
                    message_type,
                    max_attempts,
                    current_text,
                    stale_before,
                ),
            )
        return cursor.rowcount == 1

    def get_delivery(self, envelope_id: str, message_type: str) -> dict[str, Any]:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM deliveries WHERE envelope_id = ? AND message_type = ?",
                (envelope_id, message_type),
            ).fetchone()
        if row is None:
            raise KeyError(envelope_id)
        return dict(row)

    def peer_retry_after(
        self,
        peer: str,
        *,
        minimum_interval_seconds: int,
        now: datetime | None = None,
    ) -> float:
        if minimum_interval_seconds <= 0:
            return 0.0
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT updated_at FROM deliveries
                WHERE peer = ? AND status = 'sent'
                ORDER BY updated_at DESC LIMIT 1
                """,
                (peer,),
            ).fetchone()
        if row is None:
            return 0.0
        try:
            sent_at = datetime.fromisoformat(str(row["updated_at"]).replace("Z", "+00:00"))
        except ValueError:
            return float(minimum_interval_seconds)
        current = now or datetime.now(UTC)
        elapsed = (current.astimezone(UTC) - sent_at.astimezone(UTC)).total_seconds()
        return max(0.0, float(minimum_interval_seconds) - elapsed)

    def inbound_peer_retry_after(
        self,
        peer: str,
        *,
        minimum_interval_seconds: int,
        now: datetime | None = None,
    ) -> float:
        if minimum_interval_seconds <= 0:
            return 0.0
        with self._connect() as connection:
            row = connection.execute(
                """
                SELECT envelopes.processed_at
                FROM envelopes
                JOIN exchanges USING (exchange_id)
                WHERE exchanges.peer = ?
                  AND envelopes.direction = 'inbound'
                  AND envelopes.message_type = 'request'
                  AND envelopes.processing_outcome = 'accepted'
                ORDER BY envelopes.processed_at DESC LIMIT 1
                """,
                (peer,),
            ).fetchone()
        if row is None:
            return 0.0
        try:
            received_at = datetime.fromisoformat(
                str(row["processed_at"]).replace("Z", "+00:00")
            )
        except ValueError:
            return float(minimum_interval_seconds)
        current = now or datetime.now(UTC)
        elapsed = (
            current.astimezone(UTC) - received_at.astimezone(UTC)
        ).total_seconds()
        return max(0.0, float(minimum_interval_seconds) - elapsed)

    def finish_delivery(
        self,
        *,
        envelope_id: str,
        message_type: str,
        success: bool,
        error_code: str | None = None,
        retryable: bool = False,
        next_retry_at: str | None = None,
    ) -> None:
        status = "sent" if success else ("retryable" if retryable else "failed")
        with self._write() as connection:
            cursor = connection.execute(
                """
                UPDATE deliveries
                SET status = ?, last_error_code = ?, retryable = ?,
                    next_retry_at = ?, updated_at = ?
                WHERE envelope_id = ? AND message_type = ?
                """,
                (
                    status,
                    error_code,
                    int(retryable),
                    next_retry_at,
                    _now(),
                    envelope_id,
                    message_type,
                ),
            )
            if cursor.rowcount != 1:
                raise KeyError(envelope_id)

    def record_notification(
        self,
        *,
        exchange_id: str,
        gate: str,
        chat_id: str,
        message_id: str,
    ) -> None:
        with self._write() as connection:
            connection.execute(
                """
                INSERT OR REPLACE INTO notifications (
                    exchange_id, gate, chat_id, message_id, created_at
                ) VALUES (?, ?, ?, ?, ?)
                """,
                (exchange_id, gate, str(chat_id), str(message_id), _now()),
            )

    def resolve_notification(self, chat_id: str, message_id: str) -> dict[str, Any] | None:
        with self._connect() as connection:
            row = connection.execute(
                "SELECT * FROM notifications WHERE chat_id = ? AND message_id = ?",
                (str(chat_id), str(message_id)),
            ).fetchone()
        return dict(row) if row is not None else None

    def audit(self, exchange_id: str) -> dict[str, Any]:
        with self._connect() as connection:
            approvals = connection.execute(
                """
                SELECT gate, actor_user_id, actor_chat_id, decision, note, created_at
                FROM approvals WHERE exchange_id = ? ORDER BY id
                """,
                (exchange_id,),
            ).fetchall()
            deliveries = connection.execute(
                "SELECT * FROM deliveries WHERE exchange_id = ? ORDER BY id",
                (exchange_id,),
            ).fetchall()
            envelopes = connection.execute(
                "SELECT * FROM envelopes WHERE exchange_id = ? ORDER BY processed_at",
                (exchange_id,),
            ).fetchall()
        return {
            "approvals": [dict(row) for row in approvals],
            "deliveries": [dict(row) for row in deliveries],
            "envelopes": [dict(row) for row in envelopes],
        }

    def capture_expired_result(self, exchange_id: str, latest_result: str) -> dict[str, Any]:
        """Persist work that completed after its exchange reached terminal expiry."""

        with self._write() as connection:
            row = connection.execute(
                "SELECT state, latest_result FROM exchanges WHERE exchange_id = ?",
                (exchange_id,),
            ).fetchone()
            if row is None or row["state"] != ExchangeState.EXPIRED.value:
                raise StateConflictError("exchange is not expired")
            existing = row["latest_result"]
            if existing not in (None, "", latest_result):
                raise StateConflictError("expired exchange already has a different result")
            if not existing:
                connection.execute(
                    """
                    UPDATE exchanges SET latest_result = ?, updated_at = ?
                    WHERE exchange_id = ? AND state = 'expired'
                    """,
                    (latest_result, _now(), exchange_id),
                )
        return self.get_exchange(exchange_id)

    def expire_due(self, now: datetime | None = None) -> int:
        timestamp = (now or datetime.now(UTC)).isoformat()
        placeholders = ",".join("?" for _ in EXPIRABLE_STATES)
        with self._write() as connection:
            cursor = connection.execute(
                f"""
                UPDATE exchanges SET state = 'expired', updated_at = ?
                WHERE expires_at <= ? AND state IN ({placeholders})
                """,
                [timestamp, timestamp, *EXPIRABLE_STATES],
            )
        return cursor.rowcount

    @staticmethod
    def _exchange_row(row: sqlite3.Row) -> dict[str, Any]:
        result = dict(row)
        result["constraints"] = json.loads(result.get("constraints") or "[]")
        result["artifact_refs"] = json.loads(result.get("artifact_refs") or "[]")
        result["metadata"] = json.loads(result.get("metadata") or "{}")
        return result
