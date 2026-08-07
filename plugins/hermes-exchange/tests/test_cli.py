from __future__ import annotations

import io
import json
import sys
import types
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "assets"))

try:
    import hermes_constants  # noqa: F401
except ModuleNotFoundError:
    constants = types.ModuleType("hermes_constants")
    constants.get_hermes_home = lambda: Path("/unused-test-profile")
    sys.modules["hermes_constants"] = constants

from hermes_exchange.cli import main  # noqa: E402


class _Peer:
    name = "ryan"
    telegram_username = "@ryan_bot"


class _Receive:
    max_message_bytes = 3_500


class _Config:
    local_peer = "haha"
    receive = _Receive()

    @staticmethod
    def peer(name: str) -> _Peer:
        if name != "ryan":
            raise ValueError("unknown peer")
        return _Peer()


class _Delivery:
    success = True
    status = "delivered"
    message_id = 42
    retryable = False
    human_required = False
    error_code = None
    error_message = None


class _Transport:
    instances: list["_Transport"] = []

    def __init__(self, *, token: str) -> None:
        self.token = token
        self.calls: list[tuple[str, str]] = []
        self.instances.append(self)

    async def send(self, username: str, text: str) -> _Delivery:
        self.calls.append((username, text))
        return _Delivery()


class RelayCliTests(unittest.TestCase):
    def setUp(self) -> None:
        _Transport.instances.clear()

    def test_notify_reads_body_from_stdin_and_uses_configured_peer(self) -> None:
        output = io.StringIO()
        loaded_paths: list[str | None] = []

        def load(path: str | None = None) -> _Config:
            loaded_paths.append(path)
            return _Config()

        code = main(
            [
                "notify",
                "--config",
                "/tmp/relay.yaml",
                "--peer",
                "ryan",
                "--kind",
                "handoff",
                "--subject",
                "Map work ready",
            ],
            stdin=io.StringIO("Please review the query contract."),
            stdout=output,
            environ={"TELEGRAM_BOT_TOKEN": "test-token"},
            config_loader=load,
            transport_factory=_Transport,
        )

        result = json.loads(output.getvalue())
        self.assertEqual(code, 0)
        self.assertEqual(loaded_paths, ["/tmp/relay.yaml"])
        self.assertEqual(_Transport.instances[0].calls[0][0], "@ryan_bot")
        self.assertIn("Please review the query contract.", _Transport.instances[0].calls[0][1])
        self.assertNotIn("test-token", output.getvalue())
        self.assertEqual(result["status"], "delivered")

    def test_notify_without_token_fails_closed_before_transport(self) -> None:
        output = io.StringIO()

        code = main(
            ["notify", "--peer", "ryan", "--subject", "Map work ready"],
            stdin=io.StringIO("body"),
            stdout=output,
            environ={},
            config_loader=lambda _path=None: _Config(),
            transport_factory=_Transport,
        )

        result = json.loads(output.getvalue())
        self.assertEqual(code, 2)
        self.assertEqual(result["error_code"], "missing_bot_token")
        self.assertEqual(_Transport.instances, [])


if __name__ == "__main__":
    unittest.main()
