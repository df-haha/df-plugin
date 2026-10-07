"""LineSource — subprocess bridge to line_collect.mjs.

Runs ``node line_collect.mjs --vendor <runtime>/line-desktop-mcp`` with a JSON
request on stdin and reads a JSON response from stdout.  Environment variables
for the LINE reader are injected from config / runtime paths, and
``LINE_MCP_EXTENSIONS`` is explicitly removed from the child process env.
"""

from __future__ import annotations

import json
import os
import subprocess
from pathlib import Path
from typing import Any


_DEFAULT_TIMEOUT_S = 120


class LineSourceError(Exception):
    """A LINE read operation failed with a machine-readable code."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


class LineSource:
    """Communicate with the vendored LINE local reader via ``line_collect.mjs``.

    Parameters
    ----------
    plugin_dir:
        Directory containing ``scripts/line_collect.mjs``.
    runtime_dir:
        User's runtime directory (contains ``line-desktop-mcp/``).
    python_path:
        Absolute path to the Python interpreter for the reader subprocess.
    sqlite3mc_dll:
        Absolute path to the SQLite3MultipleCiphers DLL.
    main_database:
        Filename of the selected LINE main database (e.g. ``qw0b9...729.edb``).
    trusted_builds_path:
        Absolute path to the trusted builds JSON file.
    node:
        Node executable (default ``"node"``).
    timeout_s:
        Timeout in seconds for each subprocess call.
    """

    def __init__(
        self,
        *,
        plugin_dir: Path,
        runtime_dir: Path,
        python_path: str,
        sqlite3mc_dll: str,
        main_database: str,
        trusted_builds_path: str,
        node: str = "node",
        timeout_s: int = _DEFAULT_TIMEOUT_S,
    ) -> None:
        self._script = Path(plugin_dir) / "scripts" / "line_collect.mjs"
        self._vendor = Path(runtime_dir) / "line-desktop-mcp"
        self._env_overrides = {
            "LINE_MCP_PYTHON": str(python_path),
            "LINE_MCP_SQLITE3MC_DLL": str(sqlite3mc_dll),
            "LINE_MCP_MAIN_DATABASE": str(main_database),
            "LINE_MCP_TRUSTED_BUILDS": str(trusted_builds_path),
            "PYTHONIOENCODING": "utf-8",
        }
        self._node = node
        self._timeout = timeout_s

    def _run(self, request: dict[str, Any]) -> dict[str, Any]:
        """Execute line_collect.mjs with *request* and return parsed JSON."""
        env = {k: v for k, v in os.environ.items()}
        env.pop("LINE_MCP_EXTENSIONS", None)
        env.update(self._env_overrides)

        try:
            proc = subprocess.run(
                [self._node, str(self._script), "--vendor", str(self._vendor)],
                input=json.dumps(request).encode("utf-8"),
                capture_output=True,
                timeout=self._timeout,
                env=env,
            )
        except FileNotFoundError:
            raise LineSourceError("NODE_MISSING")
        except subprocess.TimeoutExpired:
            raise LineSourceError("LINE_TIMEOUT")

        stdout = proc.stdout.decode("utf-8", errors="replace")

        try:
            result = json.loads(stdout)
        except (json.JSONDecodeError, ValueError):
            raise LineSourceError("LOCAL_READER_FAILED")

        if not result.get("ok"):
            raise LineSourceError(result.get("code", "LOCAL_READER_FAILED"))

        return result

    def probe(self) -> dict[str, Any]:
        """Minimal read — returns ``{"ok": bool, "code": str|None, "ms": int}``."""
        try:
            return self._run({"op": "probe"})
        except LineSourceError as exc:
            return {"ok": False, "code": exc.code, "ms": -1}

    def recent_chats(self, *, days: int, offset: int = 0, limit: int = 50) -> dict[str, Any]:
        """List recent chats.  Returns upstream-shaped result with ``chats`` and ``hasMore``."""
        return self._run({
            "op": "recentChats",
            "days": days,
            "offset": offset,
            "limit": limit,
        })

    def messages(
        self,
        *,
        chat_name: str,
        chat_ref: str,
        chat_type: str = "auto",
        date_from: str,
        date_to: str,
        cursor: str | None = None,
        message_limit: int = 200,
    ) -> dict[str, Any]:
        """Read messages for a specific chat."""
        req: dict[str, Any] = {
            "op": "messages",
            "chatName": chat_name,
            "chatType": chat_type,
            "dateFrom": date_from,
            "dateTo": date_to,
            "messageLimit": message_limit,
            "expectedChatRef": chat_ref,
        }
        if cursor is not None:
            req["cursor"] = cursor
        return self._run(req)
