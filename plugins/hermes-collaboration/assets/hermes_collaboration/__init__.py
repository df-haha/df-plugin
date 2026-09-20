"""Hermes Collaboration standalone user-plugin registration surface."""

from __future__ import annotations

from typing import Any


VERSION = "0.1.0"
PROTOCOL_VERSION = "HERMES_CASE_V1"
_REQUIRED_HOOKS = frozenset({"pre_gateway_dispatch", "pre_tool_call", "post_tool_call"})


def _assert_host_capabilities() -> None:
    from hermes_cli.plugins import VALID_HOOKS

    missing = sorted(_REQUIRED_HOOKS - set(VALID_HOOKS))
    if missing:
        raise RuntimeError("Hermes Collaboration requires host hooks: " + ",".join(missing))


def create_runtime(ctx: Any) -> Any:
    from hermes_constants import get_hermes_home

    from .runtime import CollaborationRuntime

    return CollaborationRuntime.from_profile(
        get_hermes_home(), llm=getattr(ctx, "llm", None),
        github_webhook_secret=getattr(ctx, "github_webhook_secret", None),
        github_allowed_actors=getattr(ctx, "github_allowed_actors", ()),
        github_machine_login=getattr(ctx, "github_machine_login", None),
        github_allowed_labels=getattr(ctx, "github_allowed_labels", ()),
        github_client=getattr(ctx, "github_client", None),
        executor_runners=getattr(ctx, "executor_runners", {}),
        repository_paths=getattr(ctx, "repository_paths", {}),
    )


def register(ctx: Any) -> None:
    _assert_host_capabilities()
    runtime = create_runtime(ctx)
    ctx.register_tool(name="case_submit", toolset="hermes-collaboration", schema=runtime.submit_schema, handler=runtime.submit_tool)
    ctx.register_tool(name="case_view", toolset="hermes-collaboration", schema=runtime.view_schema, handler=runtime.view_tool)
    ctx.register_tool(name="case_list", toolset="hermes-collaboration", schema=runtime.list_schema, handler=runtime.list_tool)
    ctx.register_tool(name="case_github_event", toolset="hermes-collaboration", schema=runtime.github_event_schema, handler=runtime.github_event_tool)
    ctx.register_tool(name="case_run_work", toolset="hermes-collaboration", schema=runtime.run_work_schema, handler=runtime.run_work_tool)
    ctx.register_hook("pre_gateway_dispatch", runtime.pre_gateway_dispatch)
    ctx.register_hook("pre_tool_call", runtime.pre_tool_call)
    ctx.register_hook("post_tool_call", runtime.post_tool_call)
