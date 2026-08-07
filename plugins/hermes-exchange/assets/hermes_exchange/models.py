"""Domain values shared by the portable Hermes Exchange plugin."""

from __future__ import annotations

from dataclasses import dataclass, field
from datetime import datetime, timezone
from enum import Enum
from typing import Any, Mapping


class MessageType(str, Enum):
    REQUEST = "request"
    RESULT = "result"
    REJECTION = "rejection"
    CANCELLATION = "cancellation"


class Direction(str, Enum):
    INBOUND = "inbound"
    OUTBOUND = "outbound"


class ExchangeState(str, Enum):
    DRAFT = "draft"
    AWAITING_SENDER_APPROVAL = "awaiting_sender_approval"
    SENT = "sent"
    RESULT_RECEIVED = "result_received"
    RECEIVED = "received"
    AWAITING_RECIPIENT_DECISION = "awaiting_recipient_decision"
    EXECUTING = "executing"
    AWAITING_RESULT_APPROVAL = "awaiting_result_approval"
    RESULT_SENT = "result_sent"
    CLOSED = "closed"
    REJECTED = "rejected"
    CANCELLED = "cancelled"
    EXPIRED = "expired"
    DELIVERY_FAILED = "delivery_failed"
    SECURITY_REJECTED = "security_rejected"


TERMINAL_STATES = frozenset(
    {
        ExchangeState.CLOSED,
        ExchangeState.REJECTED,
        ExchangeState.CANCELLED,
        ExchangeState.EXPIRED,
        ExchangeState.DELIVERY_FAILED,
        ExchangeState.SECURITY_REJECTED,
    }
)


LEGAL_TRANSITIONS: Mapping[ExchangeState, frozenset[ExchangeState]] = {
    ExchangeState.DRAFT: frozenset(
        {ExchangeState.AWAITING_SENDER_APPROVAL, ExchangeState.CANCELLED}
    ),
    ExchangeState.AWAITING_SENDER_APPROVAL: frozenset(
        {
            ExchangeState.SENT,
            ExchangeState.CANCELLED,
            ExchangeState.EXPIRED,
            ExchangeState.DELIVERY_FAILED,
        }
    ),
    ExchangeState.SENT: frozenset(
        {
            ExchangeState.RESULT_RECEIVED,
            ExchangeState.REJECTED,
            ExchangeState.CANCELLED,
            ExchangeState.EXPIRED,
            ExchangeState.DELIVERY_FAILED,
        }
    ),
    ExchangeState.RESULT_RECEIVED: frozenset({ExchangeState.CLOSED}),
    ExchangeState.RECEIVED: frozenset(
        {
            ExchangeState.AWAITING_RECIPIENT_DECISION,
            ExchangeState.CANCELLED,
            ExchangeState.EXPIRED,
            ExchangeState.SECURITY_REJECTED,
        }
    ),
    ExchangeState.AWAITING_RECIPIENT_DECISION: frozenset(
        {
            ExchangeState.EXECUTING,
            ExchangeState.REJECTED,
            ExchangeState.CANCELLED,
            ExchangeState.EXPIRED,
        }
    ),
    ExchangeState.EXECUTING: frozenset(
        {
            ExchangeState.AWAITING_RESULT_APPROVAL,
            ExchangeState.CANCELLED,
            ExchangeState.EXPIRED,
        }
    ),
    ExchangeState.AWAITING_RESULT_APPROVAL: frozenset(
        {
            ExchangeState.EXECUTING,
            ExchangeState.RESULT_SENT,
            ExchangeState.CANCELLED,
            ExchangeState.EXPIRED,
            ExchangeState.DELIVERY_FAILED,
        }
    ),
    ExchangeState.RESULT_SENT: frozenset({ExchangeState.CLOSED}),
}


def can_transition(current: ExchangeState, target: ExchangeState) -> bool:
    return target in LEGAL_TRANSITIONS.get(current, ())


def format_timestamp(value: datetime) -> str:
    """Return a canonical UTC timestamp with second precision."""
    if value.tzinfo is None or value.utcoffset() is None:
        raise ValueError("timestamps must include a timezone")
    normalized = value.astimezone(timezone.utc).replace(microsecond=0)
    return normalized.isoformat().replace("+00:00", "Z")


def parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value:
        raise ValueError("timestamp must be a non-empty string")
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError as exc:
        raise ValueError("timestamp must be ISO-8601") from exc
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(timezone.utc).replace(microsecond=0)


@dataclass(frozen=True, slots=True)
class Envelope:
    envelope_id: str
    exchange_id: str
    version: int
    message_type: MessageType
    sender_peer: str
    recipient_peer: str
    kind: str
    subject: str
    created_at: datetime
    expires_at: datetime
    hop_count: int
    summary: str
    payload: Mapping[str, Any]
    constraints: tuple[str, ...] = ()
    artifact_refs: tuple[str, ...] = ()
    in_reply_to: str | None = None
    execution_hint: str | None = None
    auth: Mapping[str, Any] | None = None

    def to_dict(self) -> dict[str, Any]:
        data: dict[str, Any] = {
            "artifact_refs": list(self.artifact_refs),
            "constraints": list(self.constraints),
            "created_at": format_timestamp(self.created_at),
            "envelope_id": self.envelope_id,
            "exchange_id": self.exchange_id,
            "expires_at": format_timestamp(self.expires_at),
            "hop_count": self.hop_count,
            "kind": self.kind,
            "message_type": self.message_type.value,
            "payload": dict(self.payload),
            "recipient_peer": self.recipient_peer,
            "sender_peer": self.sender_peer,
            "subject": self.subject,
            "summary": self.summary,
            "version": self.version,
        }
        if self.in_reply_to is not None:
            data["in_reply_to"] = self.in_reply_to
        if self.execution_hint is not None:
            data["execution_hint"] = self.execution_hint
        if self.auth is not None:
            data["auth"] = dict(self.auth)
        return data


@dataclass(frozen=True, slots=True)
class ExchangeRecord:
    envelope: Envelope
    direction: Direction
    state: ExchangeState
    policy: str = "human-gated"
    owner_chat_id: int | None = None
    latest_result: str | None = None
    revision: int = 0

    @property
    def exchange_id(self) -> str:
        return self.envelope.exchange_id


@dataclass(frozen=True, slots=True)
class EnvelopeRecord:
    envelope_id: str
    exchange_id: str
    raw_hash: str
    direction: Direction
    message_type: MessageType
    processing_outcome: str
    conflict_count: int = 0
    last_conflict_hash: str | None = None
    is_new: bool = field(default=False, compare=False)
