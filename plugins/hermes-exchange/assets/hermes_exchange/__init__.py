"""Hermes Relay Lite user-plugin registration surface."""

from __future__ import annotations

from typing import Any


_REQUIRED_HOOKS = frozenset({"pre_gateway_dispatch"})


def _assert_host_capabilities() -> None:
    from hermes_cli.plugins import VALID_HOOKS

    missing = sorted(_REQUIRED_HOOKS - set(VALID_HOOKS))
    if missing:
        raise RuntimeError("Hermes Relay requires host plugin hooks: " + ",".join(missing))


def create_runtime(_ctx: Any) -> Any:
    from .runtime import RelayRuntime

    return RelayRuntime.from_user_scope()


def register(ctx: Any) -> None:
    """Register notification receive/send and the optional local executor."""

    from . import schemas

    _assert_host_capabilities()
    runtime = create_runtime(ctx)
    ctx.register_tool(
        name="relay_notify",
        toolset="hermes-exchange",
        schema=schemas.RELAY_NOTIFY,
        handler=runtime.notify_tool,
        is_async=True,
        description=schemas.RELAY_NOTIFY["description"],
    )
    if runtime.config is not None and runtime.config.execution.enabled:
        ctx.register_tool(
            name="relay_execute",
            toolset="hermes-exchange",
            schema=schemas.RELAY_EXECUTE,
            handler=runtime.execute_tool,
            is_async=True,
            description=schemas.RELAY_EXECUTE["description"],
        )
    ctx.register_hook("pre_gateway_dispatch", runtime.pre_gateway_dispatch)
