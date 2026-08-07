from __future__ import annotations

import importlib
import json
import sys
import unittest
from datetime import UTC, datetime, timedelta
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
ASSETS = PLUGIN_ROOT / "assets"
NOW = datetime(2026, 8, 7, 4, 0, tzinfo=UTC)


def _codec():
    sys.path.insert(0, str(ASSETS))
    try:
        return importlib.import_module("hermes_exchange.envelope")
    finally:
        sys.path.pop(0)


def _wire_message(*, version: int = 1, recipient: str = "ryan") -> str:
    payload = {
        "artifact_refs": [],
        "constraints": ["Do not push before owner approval"],
        "created_at": "2026-08-07T04:00:00Z",
        "envelope_id": "henv-conformance-1",
        "exchange_id": "hex-conformance-1",
        "expires_at": "2026-08-07T04:30:00Z",
        "hop_count": 0,
        "kind": "decision_request",
        "message_type": "request",
        "payload": {"question": "Should we retain the fallback?"},
        "recipient_peer": recipient,
        "sender_peer": "haha",
        "subject": "Confirm fallback behavior",
        "summary": "Please review one bounded decision.",
        "version": version,
    }
    return "HERMES_EXCHANGE/1\n" + json.dumps(
        payload,
        ensure_ascii=False,
        separators=(",", ":"),
        sort_keys=True,
    )


class ProtocolConformanceTests(unittest.TestCase):
    def test_framework_neutral_fixture_decodes_without_importing_hermes_host(self) -> None:
        codec = _codec()
        before = set(sys.modules)

        envelope = codec.decode_envelope(
            _wire_message(),
            expected_recipient="ryan",
            now=NOW,
        )

        self.assertEqual(envelope.kind, "decision_request")
        self.assertEqual(envelope.payload, {"question": "Should we retain the fallback?"})
        self.assertNotIn("hermes_cli", set(sys.modules) - before)

    def test_unknown_version_fails_closed(self) -> None:
        codec = _codec()

        with self.assertRaises(codec.EnvelopeValidationError):
            codec.decode_envelope(
                _wire_message(version=2),
                expected_recipient="ryan",
                now=NOW,
            )

    def test_wrong_recipient_fails_closed(self) -> None:
        codec = _codec()

        with self.assertRaises(codec.EnvelopeValidationError):
            codec.decode_envelope(
                _wire_message(recipient="someone-else"),
                expected_recipient="ryan",
                now=NOW,
            )

    def test_expired_fixture_fails_closed(self) -> None:
        codec = _codec()

        with self.assertRaises(codec.EnvelopeValidationError):
            codec.decode_envelope(
                _wire_message(),
                expected_recipient="ryan",
                now=NOW + timedelta(hours=1),
            )


if __name__ == "__main__":
    unittest.main()
