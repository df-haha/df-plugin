"""Tests for line_source.py — subprocess bridge to line_collect.mjs."""

from __future__ import annotations

import json
import os
import subprocess
import sys
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock, patch

import pytest

from daily_agenda.line_source import LineSource, LineSourceError


def _make_source(
    tmp_path: Path,
    **overrides: Any,
) -> LineSource:
    defaults = dict(
        plugin_dir=tmp_path / "plugin",
        runtime_dir=tmp_path / "runtime",
        python_path="/usr/bin/python3",
        sqlite3mc_dll="C:\\sqlite3mc.dll",
        main_database="test123.edb",
        trusted_builds_path="/tmp/trusted.json",
        node="node",
        timeout_s=10,
    )
    defaults.update(overrides)
    return LineSource(**defaults)


class TestLineSourceRun:
    """Test the _run method via probe/recent_chats/messages."""

    def test_probe_success(self, tmp_path: Path) -> None:
        source = _make_source(tmp_path)
        fake_result = {"ok": True, "code": None, "ms": 50}

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=json.dumps(fake_result).encode("utf-8"),
                returncode=0,
            )
            result = source.probe()

        assert result["ok"] is True
        assert result["ms"] == 50

        # Verify env vars were set correctly
        call_kwargs = mock_run.call_args
        env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env", {})
        assert env.get("LINE_MCP_PYTHON") == "/usr/bin/python3"
        assert env.get("LINE_MCP_MAIN_DATABASE") == "test123.edb"
        assert env.get("PYTHONIOENCODING") == "utf-8"
        assert "LINE_MCP_EXTENSIONS" not in env

    def test_probe_failure_returns_code(self, tmp_path: Path) -> None:
        source = _make_source(tmp_path)
        fake_result = {"ok": False, "code": "SESSION_KEY_UNAVAILABLE", "ms": 100}

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=json.dumps(fake_result).encode("utf-8"),
                returncode=0,
            )
            result = source.probe()

        assert result["ok"] is False
        assert result["code"] == "SESSION_KEY_UNAVAILABLE"

    def test_timeout_returns_error(self, tmp_path: Path) -> None:
        source = _make_source(tmp_path, timeout_s=1)

        with patch("subprocess.run", side_effect=subprocess.TimeoutExpired("node", 1)):
            result = source.probe()

        assert result["ok"] is False
        assert result["code"] == "LINE_TIMEOUT"

    def test_node_missing_returns_error(self, tmp_path: Path) -> None:
        source = _make_source(tmp_path)

        with patch("subprocess.run", side_effect=FileNotFoundError):
            result = source.probe()

        assert result["ok"] is False
        assert result["code"] == "NODE_MISSING"

    def test_recent_chats(self, tmp_path: Path) -> None:
        source = _make_source(tmp_path)
        fake_result = {
            "ok": True,
            "chats": [{"chatRef": "chat:aabb", "chatName": "Test"}],
            "hasMore": False,
        }

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=json.dumps(fake_result).encode("utf-8"),
                returncode=0,
            )
            result = source.recent_chats(days=30, offset=0, limit=50)

        assert result["ok"] is True
        assert len(result["chats"]) == 1

        # Verify the request payload
        call_kwargs = mock_run.call_args
        input_bytes = call_kwargs.kwargs.get("input") or call_kwargs[1].get("input", b"")
        req = json.loads(input_bytes)
        assert req["op"] == "recentChats"
        assert req["days"] == 30

    def test_messages(self, tmp_path: Path) -> None:
        source = _make_source(tmp_path)
        fake_result = {
            "ok": True,
            "chatRef": "chat:aabb",
            "count": 1,
            "messages": [{"sourceRef": "message:cc", "text": "Hi"}],
            "hasMore": False,
        }

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=json.dumps(fake_result).encode("utf-8"),
                returncode=0,
            )
            result = source.messages(
                chat_name="Test",
                chat_ref="chat:aabb",
                date_from="2026-10-08",
                date_to="2026-10-08",
            )

        assert result["ok"] is True
        assert result["count"] == 1

    def test_extensions_stripped_from_env(self, tmp_path: Path) -> None:
        """LINE_MCP_EXTENSIONS must not be in the child env."""
        source = _make_source(tmp_path)
        fake_result = {"ok": True, "code": None, "ms": 1}

        with patch.dict(os.environ, {"LINE_MCP_EXTENSIONS": "1"}):
            with patch("subprocess.run") as mock_run:
                mock_run.return_value = MagicMock(
                    stdout=json.dumps(fake_result).encode("utf-8"),
                    returncode=0,
                )
                source.probe()

        call_kwargs = mock_run.call_args
        env = call_kwargs.kwargs.get("env") or call_kwargs[1].get("env", {})
        assert "LINE_MCP_EXTENSIONS" not in env

    def test_invalid_json_stdout(self, tmp_path: Path) -> None:
        source = _make_source(tmp_path)

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=b"not json at all",
                returncode=0,
            )
            result = source.probe()

        assert result["ok"] is False
        assert result["code"] == "LOCAL_READER_FAILED"

    def test_uses_bytes_mode_for_subprocess(self, tmp_path: Path) -> None:
        """Subprocess must use bytes mode to avoid cp950 encoding issues on Windows."""
        source = _make_source(tmp_path)
        fake_result = {"ok": True, "code": None, "ms": 1}

        with patch("subprocess.run") as mock_run:
            mock_run.return_value = MagicMock(
                stdout=json.dumps(fake_result).encode("utf-8"),
                returncode=0,
            )
            source.probe()

        call_kwargs = mock_run.call_args
        # Should use capture_output=True (bytes mode), NOT text=True
        assert call_kwargs.kwargs.get("capture_output") is True
        assert call_kwargs.kwargs.get("text") is not True
