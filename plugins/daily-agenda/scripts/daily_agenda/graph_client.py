"""GraphSource — MCP client wrapper for the df-graph plugin.

Starts a df-graph MCP server via stdio, validates that the ``immutable_ids``
parameter is available (DF_GRAPH_TOO_OLD otherwise), and exposes synchronous
methods that parse df-graph's text-based tool output.

The server command is read from ``~/.claude.json`` → ``mcpServers["df-graph"]``.
"""

from __future__ import annotations

import asyncio
import json
import re
import tempfile
import threading
from contextlib import contextmanager
from pathlib import Path
from typing import Any, Generator

from mcp import ClientSession, StdioServerParameters
from mcp.client.stdio import stdio_client


class GraphClientError(Exception):
    """A graph operation failed with a machine-readable code."""

    def __init__(self, code: str, detail: str = "") -> None:
        self.code = code
        self.detail = detail
        super().__init__(f"{code}: {detail}" if detail else code)


# ---------------------------------------------------------------------------
# Command source resolution
# ---------------------------------------------------------------------------

def _load_claude_json(claude_json_path: Path | None = None) -> dict[str, Any] | None:
    """Read ``~/.claude.json`` and return the ``mcpServers["df-graph"]`` entry.

    Only reads the **top-level** ``mcpServers`` key (user scope).  Does NOT
    read ``projects[...]`` entries or cwd ``.mcp.json`` — those are
    project-scoped and would bypass Claude Code's trust prompt.
    """
    path = claude_json_path or Path.home() / ".claude.json"
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (FileNotFoundError, json.JSONDecodeError, OSError):
        return None
    if not isinstance(data, dict):
        return None
    servers = data.get("mcpServers", {})
    if not isinstance(servers, dict):
        return None
    return servers.get("df-graph")


def resolve_server_params(
    claude_json_path: Path | None = None,
    server_config: dict[str, Any] | None = None,
) -> StdioServerParameters:
    """Resolve the df-graph MCP server command.

    The sole production source is the **user-scope** ``~/.claude.json``
    top-level ``mcpServers["df-graph"]`` entry — the same registration that
    ``claude mcp add df-graph --scope user`` writes.  This file is NOT
    influenced by the working directory; project-scoped servers
    (``projects[...]`` in ``~/.claude.json`` or cwd ``.mcp.json``) are
    deliberately ignored so a malicious repo cannot inject its own command.

    *server_config* overrides all other sources (used by tests to inject a
    fake server command without touching the real config file).
    """
    cfg = server_config if server_config is not None else _load_claude_json(claude_json_path)

    if not isinstance(cfg, dict):
        raise GraphClientError("DF_GRAPH_NOT_REGISTERED")
    if cfg.get("type") != "stdio":
        raise GraphClientError("DF_GRAPH_NOT_REGISTERED")

    command = cfg.get("command")
    if not isinstance(command, str) or not command:
        raise GraphClientError("DF_GRAPH_NOT_REGISTERED")

    args = cfg.get("args", [])
    if not isinstance(args, list) or not all(isinstance(a, str) for a in args):
        raise GraphClientError("DF_GRAPH_NOT_REGISTERED")

    env = cfg.get("env")
    if env is not None:
        if not isinstance(env, dict):
            raise GraphClientError("DF_GRAPH_NOT_REGISTERED")
        if not all(isinstance(k, str) and isinstance(v, str) for k, v in env.items()):
            raise GraphClientError("DF_GRAPH_NOT_REGISTERED")

    return StdioServerParameters(
        command=command,
        args=args,
        env=env if env else None,
    )


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

_FENCE_BEGIN_RE = re.compile(r"<<<BEGIN UNTRUSTED .+>>>")
_FENCE_END_RE = re.compile(r"<<<END UNTRUSTED .+>>>")


def _parse_summary_json(text: str) -> tuple[str, Any]:
    """Parse df-graph's ``summary\\nJSON`` format.  Returns (summary, parsed)."""
    lines = text.split("\n", 1)
    summary = lines[0]
    if len(lines) < 2:
        return summary, None
    try:
        return summary, json.loads(lines[1])
    except json.JSONDecodeError:
        return summary, None


def _parse_mail_get(text: str) -> dict[str, Any]:
    """Parse ``mail_get`` concise output into structured fields.

    Format::
        Subject: ...
        From: ...
        To: ...
        Cc: ...          (optional)
        Date: ...
        Internet-Message-Id: ...  (when immutable_ids=True, from patched df-graph)
        Body:
        <<<BEGIN UNTRUSTED EMAIL BODY ...>>>
        ...body...
        <<<END UNTRUSTED EMAIL BODY ...>>>
    """
    headers: dict[str, str] = {}
    body_lines: list[str] = []
    in_body = False
    fence_depth = 0

    for line in text.split("\n"):
        if not in_body:
            if line.startswith("Body:"):
                in_body = True
                continue
            if ":" in line:
                key, _, value = line.partition(":")
                headers[key.strip()] = value.strip()
        else:
            if _FENCE_BEGIN_RE.search(line):
                fence_depth += 1
                if fence_depth == 1:
                    continue  # skip opening fence
            elif _FENCE_END_RE.search(line):
                if fence_depth <= 1:
                    # Last closing fence — everything after is attachments metadata
                    break
                fence_depth -= 1
            body_lines.append(line)

    return {
        "subject": headers.get("Subject", ""),
        "from": headers.get("From", ""),
        "to": headers.get("To", ""),
        "received_at": headers.get("Date", ""),
        "internet_message_id": headers.get("Internet-Message-Id", ""),
        "body": "\n".join(body_lines),
    }


def _check_error(text: str) -> None:
    """Raise on df-graph error responses."""
    if text.startswith("ERROR:"):
        detail = text[6:].strip()
        if "過期" in detail or "expired" in detail.lower() or "auth" in detail.lower():
            raise GraphClientError("DF_GRAPH_AUTH", detail)
        raise GraphClientError("GRAPH_FAILED", detail)


def _check_truncation(summary: str) -> None:
    """Raise on truncated results (fail-closed)."""
    if "WARNING" in summary and "安全上限" in summary:
        raise GraphClientError(
            "GRAPH_TRUNCATED",
            "Result exceeded the safety limit; cannot guarantee completeness.",
        )


# ---------------------------------------------------------------------------
# Async session on a dedicated thread
# ---------------------------------------------------------------------------

class GraphSource:
    """Synchronous wrapper around an MCP client session to df-graph.

    Use as a context manager::

        with open_graph(server_config=...) as graph:
            items = graph.mail_list(days=7, folder="inbox")
    """

    def __init__(self) -> None:
        self._loop: asyncio.AbstractEventLoop | None = None
        self._thread: threading.Thread | None = None
        self._session: ClientSession | None = None
        self._ready = threading.Event()
        self._stop = asyncio.Event()
        self._error: Exception | None = None
        self._has_folder_list: bool = False
        # Server stderr goes here instead of our own stderr: df-graph prints
        # pydantic warnings and "Processing request..." lines on every call,
        # and on a crash the tail is the only useful diagnostic.
        self._errlog = tempfile.TemporaryFile(mode="w+", encoding="utf-8", errors="replace")

    # -- lifecycle -----------------------------------------------------------

    def _start(self, params: StdioServerParameters) -> None:
        self._thread = threading.Thread(
            target=self._run_loop, args=(params,), daemon=True,
        )
        self._thread.start()
        ready = self._ready.wait(timeout=30)
        if self._error:
            if isinstance(self._error, GraphClientError):
                raise self._error
            # Anything else (process exited, broken pipe, protocol error) means
            # the registered command could not be started — a different
            # problem from "not registered", which resolve_server_params covers.
            raise GraphClientError(
                "DF_GRAPH_START_FAILED",
                f"{type(self._error).__name__}: {self._error}\n{self._stderr_tail()}".rstrip(),
            )
        if not ready:
            raise GraphClientError("DF_GRAPH_START_FAILED", f"timed out after 30s\n{self._stderr_tail()}".rstrip())

    def _stderr_tail(self, max_lines: int = 20, max_chars: int = 2000) -> str:
        try:
            self._errlog.seek(0)
            lines = self._errlog.read().splitlines()
        except (OSError, ValueError):
            return ""
        return "\n".join(lines[-max_lines:])[-max_chars:]

    def _run_loop(self, params: StdioServerParameters) -> None:
        loop = asyncio.new_event_loop()
        self._loop = loop
        self._stop = asyncio.Event()
        try:
            loop.run_until_complete(self._session_lifecycle(params))
        except Exception as exc:
            self._error = exc
            self._ready.set()
        finally:
            loop.close()

    async def _session_lifecycle(self, params: StdioServerParameters) -> None:
        async with stdio_client(params, errlog=self._errlog) as (read_stream, write_stream):
            async with ClientSession(read_stream, write_stream) as session:
                await session.initialize()
                self._session = session

                # Validate that immutable_ids is available
                await self._check_tool_schema(session)

                self._ready.set()
                # Keep alive until shutdown is requested
                await self._stop.wait()

    async def _check_tool_schema(self, session: ClientSession) -> None:
        """Verify that df-graph supports ``immutable_ids``."""
        tools_result = await session.list_tools()
        tool_map = {t.name: t for t in tools_result.tools}

        for name in ("mail_list_recent", "mail_get"):
            tool = tool_map.get(name)
            if tool is None:
                raise GraphClientError("DF_GRAPH_NOT_REGISTERED", f"Tool {name} not found")
            schema = tool.inputSchema or {}
            props = schema.get("properties", {})
            if "immutable_ids" not in props:
                raise GraphClientError("DF_GRAPH_TOO_OLD", f"{name} lacks immutable_ids")

        # folder_list is optional (newer df-graph only); record presence
        self._has_folder_list = "folder_list" in tool_map

    def _shutdown(self) -> None:
        if self._loop and not self._loop.is_closed():
            self._loop.call_soon_threadsafe(self._stop.set)
        if self._thread:
            self._thread.join(timeout=10)
        try:
            self._errlog.close()
        except OSError:
            pass

    # -- tool calls ----------------------------------------------------------

    def _call_tool(self, name: str, arguments: dict[str, Any]) -> str:
        if not self._session or not self._loop:
            raise GraphClientError("GRAPH_FAILED", "Session not initialized")

        future = asyncio.run_coroutine_threadsafe(
            self._session.call_tool(name, arguments),
            self._loop,
        )
        try:
            result = future.result(timeout=60)
        except asyncio.TimeoutError:
            raise GraphClientError("GRAPH_FAILED", "Tool call timed out")
        except Exception as exc:
            detail = str(exc)
            if "auth" in detail.lower() or "expired" in detail.lower() or "401" in detail:
                raise GraphClientError("DF_GRAPH_AUTH", detail)
            raise GraphClientError("GRAPH_FAILED", detail)

        # MCP tool result: list of content blocks
        if result.isError:
            text = ""
            for block in result.content:
                if hasattr(block, "text"):
                    text += block.text
            if "auth" in text.lower() or "expired" in text.lower() or "401" in text:
                raise GraphClientError("DF_GRAPH_AUTH", text)
            raise GraphClientError("GRAPH_FAILED", text)

        text_parts: list[str] = []
        for block in result.content:
            if hasattr(block, "text"):
                text_parts.append(block.text)
        return "\n".join(text_parts)

    # -- public API ----------------------------------------------------------

    def mail_list(self, *, days: int, folder: str = "inbox") -> list[dict[str, Any]]:
        """List recent emails.  Returns parsed JSON items."""
        raw = self._call_tool("mail_list_recent", {
            "days": days,
            "folder": folder,
            "immutable_ids": True,
        })
        _check_error(raw)
        summary, items = _parse_summary_json(raw)
        _check_truncation(summary)
        if not isinstance(items, list):
            raise GraphClientError("GRAPH_FAILED", "Unexpected mail_list format")
        return items

    def mail_get(self, message_id: str) -> dict[str, Any]:
        """Read a single email.  Returns structured fields."""
        raw = self._call_tool("mail_get", {
            "message_id": message_id,
            "immutable_ids": True,
        })
        _check_error(raw)
        parsed = _parse_mail_get(raw)
        parsed["id"] = message_id
        return parsed

    def folder_list(self, parent_id: str = "") -> list[dict[str, Any]]:
        """List mail folders.  Returns parsed JSON list.

        Raises ``DF_GRAPH_TOO_OLD`` if the server lacks ``folder_list``.
        Unlike ``mail_list``/``calendar_list``, ``folder_list`` returns
        bare JSON (no summary line) — we parse it with ``json.loads``
        directly instead of ``_parse_summary_json``.
        """
        if not self._has_folder_list:
            raise GraphClientError(
                "DF_GRAPH_TOO_OLD",
                "folder_list tool not available; update df-graph plugin",
            )
        args: dict[str, Any] = {"include_hidden": False}
        if parent_id:
            args["parent_id"] = parent_id
        raw = self._call_tool("folder_list", args)
        _check_error(raw)
        try:
            items = json.loads(raw)
        except json.JSONDecodeError:
            raise GraphClientError("GRAPH_FAILED", "Unexpected folder_list format")
        if not isinstance(items, list):
            raise GraphClientError("GRAPH_FAILED", "Unexpected folder_list format")
        return items

    def calendar_list(self, *, days_ahead: int = 3, days_back: int = 1) -> list[dict[str, Any]]:
        """List calendar events.  Returns parsed JSON items."""
        raw = self._call_tool("calendar_list", {
            "days_ahead": days_ahead,
            "days_back": days_back,
        })
        _check_error(raw)
        summary, items = _parse_summary_json(raw)
        _check_truncation(summary)
        if not isinstance(items, list):
            raise GraphClientError("GRAPH_FAILED", "Unexpected calendar_list format")
        return items


# ---------------------------------------------------------------------------
# Context manager factory
# ---------------------------------------------------------------------------

@contextmanager
def open_graph(
    *,
    claude_json_path: Path | None = None,
    server_config: dict[str, Any] | None = None,
) -> Generator[GraphSource, None, None]:
    """Open a GraphSource session.

    Parameters
    ----------
    claude_json_path:
        Path to ``~/.claude.json`` (default: auto-detect).
    server_config:
        Override the server config dict (for testing — pass the fake server
        command directly).
    """
    params = resolve_server_params(claude_json_path, server_config)
    source = GraphSource()
    try:
        source._start(params)
        yield source
    finally:
        source._shutdown()
