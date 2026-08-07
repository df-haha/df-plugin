from __future__ import annotations

import sys
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "assets"))

from hermes_exchange.transport import TelegramTransport  # noqa: E402


class _Response:
    def __init__(self, status_code: int, payload: dict[str, object]) -> None:
        self.status_code = status_code
        self.payload = payload
        self.headers: dict[str, str] = {}

    def json(self) -> dict[str, object]:
        return self.payload


class _Client:
    def __init__(self, outcome: object) -> None:
        self.outcome = outcome
        self.calls: list[dict[str, object]] = []

    async def post(self, **kwargs: object) -> _Response:
        self.calls.append(dict(kwargs))
        if isinstance(self.outcome, BaseException):
            raise self.outcome
        return self.outcome  # type: ignore[return-value]


class TelegramTransportTests(unittest.IsolatedAsyncioTestCase):
    async def test_success_preserves_configured_bot_username(self) -> None:
        client = _Client(_Response(200, {"ok": True, "result": {"message_id": 731}}))

        result = await TelegramTransport(token="token", http_client=client).send(
            "@ryan_bot", "wire"
        )

        self.assertTrue(result.success)
        self.assertEqual(result.message_id, 731)
        self.assertEqual(client.calls[0]["json"], {"chat_id": "@ryan_bot", "text": "wire"})

    async def test_bot_to_bot_disabled_is_permanent_human_action(self) -> None:
        client = _Client(
            _Response(
                403,
                {"ok": False, "description": "Forbidden: USER_BOT_TO_BOT_DISABLED"},
            )
        )

        result = await TelegramTransport(token="token", http_client=client).send(
            "@ryan_bot", "wire"
        )

        self.assertFalse(result.retryable)
        self.assertTrue(result.human_required)
        self.assertEqual(result.error_code, "bot_to_bot_disabled")

    async def test_transport_error_never_exposes_token(self) -> None:
        token = "123456:top-secret"
        client = _Client(RuntimeError(f"failed at /bot{token}/sendMessage"))

        result = await TelegramTransport(token=token, http_client=client).send(
            "@ryan_bot", "wire"
        )

        self.assertEqual(result.error_code, "transport_error")
        self.assertNotIn(token, repr(result))


if __name__ == "__main__":
    unittest.main()
