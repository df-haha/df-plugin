"""Canonical text envelope codec with fail-closed validation."""

from __future__ import annotations

from datetime import datetime, timezone
import json
import re
from typing import Any, Mapping

from .models import Envelope, MessageType, parse_timestamp


ENVELOPE_PREFIX = "HERMES_EXCHANGE/1\n"
MAX_ENVELOPE_BYTES = 3_500
DEFAULT_MAX_TTL_SECONDS = 86_400
DEFAULT_MAX_HOPS = 2

_ID_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:-]{0,126}[A-Za-z0-9])?$")
_SLUG_RE = re.compile(r"^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$")
_REQUIRED_KEYS = frozenset(
    {
        "artifact_refs",
        "constraints",
        "created_at",
        "envelope_id",
        "exchange_id",
        "expires_at",
        "hop_count",
        "kind",
        "message_type",
        "payload",
        "recipient_peer",
        "sender_peer",
        "subject",
        "summary",
        "version",
    }
)
_OPTIONAL_KEYS = frozenset({"auth", "execution_hint", "in_reply_to"})


class EnvelopeValidationError(ValueError):
    """Raised for any malformed or policy-invalid exchange envelope."""


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
        raise EnvelopeValidationError("envelope contains non-JSON data") from exc


def _reject_constant(value: str) -> None:
    raise ValueError(f"non-finite number: {value}")


def _string(value: object, field: str, *, maximum: int, allow_empty: bool = False) -> str:
    if not isinstance(value, str) or (not value and not allow_empty) or len(value) > maximum:
        qualifier = "string" if allow_empty else "non-empty string"
        raise EnvelopeValidationError(f"{field} must be a {qualifier} up to {maximum} characters")
    return value


def _id(value: object, field: str) -> str:
    parsed = _string(value, field, maximum=128)
    if not _ID_RE.fullmatch(parsed):
        raise EnvelopeValidationError(f"{field} has an invalid format")
    return parsed


def _slug(value: object, field: str) -> str:
    parsed = _string(value, field, maximum=64)
    if not _SLUG_RE.fullmatch(parsed):
        raise EnvelopeValidationError(f"{field} must be a lowercase slug")
    return parsed


def _string_tuple(value: object, field: str, *, maximum_items: int) -> tuple[str, ...]:
    if not isinstance(value, list) or len(value) > maximum_items:
        raise EnvelopeValidationError(f"{field} must be a list with at most {maximum_items} items")
    return tuple(_string(item, f"{field}[]", maximum=500) for item in value)


def _validate_structure(
    envelope: Envelope,
    *,
    expected_recipient: str | None,
    now: datetime | None,
    max_ttl_seconds: int,
    max_hops: int,
) -> None:
    _id(envelope.envelope_id, "envelope_id")
    _id(envelope.exchange_id, "exchange_id")
    if isinstance(envelope.version, bool) or envelope.version != 1:
        raise EnvelopeValidationError("version must be 1")
    if not isinstance(envelope.message_type, MessageType):
        raise EnvelopeValidationError("unknown message_type")
    _slug(envelope.sender_peer, "sender_peer")
    _slug(envelope.recipient_peer, "recipient_peer")
    _slug(envelope.kind, "kind")
    if expected_recipient is not None and envelope.recipient_peer != expected_recipient:
        raise EnvelopeValidationError("recipient_peer does not match the local peer")
    _string(envelope.subject, "subject", maximum=200)
    _string(envelope.summary, "summary", maximum=1_000)
    if not isinstance(envelope.payload, Mapping):
        raise EnvelopeValidationError("payload must be an object")
    if not isinstance(envelope.constraints, tuple) or len(envelope.constraints) > 20:
        raise EnvelopeValidationError("constraints must contain at most 20 strings")
    for value in envelope.constraints:
        _string(value, "constraints[]", maximum=500)
    if not isinstance(envelope.artifact_refs, tuple) or len(envelope.artifact_refs) > 20:
        raise EnvelopeValidationError("artifact_refs must contain at most 20 strings")
    for value in envelope.artifact_refs:
        _string(value, "artifact_refs[]", maximum=500)
    if envelope.execution_hint is not None:
        _string(envelope.execution_hint, "execution_hint", maximum=100)
    if envelope.auth is not None and not isinstance(envelope.auth, Mapping):
        raise EnvelopeValidationError("auth must be an object")

    if isinstance(envelope.hop_count, bool) or not isinstance(envelope.hop_count, int):
        raise EnvelopeValidationError("hop_count must be an integer")
    if envelope.hop_count < 0 or envelope.hop_count > max_hops:
        raise EnvelopeValidationError("hop_count exceeds policy")
    if max_ttl_seconds <= 0 or max_hops < 0:
        raise EnvelopeValidationError("validator policy limits are invalid")
    if envelope.created_at.tzinfo is None or envelope.expires_at.tzinfo is None:
        raise EnvelopeValidationError("timestamps must include a timezone")
    created_at = envelope.created_at.astimezone(timezone.utc)
    expires_at = envelope.expires_at.astimezone(timezone.utc)
    ttl_seconds = (expires_at - created_at).total_seconds()
    if ttl_seconds <= 0 or ttl_seconds > max_ttl_seconds:
        raise EnvelopeValidationError("envelope TTL exceeds policy")
    if now is not None:
        if now.tzinfo is None or now.utcoffset() is None:
            raise EnvelopeValidationError("validator now must include a timezone")
        if expires_at <= now.astimezone(timezone.utc):
            raise EnvelopeValidationError("envelope has expired")

    reply_types = {
        MessageType.RESULT,
        MessageType.REJECTION,
        MessageType.CANCELLATION,
    }
    if envelope.message_type in reply_types:
        _id(envelope.in_reply_to, "in_reply_to")
    elif envelope.in_reply_to is not None:
        raise EnvelopeValidationError("request envelopes cannot set in_reply_to")

    # Validate nested JSON values before the encoded-size check.
    _canonical_json(envelope.to_dict())


def encode_envelope(
    envelope: Envelope,
    *,
    max_bytes: int = MAX_ENVELOPE_BYTES,
    max_ttl_seconds: int = DEFAULT_MAX_TTL_SECONDS,
    max_hops: int = DEFAULT_MAX_HOPS,
) -> str:
    _validate_structure(
        envelope,
        expected_recipient=None,
        now=None,
        max_ttl_seconds=max_ttl_seconds,
        max_hops=max_hops,
    )
    encoded = ENVELOPE_PREFIX + _canonical_json(envelope.to_dict())
    if len(encoded.encode("utf-8")) > max_bytes:
        raise EnvelopeValidationError(
            "envelope exceeds the 3,500-byte limit; use stable artifact references"
        )
    return encoded


def decode_envelope(
    raw: str,
    *,
    expected_recipient: str | None,
    now: datetime | None = None,
    max_ttl_seconds: int = DEFAULT_MAX_TTL_SECONDS,
    max_hops: int = DEFAULT_MAX_HOPS,
    max_bytes: int = MAX_ENVELOPE_BYTES,
) -> Envelope:
    if not isinstance(raw, str) or not raw.startswith(ENVELOPE_PREFIX):
        raise EnvelopeValidationError("invalid envelope prefix")
    if len(raw.encode("utf-8")) > max_bytes:
        raise EnvelopeValidationError("envelope exceeds the 3,500-byte limit")
    json_text = raw[len(ENVELOPE_PREFIX) :]
    try:
        data = json.loads(json_text, parse_constant=_reject_constant)
    except (TypeError, ValueError, json.JSONDecodeError) as exc:
        raise EnvelopeValidationError("invalid envelope JSON") from exc
    if not isinstance(data, dict):
        raise EnvelopeValidationError("envelope JSON must be an object")
    keys = frozenset(data)
    if not _REQUIRED_KEYS.issubset(keys) or not keys.issubset(_REQUIRED_KEYS | _OPTIONAL_KEYS):
        raise EnvelopeValidationError("envelope fields do not match version 1")
    if json_text != _canonical_json(data):
        raise EnvelopeValidationError("envelope JSON is not canonical")

    try:
        message_type = MessageType(data["message_type"])
    except (TypeError, ValueError) as exc:
        raise EnvelopeValidationError("unknown message_type") from exc
    if isinstance(data["version"], bool) or not isinstance(data["version"], int):
        raise EnvelopeValidationError("version must be an integer")
    if isinstance(data["hop_count"], bool) or not isinstance(data["hop_count"], int):
        raise EnvelopeValidationError("hop_count must be an integer")
    if not isinstance(data["payload"], dict):
        raise EnvelopeValidationError("payload must be an object")
    constraints = _string_tuple(data["constraints"], "constraints", maximum_items=20)
    artifact_refs = _string_tuple(data["artifact_refs"], "artifact_refs", maximum_items=20)
    if "auth" in data and not isinstance(data["auth"], dict):
        raise EnvelopeValidationError("auth must be an object")
    try:
        created_at = parse_timestamp(data["created_at"])
        expires_at = parse_timestamp(data["expires_at"])
    except ValueError as exc:
        raise EnvelopeValidationError(str(exc)) from exc

    envelope = Envelope(
        envelope_id=data["envelope_id"],
        exchange_id=data["exchange_id"],
        version=data["version"],
        message_type=message_type,
        sender_peer=data["sender_peer"],
        recipient_peer=data["recipient_peer"],
        kind=data["kind"],
        subject=data["subject"],
        created_at=created_at,
        expires_at=expires_at,
        hop_count=data["hop_count"],
        summary=data["summary"],
        payload=data["payload"],
        constraints=constraints,
        artifact_refs=artifact_refs,
        in_reply_to=data.get("in_reply_to"),
        execution_hint=data.get("execution_hint"),
        auth=data.get("auth"),
    )
    _validate_structure(
        envelope,
        expected_recipient=expected_recipient,
        now=now or datetime.now(timezone.utc),
        max_ttl_seconds=max_ttl_seconds,
        max_hops=max_hops,
    )
    return envelope
