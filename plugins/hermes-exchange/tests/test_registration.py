from __future__ import annotations

import sys
import types
import unittest
from pathlib import Path


PLUGIN_ROOT = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(PLUGIN_ROOT / "assets"))

plugins = types.ModuleType("hermes_cli.plugins")
plugins.VALID_HOOKS = {"pre_gateway_dispatch"}
hermes_cli = types.ModuleType("hermes_cli")
hermes_cli.plugins = plugins
sys.modules.setdefault("hermes_cli", hermes_cli)
sys.modules.setdefault("hermes_cli.plugins", plugins)

import hermes_exchange  # noqa: E402


class _Context:
    def __init__(self) -> None:
        self.tools: list[dict[str, object]] = []
        self.hooks: list[tuple[str, object]] = []

    def register_tool(self, **kwargs: object) -> None:
        self.tools.append(kwargs)

    def register_hook(self, name: str, handler: object) -> None:
        self.hooks.append((name, handler))


class _Runtime:
    def __init__(self, *, enabled: bool) -> None:
        self.config = types.SimpleNamespace(
            execution=types.SimpleNamespace(enabled=enabled)
        )

    async def notify_tool(self, _args: object) -> str:
        return "{}"

    async def execute_tool(self, _args: object) -> str:
        return "{}"

    def pre_gateway_dispatch(self, **_kwargs: object) -> None:
        return None


class RegistrationTests(unittest.TestCase):
    def test_core_registers_only_async_notify_and_receive_hook(self) -> None:
        context = _Context()
        original = hermes_exchange.create_runtime
        hermes_exchange.create_runtime = lambda _ctx: _Runtime(enabled=False)
        try:
            hermes_exchange.register(context)
        finally:
            hermes_exchange.create_runtime = original

        self.assertEqual([tool["name"] for tool in context.tools], ["relay_notify"])
        self.assertTrue(context.tools[0]["is_async"])
        self.assertIn("cannot authorize", str(context.tools[0]["schema"]["description"]))
        self.assertEqual([name for name, _handler in context.hooks], ["pre_gateway_dispatch"])

    def test_execute_tool_only_exists_when_locally_enabled(self) -> None:
        context = _Context()
        original = hermes_exchange.create_runtime
        hermes_exchange.create_runtime = lambda _ctx: _Runtime(enabled=True)
        try:
            hermes_exchange.register(context)
        finally:
            hermes_exchange.create_runtime = original

        self.assertEqual(
            [tool["name"] for tool in context.tools],
            ["relay_notify", "relay_execute"],
        )
        execute = context.tools[1]
        self.assertTrue(execute["is_async"])
        self.assertIn("configured repository", str(execute["schema"]["description"]))


if __name__ == "__main__":
    unittest.main()
