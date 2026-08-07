"""Telegram Bot API transport for Hermes Exchange."""

from __future__ import annotations

import math
from dataclasses import dataclass
from typing import Any, Mapping

import httpx


@dataclass(frozen=True, slots=True)
class DeliveryResult:
    """Structured outcome of one Telegram delivery attempt."""

    success: bool
    status: str
    message_id: int | None = None
    retryable: bool = False
    human_required: bool = False
    error_code: str | None = None
    status_code: int | None = None
    retry_after_seconds: float | None = None
    error_message: str | None = None


class TelegramTransport:
    """Send exchange messages through Telegram's ``sendMessage`` method."""

    def __init__(
        self,
        *,
        token: str,
        http_client: Any | None = None,
        request_timeout_seconds: float = 10.0,
        max_retry_after_seconds: float = 60.0,
    ) -> None:
        self._token = token
        self._http_client = http_client
        self._request_timeout_seconds = request_timeout_seconds
        self._max_retry_after_seconds = max(0.0, max_retry_after_seconds)

    async def send(self, username: str, text: str) -> DeliveryResult:
        if not self._token:
            return DeliveryResult(
                success=False,
                status="human_required",
                human_required=True,
                error_code="missing_bot_token",
                error_message="Telegram bot token is not configured.",
            )
        try:
            response = await self._post(username=username, text=text)
        except (TimeoutError, httpx.TimeoutException):
            return DeliveryResult(
                success=False,
                status="retryable",
                retryable=True,
                error_code="timeout",
                error_message="Telegram request timed out.",
            )
        except Exception:
            return DeliveryResult(
                success=False,
                status="human_required",
                human_required=True,
                error_code="transport_error",
                error_message="Telegram transport failed; owner action is required.",
            )

        try:
            body = response.json()
        except Exception:
            body = {}
        if not isinstance(body, Mapping):
            body = {}
        status_code = response.status_code
        if status_code == 200 and body.get("ok") is True:
            result_body = body.get("result")
            message_id = (
                result_body.get("message_id")
                if isinstance(result_body, Mapping)
                else None
            )
            return DeliveryResult(
                success=True,
                status="delivered",
                message_id=message_id,
                status_code=status_code,
            )

        description = str(body.get("description", ""))
        if status_code == 429:
            return DeliveryResult(
                success=False,
                status="retryable",
                retryable=True,
                error_code="rate_limited",
                status_code=status_code,
                retry_after_seconds=self._retry_after(response, body),
                error_message="Telegram rate limit reached.",
            )
        if 500 <= status_code <= 599:
            return DeliveryResult(
                success=False,
                status="retryable",
                retryable=True,
                error_code="telegram_server_error",
                status_code=status_code,
                error_message="Telegram service is temporarily unavailable.",
            )
        if 400 <= status_code <= 499:
            bot_to_bot_disabled = "USER_BOT_TO_BOT_DISABLED" in description.upper()
            return DeliveryResult(
                success=False,
                status="human_required",
                human_required=True,
                error_code=(
                    "bot_to_bot_disabled"
                    if bot_to_bot_disabled
                    else "telegram_client_error"
                ),
                status_code=status_code,
                error_message=(
                    "Telegram Bot-to-Bot communication is disabled for one of the bots."
                    if bot_to_bot_disabled
                    else "Telegram rejected the request; owner action is required."
                ),
            )

        return DeliveryResult(
            success=False,
            status="human_required",
            human_required=True,
            error_code="unexpected_response",
            status_code=status_code,
            error_message="Telegram returned an unexpected response.",
        )

    async def _post(self, *, username: str, text: str) -> Any:
        request = {
            "url": f"https://api.telegram.org/bot{self._token}/sendMessage",
            "json": {"chat_id": username, "text": text},
            "timeout": self._request_timeout_seconds,
        }
        if self._http_client is not None:
            return await self._http_client.post(**request)
        async with httpx.AsyncClient() as client:
            return await client.post(**request)

    def _retry_after(
        self,
        response: Any,
        body: Mapping[str, Any],
    ) -> float | None:
        parameters = body.get("parameters")
        candidate = (
            parameters.get("retry_after")
            if isinstance(parameters, Mapping)
            else None
        )
        if candidate is None:
            headers = getattr(response, "headers", {})
            candidate = headers.get("Retry-After") if isinstance(headers, Mapping) else None
        try:
            seconds = float(candidate)
        except (TypeError, ValueError):
            return None
        if not math.isfinite(seconds):
            return None
        return min(max(0.0, seconds), self._max_retry_after_seconds)
