from __future__ import annotations

from pathlib import Path
import sys
import tempfile
import types
import unittest
from unittest.mock import patch


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "assets"))

try:
    import hermes_constants  # noqa: F401
except ModuleNotFoundError:
    hermes_constants_stub = types.ModuleType("hermes_constants")
    hermes_constants_stub.get_hermes_home = lambda: Path("/unused-test-profile")
    sys.modules["hermes_constants"] = hermes_constants_stub

from hermes_exchange import config as config_module  # noqa: E402
from hermes_exchange.config import (  # noqa: E402
    ExchangeConfigError,
    config_from_mapping,
    load_config,
)


def base_config() -> dict[str, object]:
    return {
        "local_peer": "haha",
        "owner": {
            "telegram_chat_id": 123456789,
        },
        "peers": {
            "ryan": {
                "telegram_username": "@ryan_bot",
                "expected_sender_id": 987654321,
                "enabled": True,
            }
        },
    }


class RelayConfigTests(unittest.TestCase):
    def test_execution_is_disabled_with_no_repositories_by_default(self) -> None:
        parsed = config_from_mapping(base_config())

        self.assertFalse(parsed.execution.enabled)
        self.assertEqual(parsed.execution.repositories, {})
        self.assertEqual(parsed.receive.max_message_bytes, 3500)
        self.assertEqual(parsed.receive.min_peer_interval_seconds, 3)
        self.assertFalse(hasattr(parsed, "owner_user_ids"))
        self.assertFalse(hasattr(config_module, "state_dir"))
        self.assertFalse(hasattr(config_module, "default_state_path"))
        with patch.object(
            sys.modules["hermes_constants"],
            "get_hermes_home",
            return_value=Path("/profiles/reviewer"),
        ):
            self.assertEqual(
                config_module.default_config_path(),
                Path("/profiles/reviewer/state/hermes-exchange/config.yaml"),
            )

        misleading_owner_acl = base_config()
        misleading_owner_acl["owner"]["allowed_user_ids"] = [123456789]
        with self.assertRaisesRegex(ExchangeConfigError, "unknown fields"):
            config_from_mapping(misleading_owner_acl)

    def test_enabled_execution_loads_fixed_existing_repository_aliases(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            claude_repo = root / "claude-repo"
            codex_repo = root / "codex-repo"
            claude_repo.mkdir()
            codex_repo.mkdir()
            config_path = root / "config.yaml"
            config_path.write_text(
                f"""
local_peer: haha
owner:
  telegram_chat_id: 123456789
peers:
  ryan:
    telegram_username: "@ryan_bot"
    expected_sender_id: 987654321
receive:
  max_message_bytes: 3500
  min_peer_interval_seconds: 3
execution:
  enabled: true
  timeout_seconds: 1800
  output_limit_chars: 20000
  repositories:
    map-review:
      path: "{claude_repo}"
      executor: claude
    api-review:
      path: "{codex_repo}"
      executor: codex
""".lstrip(),
                encoding="utf-8",
            )

            parsed = load_config(config_path)

        self.assertTrue(parsed.execution.enabled)
        self.assertEqual(
            parsed.execution.repositories["map-review"].path,
            claude_repo.resolve(),
        )
        self.assertEqual(parsed.execution.repositories["map-review"].executor, "claude")
        self.assertEqual(parsed.execution.repositories["api-review"].executor, "codex")

    def test_enabled_execution_rejects_unsafe_repository_mappings(self) -> None:
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            repository = root / "repo"
            repository.mkdir()
            regular_file = root / "not-a-directory"
            regular_file.write_text("data", encoding="utf-8")
            invalid_executions = (
                {"enabled": True, "repositories": {}},
                {
                    "enabled": False,
                    "repositories": {
                        "repo": {"path": str(repository), "executor": "claude"}
                    },
                },
                {
                    "enabled": True,
                    "repositories": {
                        "repo": {"path": str(repository), "executor": "shell"}
                    },
                },
                {
                    "enabled": True,
                    "repositories": {
                        "repo": {"path": str(root / "missing"), "executor": "claude"}
                    },
                },
                {
                    "enabled": True,
                    "repositories": {
                        "repo": {"path": str(regular_file), "executor": "codex"}
                    },
                },
                {
                    "enabled": True,
                    "repositories": {
                        "repo": {"path": "relative/repo", "executor": "claude"}
                    },
                },
                {
                    "enabled": True,
                    "repositories": {
                        "*": {"path": str(repository), "executor": "claude"}
                    },
                },
            )
            for execution in invalid_executions:
                raw = base_config()
                raw["execution"] = execution
                with self.subTest(execution=execution):
                    with self.assertRaises(ExchangeConfigError):
                        config_from_mapping(raw)

    def test_duplicate_sender_ids_and_wildcard_or_invalid_peers_are_rejected(self) -> None:
        duplicate = base_config()
        duplicate["peers"]["alex"] = {
            "telegram_username": "@alex_bot",
            "expected_sender_id": 987654321,
        }
        wildcard_name = base_config()
        wildcard_name["peers"] = {
            "*": {
                "telegram_username": "@ryan_bot",
                "expected_sender_id": 111,
            }
        }
        wildcard_username = base_config()
        wildcard_username["peers"] = {
            "ryan": {"telegram_username": "@*", "expected_sender_id": 111}
        }
        duplicate_username = base_config()
        duplicate_username["peers"]["alex"] = {
            "telegram_username": "@RYAN_BOT",
            "expected_sender_id": 222,
        }
        local_as_remote = base_config()
        local_as_remote["peers"] = {
            "haha": {
                "telegram_username": "@haha_bot",
                "expected_sender_id": 111,
            }
        }

        for raw in (
            duplicate,
            wildcard_name,
            wildcard_username,
            duplicate_username,
            local_as_remote,
        ):
            with self.subTest(peers=raw["peers"]):
                with self.assertRaises(ExchangeConfigError):
                    config_from_mapping(raw)

    def test_unsafe_receive_and_execution_bounds_are_rejected(self) -> None:
        invalid_overrides = (
            ("receive", "max_message_bytes", 255),
            ("receive", "max_message_bytes", 3501),
            ("receive", "min_peer_interval_seconds", 0),
            ("receive", "min_peer_interval_seconds", 3601),
            ("execution", "timeout_seconds", 0),
            ("execution", "timeout_seconds", 3601),
            ("execution", "output_limit_chars", 0),
            ("execution", "output_limit_chars", 100001),
        )
        for section, key, value in invalid_overrides:
            raw = base_config()
            raw[section] = {key: value}
            with self.subTest(section=section, key=key, value=value):
                with self.assertRaises(ExchangeConfigError):
                    config_from_mapping(raw)


if __name__ == "__main__":
    unittest.main()
