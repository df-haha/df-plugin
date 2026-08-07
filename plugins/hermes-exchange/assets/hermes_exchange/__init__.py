"""Hermes Exchange user plugin registration surface."""

from __future__ import annotations

from typing import Any


_REQUIRED_HOOKS = frozenset(
    {
        "pre_gateway_dispatch",
        "pre_llm_call",
        "post_llm_call",
        "pre_tool_call",
        "transform_llm_output",
    }
)


def _assert_host_capabilities() -> None:
    """Fail closed when the host cannot enforce the exchange policy."""

    from hermes_cli.plugins import VALID_HOOKS

    missing_hooks = sorted(_REQUIRED_HOOKS - set(VALID_HOOKS))
    if missing_hooks:
        raise RuntimeError(
            "Hermes Exchange requires host plugin hooks: "
            + ",".join(missing_hooks)
        )


def create_runtime(ctx: Any) -> Any:
    """Create the fail-closed runtime lazily at plugin registration."""

    from .workflow import ExchangeRuntime

    return ExchangeRuntime.from_user_scope(llm=ctx.llm)


def register(ctx: Any) -> None:
    """Register draft/read tools and the five human-gate policy hooks."""

    from . import schemas

    _assert_host_capabilities()
    runtime = create_runtime(ctx)
    ctx.register_tool(
        name="exchange_prepare",
        toolset="hermes-exchange",
        schema=schemas.PREPARE,
        handler=runtime.prepare_tool,
    )
    ctx.register_tool(
        name="exchange_status",
        toolset="hermes-exchange",
        schema=schemas.STATUS,
        handler=runtime.status_tool,
    )
    ctx.register_tool(
        name="exchange_list",
        toolset="hermes-exchange",
        schema=schemas.LIST,
        handler=runtime.list_tool,
    )

    ctx.register_hook("pre_gateway_dispatch", runtime.pre_gateway_dispatch)
    ctx.register_hook("pre_llm_call", runtime.pre_llm_call)
    ctx.register_hook("post_llm_call", runtime.post_llm_call)
    ctx.register_hook("pre_tool_call", runtime.pre_tool_call)
    ctx.register_hook("transform_llm_output", runtime.transform_llm_output)
