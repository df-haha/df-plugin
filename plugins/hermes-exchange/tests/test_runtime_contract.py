from __future__ import annotations

import hashlib
import json
import sys
import tempfile
import types
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "assets"))

try:
    import httpx  # noqa: F401
except ModuleNotFoundError:
    sys.modules["httpx"] = types.ModuleType("httpx")

try:
    import yaml  # noqa: F401
except ModuleNotFoundError:
    sys.modules["yaml"] = types.ModuleType("yaml")

try:
    import hermes_constants  # noqa: F401
except ModuleNotFoundError:
    hermes_constants_stub = types.ModuleType("hermes_constants")
    hermes_constants_stub.get_hermes_home = lambda: Path("/unused-test-profile")
    sys.modules["hermes_constants"] = hermes_constants_stub

from hermes_exchange.config import ExchangeConfig, PeerConfig, PolicyConfig  # noqa: E402
from hermes_exchange.schemas import PREPARE  # noqa: E402
from hermes_exchange.store import ExchangeStore  # noqa: E402
from hermes_exchange.workflow import ExchangeRuntime  # noqa: E402


def _config() -> ExchangeConfig:
    return ExchangeConfig(
        local_peer="haha",
        owner_chat_id=1001,
        owner_user_ids=frozenset({1001}),
        peers={"ryan": PeerConfig("ryan", "@ryan_bot", 222, True)},
        policy=PolicyConfig(ttl_seconds=1800, max_hops=2),
    )


def _prepare_args(**updates: object) -> dict[str, object]:
    values: dict[str, object] = {
        "peer": "ryan",
        "kind": "decision_request",
        "subject": "Confirm fallback behavior",
        "summary": "Please review one bounded decision.",
        "payload": "Should we retain the fallback?",
        "constraints": [],
        "artifact_refs": [],
    }
    values.update(updates)
    return values


class RuntimeContractTests(unittest.TestCase):
    def setUp(self) -> None:
        self._temporary = tempfile.TemporaryDirectory()
        self.root = Path(self._temporary.name)
        self.store = ExchangeStore(self.root / "exchange.sqlite3")
        self.runtime = ExchangeRuntime(
            config=_config(),
            store=self.store,
            transport=None,
            llm=None,
        )

    def tearDown(self) -> None:
        self._temporary.cleanup()

    def test_prepare_schema_matches_wire_field_limits(self) -> None:
        properties = PREPARE["parameters"]["properties"]

        self.assertEqual(properties["constraints"]["items"]["maxLength"], 500)
        self.assertEqual(properties["artifact_refs"]["items"]["maxLength"], 500)
        self.assertEqual(properties["execution_hint"]["maxLength"], 100)
        self.assertEqual(
            properties["kind"]["pattern"],
            "^[a-z0-9](?:[a-z0-9_-]{0,62}[a-z0-9])?$",
        )

    def test_prepare_rejects_invalid_kind_before_persisting(self) -> None:
        result = json.loads(
            self.runtime.prepare_tool(_prepare_args(kind="decision_request_"))
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(self.store.list_exchanges(states=None), [])

    def test_prepare_rejects_unencodable_payload_before_persisting(self) -> None:
        result = json.loads(
            self.runtime.prepare_tool(_prepare_args(payload="x" * 4_000))
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(self.store.list_exchanges(states=None), [])

    def test_prepare_rejects_overlong_constraint_before_persisting(self) -> None:
        result = json.loads(
            self.runtime.prepare_tool(_prepare_args(constraints=["x" * 501]))
        )

        self.assertEqual(result["status"], "error")
        self.assertEqual(self.store.list_exchanges(states=None), [])

    def test_completed_result_is_captured_when_exchange_expires_mid_execution(self) -> None:
        now = datetime.now(UTC)
        self.store.create_exchange(
            {
                "exchange_id": "exchange-1",
                "direction": "inbound",
                "peer": "ryan",
                "kind": "decision_request",
                "policy": "human-gated",
                "state": "executing",
                "owner_chat_id": "1001",
                "subject": "Confirm fallback behavior",
                "summary": "Review one decision",
                "payload": "Review it.",
                "constraints": [],
                "artifact_refs": [],
                "request_envelope_id": "env-request-1",
                "metadata": {
                    "execution_token_hash": hashlib.sha256(b"test-token").hexdigest()
                },
                "created_at": now.isoformat(),
                "updated_at": now.isoformat(),
                "expires_at": (now + timedelta(minutes=30)).isoformat(),
            }
        )
        self.runtime.pre_llm_call(
            user_message="HERMES_EXCHANGE_EXECUTION/1 exchange-1 test-token\nExecute."
        )
        self.store.expire_due(now=now + timedelta(hours=1))

        visible = self.runtime.transform_llm_output(response_text="Completed locally.")

        exchange = self.store.get_exchange("exchange-1")
        self.assertEqual(exchange["state"], "expired")
        self.assertEqual(exchange["latest_result"], "Completed locally.")
        self.assertIn("expired", visible or "")
        self.assertNotIn("/exchange return", visible or "")

    def test_expiry_preserves_states_without_a_legal_expired_transition(self) -> None:
        now = datetime.now(UTC)
        for exchange_id, state in (
            ("draft-1", "draft"),
            ("received-result-1", "result_received"),
            ("sent-result-1", "result_sent"),
        ):
            self.store.create_exchange(
                {
                    "exchange_id": exchange_id,
                    "direction": "outgoing",
                    "peer": "ryan",
                    "kind": "decision_request",
                    "policy": "human-gated",
                    "state": state,
                    "owner_chat_id": "1001",
                    "subject": "subject",
                    "summary": "summary",
                    "payload": "payload",
                    "constraints": [],
                    "artifact_refs": [],
                    "created_at": now.isoformat(),
                    "updated_at": now.isoformat(),
                    "expires_at": (now + timedelta(minutes=30)).isoformat(),
                }
            )

        self.store.expire_due(now=now + timedelta(hours=1))

        self.assertEqual(self.store.get_exchange("draft-1")["state"], "draft")
        self.assertEqual(
            self.store.get_exchange("received-result-1")["state"], "result_received"
        )
        self.assertEqual(
            self.store.get_exchange("sent-result-1")["state"], "result_sent"
        )


if __name__ == "__main__":
    unittest.main()
