"""Plugin-owned HERMES_CASE_V1 codec and injectable Telegram transport."""

from __future__ import annotations

import base64
import asyncio
import hashlib
import json
import math
import time
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any, Iterable, Mapping

import httpx


PREFIX = "HERMES_CASE_V1\n"


class PeerDeliveryError(ValueError):
    pass


class PeerTrustInactiveError(PeerDeliveryError):
    pass


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    success: bool
    status: str
    message_id: int | None = None
    retryable: bool = False
    human_required: bool = False
    error_code: str | None = None
    status_code: int | None = None
    retry_after_seconds: float | None = None


class TelegramTransport:
    """Small, plugin-owned Telegram ``sendMessage`` client."""

    def __init__(self, token: str, *, http_client: Any | None = None, timeout: float = 10.0) -> None:
        self._token = token
        self._http_client = http_client
        self._timeout = timeout

    async def send(self, recipient: str, text: str) -> DeliveryResult:
        if not self._token:
            return DeliveryResult(False, "human_required", human_required=True, error_code="missing_bot_token")
        try:
            request = {
                "url": f"https://api.telegram.org/bot{self._token}/sendMessage",
                "json": {"chat_id": recipient, "text": text},
                "timeout": self._timeout,
            }
            if self._http_client is not None:
                response = await self._http_client.post(**request)
            else:
                async with httpx.AsyncClient() as client:
                    response = await client.post(**request)
        except (TimeoutError, httpx.TimeoutException):
            return DeliveryResult(False, "retryable", retryable=True, error_code="timeout")
        except Exception:
            return DeliveryResult(False, "human_required", human_required=True, error_code="transport_error")
        try:
            body = response.json()
        except Exception:
            body = {}
        if not isinstance(body, Mapping):
            body = {}
        if response.status_code == 200 and body.get("ok") is True:
            result = body.get("result") if isinstance(body.get("result"), Mapping) else {}
            return DeliveryResult(True, "delivered", message_id=result.get("message_id"), status_code=200)
        description = str(body.get("description", "")).upper()
        if response.status_code == 429:
            parameters = body.get("parameters") if isinstance(body.get("parameters"), Mapping) else {}
            try:
                retry_after = float(parameters.get("retry_after"))
                retry_after = min(max(0.0, retry_after), 60.0) if math.isfinite(retry_after) else None
            except (TypeError, ValueError):
                retry_after = None
            return DeliveryResult(False, "retryable", retryable=True, error_code="rate_limited", status_code=429, retry_after_seconds=retry_after)
        if 500 <= response.status_code <= 599:
            return DeliveryResult(False, "retryable", retryable=True, error_code="telegram_server_error", status_code=response.status_code)
        code = "bot_to_bot_disabled" if "USER_BOT_TO_BOT_DISABLED" in description else "telegram_client_error"
        return DeliveryResult(
            False,
            "human_required",
            retryable=code == "bot_to_bot_disabled",
            human_required=True,
            error_code=code,
            status_code=response.status_code,
        )


class PeerCodec:
    def __init__(self, max_bytes: int = 3500) -> None:
        self.max_bytes = max_bytes

    def encode(self, delivery: Mapping[str, Any]) -> list[str]:
        raw = json.dumps(delivery, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode()
        upper = raw.upper()
        if any(marker in upper for marker in (b"GITHUB_TOKEN", b"GH_TOKEN", b"TELEGRAM_BOT_TOKEN", b"BEGIN OPENSSH PRIVATE KEY", b"API_KEY")):
            raise PeerDeliveryError("secret-bearing Case content is forbidden")
        digest = hashlib.sha256(raw).hexdigest()
        direct = PREFIX + json.dumps({"kind": "delivery", "hash": digest, "data": base64.b64encode(raw).decode()}, separators=(",", ":"))
        if len(direct.encode()) <= self.max_bytes:
            return [direct]
        overhead = 240
        size = max(32, ((self.max_bytes - overhead) * 3) // 4)
        chunks = [raw[i:i + size] for i in range(0, len(raw), size)]
        return [PREFIX + json.dumps({"kind": "fragment", "manifest": digest, "index": i, "count": len(chunks), "data": base64.b64encode(chunk).decode()}, separators=(",", ":")) for i, chunk in enumerate(chunks)]

    def reassemble(self, messages: Iterable[str], *, expected_sender: str | None = None, expected_recipient: str | None = None, now: datetime | None = None) -> dict[str, Any]:
        parts = []
        for raw in messages:
            if not raw.startswith(PREFIX) or len(raw.encode()) > self.max_bytes:
                raise PeerDeliveryError("invalid Case protocol envelope")
            try: parts.append(json.loads(raw[len(PREFIX):]))
            except Exception as exc: raise PeerDeliveryError("invalid Case protocol JSON") from exc
        if not parts: raise PeerDeliveryError("empty fragment set")
        if parts[0].get("kind") == "delivery":
            if len(parts) != 1: raise PeerDeliveryError("mixed delivery set")
            data = base64.b64decode(parts[0]["data"], validate=True)
            digest = parts[0]["hash"]
        else:
            manifests = {p.get("manifest") for p in parts}; counts = {p.get("count") for p in parts}
            if len(manifests) != 1 or len(counts) != 1: raise PeerDeliveryError("conflicting fragments")
            count = counts.pop()
            indexed = {p.get("index"): p for p in parts}
            if len(indexed) != count or set(indexed) != set(range(count)): raise PeerDeliveryError("incomplete fragments")
            try: data = b"".join(base64.b64decode(indexed[i]["data"], validate=True) for i in range(count))
            except Exception as exc: raise PeerDeliveryError("corrupt fragments") from exc
            digest = manifests.pop()
        if hashlib.sha256(data).hexdigest() != digest: raise PeerDeliveryError("manifest hash mismatch")
        try: value = json.loads(data)
        except Exception as exc: raise PeerDeliveryError("invalid reassembled content") from exc
        if expected_sender and value.get("sender_representative_id") != expected_sender: raise PeerDeliveryError("sender identity mismatch")
        if expected_recipient and value.get("recipient_representative_id") != expected_recipient: raise PeerDeliveryError("recipient identity mismatch")
        if now and value.get("expires_at"):
            expiry = datetime.fromisoformat(value["expires_at"].replace("Z", "+00:00"))
            if expiry <= now.astimezone(timezone.utc): raise PeerDeliveryError("delivery expired")
        return value


class PeerTelegramAdapter:
    def __init__(
        self,
        codec: PeerCodec,
        transport: Any,
        coordinator: Any,
        local_id: str,
        trusted_peers: Mapping[str, Mapping[str, Any]],
        *,
        min_send_interval_seconds: float = 0,
        clock: Any = time.monotonic,
        sleeper: Any = asyncio.sleep,
    ) -> None:
        self.codec, self.transport, self.coordinator, self.local_id, self.trusted_peers = codec, transport, coordinator, local_id, dict(trusted_peers)
        if min_send_interval_seconds < 0:
            raise ValueError("send interval must be non-negative")
        self.min_send_interval_seconds = float(min_send_interval_seconds)
        self._clock = clock
        self._sleeper = sleeper
        self._last_send_at: dict[str, float] = {}
        self._send_locks: dict[str, asyncio.Lock] = {}

    async def send(self, peer_id: str, delivery: Mapping[str, Any]) -> list[Any]:
        peer = self.trusted_peers.get(peer_id)
        current_trust = getattr(self.coordinator, "peer_trust", None)
        if (
            not peer
            or not peer.get("active", True)
            or (current_trust is not None and current_trust.get(peer_id) != peer.get("revision"))
        ):
            raise PeerTrustInactiveError("PeerTrust inactive")
        lock = self._send_locks.setdefault(peer_id, asyncio.Lock())
        results = []
        async with lock:
            for part in self.codec.encode(delivery):
                previous = self._last_send_at.get(peer_id)
                if previous is not None:
                    remaining = self.min_send_interval_seconds - (self._clock() - previous)
                    if remaining > 0:
                        await self._sleeper(remaining)
                results.append(await self.transport.send(peer["username"], part))
                self._last_send_at[peer_id] = self._clock()
        return results

    def receive(self, messages: Iterable[str], numeric_bot_id: int) -> Any:
        peers = [k for k, v in self.trusted_peers.items() if v.get("numeric_bot_id") == numeric_bot_id and v.get("active", True)]
        if len(peers) != 1: raise PeerDeliveryError("exact bot identity not trusted")
        value = self.codec.reassemble(messages, expected_sender=peers[0], expected_recipient=self.local_id, now=datetime.now(timezone.utc))
        return self.coordinator.ingest_peer(value["payload"], peers[0], delivery_id=value["delivery_id"])
