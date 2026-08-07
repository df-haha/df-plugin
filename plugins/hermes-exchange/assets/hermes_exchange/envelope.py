"""Strict, framework-neutral codec for lightweight Hermes notifications."""

from __future__ import annotations

from dataclasses import asdict, dataclass
from datetime import UTC, datetime, timedelta
import json
import re
import uuid
from typing import Any, Mapping


ENVELOPE_PREFIX = "HERMES_NOTIFY/1\n"
MAX_ENVELOPE_BYTES = 3_500
DEFAULT_TTL_SECONDS = 1_800
MAX_TTL_SECONDS = 86_400

_ID_RE = re.compile(r"^hmsg-[a-f0-9]{16,64}$")
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")
_FIELDS = frozenset(
    {
        "body",
        "expires_at",
        "kind",
        "message_id",
        "recipient_peer",
        "sender_peer",
        "sent_at",
        "subject",
        "version",
    }
)


class EnvelopeValidationError(ValueError):
    """Raised when a notification fails its closed wire contract."""


@dataclass(frozen=True, slots=True)
class Notification:
    message_id: str
    sender_peer: str
    recipient_peer: str
    kind: str
    subject: str
    body: str
    sent_at: datetime
    expires_at: datetime
    version: int = 1

    def to_dict(self) -> dict[str, Any]:
        data = asdict(self)
        data["sent_at"] = _format_timestamp(self.sent_at)
        data["expires_at"] = _format_timestamp(self.expires_at)
        return data


def new_notification(
    *,
    sender_peer: str,
    recipient_peer: str,
    kind: str,
    subject: str,
    body: str,
    now: datetime | None = None,
    ttl_seconds: int = DEFAULT_TTL_SECONDS,
) -> Notification:
    sent_at = (now or datetime.now(UTC)).astimezone(UTC)
    return Notification(
        message_id=f"hmsg-{uuid.uuid4().hex}",
        sender_peer=sender_peer,
        recipient_peer=recipient_peer,
        kind=kind,
        subject=subject,
        body=body,
        sent_at=sent_at,
        expires_at=sent_at + timedelta(seconds=ttl_seconds),
    )


def encode_notification(
    notification: Notification,
    *,
    max_bytes: int = MAX_ENVELOPE_BYTES,
) -> str:
    _validate(notification)
    encoded = ENVELOPE_PREFIX + _canonical_json(notification.to_dict())
    if len(encoded.encode("utf-8")) > max_bytes:
        raise EnvelopeValidationError("notification exceeds the configured byte limit")
    return encoded


def decode_notification(
    raw: str,
    *,
    expected_recipient: str,
    now: datetime | None = None,
    max_bytes: int = MAX_ENVELOPE_BYTES,
) -> Notification:
    if not isinstance(raw, str) or not raw.startswith(ENVELOPE_PREFIX):
        raise EnvelopeValidationError("invalid notification prefix")
    if len(raw.encode("utf-8")) > max_bytes:
        raise EnvelopeValidationError("notification exceeds the configured byte limit")
    json_text = raw[len(ENVELOPE_PREFIX) :]
    try:
        data = json.loads(json_text, parse_constant=_reject_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EnvelopeValidationError("invalid notification JSON") from exc
    if not isinstance(data, Mapping) or frozenset(data) != _FIELDS:
        raise EnvelopeValidationError("notification fields do not match version 1")
    if json_text != _canonical_json(data):
        raise EnvelopeValidationError("notification JSON is not canonical")
    try:
        notification = Notification(
            message_id=data["message_id"],
            sender_peer=data["sender_peer"],
            recipient_peer=data["recipient_peer"],
            kind=data["kind"],
            subject=data["subject"],
            body=data["body"],
            sent_at=_parse_timestamp(data["sent_at"]),
            expires_at=_parse_timestamp(data["expires_at"]),
            version=data["version"],
        )
    except (KeyError, TypeError, ValueError) as exc:
        raise EnvelopeValidationError("notification fields are invalid") from exc
    _validate(notification)
    if notification.recipient_peer != expected_recipient:
        raise EnvelopeValidationError("recipient does not match the local peer")
    check_time = now or datetime.now(UTC)
    if check_time.tzinfo is None or check_time.utcoffset() is None:
        raise EnvelopeValidationError("validation time must include a timezone")
    if notification.expires_at <= check_time.astimezone(UTC):
        raise EnvelopeValidationError("notification has expired")
    return notification


def _validate(notification: Notification) -> None:
    if isinstance(notification.version, bool) or notification.version != 1:
        raise EnvelopeValidationError("version must be 1")
    if not isinstance(notification.message_id, str) or not _ID_RE.fullmatch(
        notification.message_id
    ):
        raise EnvelopeValidationError("message_id has an invalid format")
    for field_name in ("sender_peer", "recipient_peer", "kind"):
        value = getattr(notification, field_name)
        if not isinstance(value, str) or not _SLUG_RE.fullmatch(value):
            raise EnvelopeValidationError(f"{field_name} must be a lowercase slug")
    _bounded_string(notification.subject, "subject", 200)
    _bounded_string(notification.body, "body", 3_000)
    if notification.sent_at.tzinfo is None or notification.sent_at.utcoffset() is None:
        raise EnvelopeValidationError("sent_at must include a timezone")
    if notification.expires_at.tzinfo is None or notification.expires_at.utcoffset() is None:
        raise EnvelopeValidationError("expires_at must include a timezone")
    ttl = (notification.expires_at - notification.sent_at).total_seconds()
    if ttl <= 0 or ttl > MAX_TTL_SECONDS:
        raise EnvelopeValidationError("notification TTL is invalid")


def _bounded_string(value: object, field: str, maximum: int) -> str:
    if not isinstance(value, str) or not value.strip() or len(value) > maximum:
        raise EnvelopeValidationError(
            f"{field} must be a non-empty string up to {maximum} characters"
        )
    return value


def _canonical_json(data: Mapping[str, Any]) -> str:
    try:
        return json.dumps(
            data,
            ensure_ascii=False,
            separators=(",", ":"),
            sort_keys=True,
            allow_nan=False,
        )
    except (TypeError, ValueError) as exc:
        raise EnvelopeValidationError("notification contains non-JSON data") from exc


def _format_timestamp(value: datetime) -> str:
    return value.astimezone(UTC).isoformat().replace("+00:00", "Z")


def _parse_timestamp(value: object) -> datetime:
    if not isinstance(value, str) or not value.endswith("Z"):
        raise ValueError("timestamp must be UTC RFC3339")
    parsed = datetime.fromisoformat(value[:-1] + "+00:00")
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ValueError("timestamp must include a timezone")
    return parsed.astimezone(UTC)


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite number: {value}")
