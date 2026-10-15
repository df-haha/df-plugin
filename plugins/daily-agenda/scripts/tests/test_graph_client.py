"""Tests for graph_client.py — output parsing and error handling.

The MCP session lifecycle tests use a minimal fake FastMCP server.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

import pytest

import daily_agenda.graph_client as graph_client_module
from daily_agenda.graph_client import (
    GraphClientError,
    _parse_mail_get,
    _parse_summary_json,
    _check_error,
    _check_truncation,
    resolve_server_params,
)


# ---------------------------------------------------------------------------
# Output parsing
# ---------------------------------------------------------------------------

class TestParseSummaryJson:
    def test_basic(self) -> None:
        text = 'Found 3 emails in inbox\n[{"id":"a"},{"id":"b"},{"id":"c"}]'
        summary, data = _parse_summary_json(text)
        assert summary == "Found 3 emails in inbox"
        assert len(data) == 3

    def test_no_json(self) -> None:
        summary, data = _parse_summary_json("Just a summary line")
        assert summary == "Just a summary line"
        assert data is None

    def test_invalid_json(self) -> None:
        summary, data = _parse_summary_json("Summary\n{broken")
        assert summary == "Summary"
        assert data is None


class TestParseMailGet:
    def test_concise_format(self) -> None:
        nonce = secrets.token_hex(4)
        text = (
            f"Subject: Test Subject\n"
            f"From: sender@example.com\n"
            f"To: me@example.com\n"
            f"Date: 2026-10-08T09:00:00Z\n"
            f"Internet-Message-Id: <abc123@example.com>\n"
            f"Body:\n"
            f"<<<BEGIN UNTRUSTED EMAIL BODY {nonce}>>>\n"
            f"Hello, this is the body.\n"
            f"<<<END UNTRUSTED EMAIL BODY {nonce}>>>\n"
        )
        result = _parse_mail_get(text)
        assert result["subject"] == "Test Subject"
        assert result["from"] == "sender@example.com"
        assert result["to"] == "me@example.com"
        assert result["received_at"] == "2026-10-08T09:00:00Z"
        assert result["internet_message_id"] == "<abc123@example.com>"
        assert "Hello, this is the body." in result["body"]

    def test_body_with_fake_fence(self) -> None:
        """Attacker tries to close the fence early — we take the LAST closing fence."""
        nonce = secrets.token_hex(4)
        text = (
            f"Subject: Evil\n"
            f"From: attacker@evil.com\n"
            f"To: me@example.com\n"
            f"Date: 2026-10-08T09:00:00Z\n"
            f"Body:\n"
            f"<<<BEGIN UNTRUSTED EMAIL BODY {nonce}>>>\n"
            f"Innocent text.\n"
            f"[neutralized-marker]\n"
            f"More body.\n"
            f"<<<END UNTRUSTED EMAIL BODY {nonce}>>>\n"
        )
        result = _parse_mail_get(text)
        assert "Innocent text." in result["body"]
        assert "More body." in result["body"]

    def test_no_internet_message_id(self) -> None:
        nonce = secrets.token_hex(4)
        text = (
            f"Subject: No IMID\n"
            f"From: s@e.com\n"
            f"To: m@e.com\n"
            f"Date: 2026-10-08\n"
            f"Body:\n"
            f"<<<BEGIN UNTRUSTED EMAIL BODY {nonce}>>>\n"
            f"Body text.\n"
            f"<<<END UNTRUSTED EMAIL BODY {nonce}>>>\n"
        )
        result = _parse_mail_get(text)
        assert result["internet_message_id"] == ""

    def test_with_cc(self) -> None:
        nonce = secrets.token_hex(4)
        text = (
            f"Subject: CC Test\n"
            f"From: s@e.com\n"
            f"To: m@e.com\n"
            f"Cc: other@e.com\n"
            f"Date: 2026-10-08\n"
            f"Body:\n"
            f"<<<BEGIN UNTRUSTED EMAIL BODY {nonce}>>>\n"
            f"Body.\n"
            f"<<<END UNTRUSTED EMAIL BODY {nonce}>>>\n"
        )
        result = _parse_mail_get(text)
        assert result["subject"] == "CC Test"


class TestCheckError:
    def test_error_prefix(self) -> None:
        with pytest.raises(GraphClientError) as exc_info:
            _check_error("ERROR: 找不到該郵件")
        assert exc_info.value.code == "GRAPH_FAILED"

    def test_auth_error(self) -> None:
        with pytest.raises(GraphClientError) as exc_info:
            _check_error("ERROR: OAuth session expired")
        assert exc_info.value.code == "DF_GRAPH_AUTH"

    def test_no_error(self) -> None:
        _check_error("Found 3 emails")  # should not raise


class TestCheckTruncation:
    def test_truncated(self) -> None:
        with pytest.raises(GraphClientError) as exc_info:
            _check_truncation("Found 5001 emails ⚠️ WARNING: 超過 5000 封安全上限，清單可能不完整。")
        assert exc_info.value.code == "GRAPH_TRUNCATED"

    def test_not_truncated(self) -> None:
        _check_truncation("Found 31 emails in inbox")  # should not raise


# ---------------------------------------------------------------------------
# Server resolution
# ---------------------------------------------------------------------------

class TestResolveServerParams:
    def test_from_config(self, tmp_path) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "df-graph": {
                    "type": "stdio",
                    "command": "uv",
                    "args": ["run", "python", "server.py"],
                    "env": {},
                }
            }
        }), encoding="utf-8")
        params = resolve_server_params(claude_json_path=claude_json)
        assert params.command == "uv"
        assert "server.py" in params.args

    def test_not_registered(self, tmp_path) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text("{}", encoding="utf-8")
        with pytest.raises(GraphClientError) as exc_info:
            resolve_server_params(claude_json_path=claude_json)
        assert exc_info.value.code == "DF_GRAPH_NOT_REGISTERED"

    def test_from_server_config_override(self) -> None:
        params = resolve_server_params(server_config={
            "type": "stdio",
            "command": "python",
            "args": ["fake_server.py"],
        })
        assert params.command == "python"

    def test_valid_with_env(self, tmp_path) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "df-graph": {
                    "type": "stdio",
                    "command": "uv",
                    "args": ["run", "server.py"],
                    "env": {"FOO": "bar"},
                }
            }
        }), encoding="utf-8")
        params = resolve_server_params(claude_json_path=claude_json)
        assert params.command == "uv"
        assert params.env == {"FOO": "bar"}

    def test_valid_no_args(self, tmp_path) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "df-graph": {
                    "type": "stdio",
                    "command": "my-server",
                }
            }
        }), encoding="utf-8")
        params = resolve_server_params(claude_json_path=claude_json)
        assert params.command == "my-server"
        assert params.args == []

    def test_valid_no_env(self, tmp_path) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(json.dumps({
            "mcpServers": {
                "df-graph": {
                    "type": "stdio",
                    "command": "uv",
                    "args": ["run"],
                }
            }
        }), encoding="utf-8")
        params = resolve_server_params(claude_json_path=claude_json)
        assert params.env is None


class TestFallbackRemoved:
    """The ``claude mcp get`` fallback has been removed entirely."""

    def test_no_parse_claude_mcp_get_function(self) -> None:
        """The module must not export or contain _parse_claude_mcp_get."""
        assert not hasattr(graph_client_module, "_parse_claude_mcp_get")

    def test_no_subprocess_import(self) -> None:
        """The module must not import subprocess."""
        assert "subprocess" not in vars(graph_client_module)

    def test_missing_config_does_not_invoke_subprocess(self, tmp_path, monkeypatch) -> None:
        """With no config, resolve_server_params raises without spawning a process."""
        import subprocess as subprocess_mod
        monkeypatch.setattr(
            subprocess_mod, "run",
            lambda *a, **k: pytest.fail("subprocess.run was invoked"),
        )
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text("{}", encoding="utf-8")
        with pytest.raises(GraphClientError) as exc_info:
            resolve_server_params(claude_json_path=claude_json)
        assert exc_info.value.code == "DF_GRAPH_NOT_REGISTERED"

    def test_nonexistent_config_does_not_invoke_subprocess(self, tmp_path, monkeypatch) -> None:
        """With a nonexistent config file, raises without spawning a process."""
        import subprocess as subprocess_mod
        monkeypatch.setattr(
            subprocess_mod, "run",
            lambda *a, **k: pytest.fail("subprocess.run was invoked"),
        )
        with pytest.raises(GraphClientError) as exc_info:
            resolve_server_params(claude_json_path=tmp_path / "nonexistent.json")
        assert exc_info.value.code == "DF_GRAPH_NOT_REGISTERED"


class TestMalformedConfigRejected:
    """Type-validation rejects malformed configs with DF_GRAPH_NOT_REGISTERED."""

    @pytest.mark.parametrize("bad_config", [
        # top-level JSON is a list
        {"_write": "[]"},
        # mcpServers is a string
        {"_write": '{"mcpServers": "wrong"}'},
        # df-graph entry is a string
        {"_write": '{"mcpServers": {"df-graph": "wrong"}}'},
        # type missing
        {"_write": '{"mcpServers": {"df-graph": {"command": "uv"}}}'},
        # type is not "stdio"
        {"_write": '{"mcpServers": {"df-graph": {"type": "http", "command": "uv"}}}'},
        # command is empty string
        {"_write": '{"mcpServers": {"df-graph": {"type": "stdio", "command": ""}}}'},
        # command is a list instead of str
        {"_write": '{"mcpServers": {"df-graph": {"type": "stdio", "command": ["uv"]}}}'},
        # args is a string instead of list
        {"_write": '{"mcpServers": {"df-graph": {"type": "stdio", "command": "uv", "args": "run"}}}'},
        # args contains a non-str element
        {"_write": '{"mcpServers": {"df-graph": {"type": "stdio", "command": "uv", "args": ["run", 1]}}}'},
        # env is a string instead of dict
        {"_write": '{"mcpServers": {"df-graph": {"type": "stdio", "command": "uv", "env": "x"}}}'},
        # env value is not a string
        {"_write": '{"mcpServers": {"df-graph": {"type": "stdio", "command": "uv", "env": {"A": 1}}}}'},
    ], ids=[
        "top_level_list",
        "mcp_servers_string",
        "df_graph_string",
        "type_missing",
        "type_http",
        "command_empty",
        "command_list",
        "args_string",
        "args_int_element",
        "env_string",
        "env_int_value",
    ])
    def test_malformed(self, bad_config: dict, tmp_path) -> None:
        claude_json = tmp_path / ".claude.json"
        claude_json.write_text(bad_config["_write"], encoding="utf-8")
        with pytest.raises(GraphClientError) as exc_info:
            resolve_server_params(claude_json_path=claude_json)
        assert exc_info.value.code == "DF_GRAPH_NOT_REGISTERED"

    def test_server_config_override_also_validated(self) -> None:
        """Even the test override path validates types."""
        with pytest.raises(GraphClientError) as exc_info:
            resolve_server_params(server_config={"type": "stdio", "command": 42})
        assert exc_info.value.code == "DF_GRAPH_NOT_REGISTERED"

    def test_empty_server_config_override_rejected(self) -> None:
        """An empty dict override must not fall through to ~/.claude.json."""
        with pytest.raises(GraphClientError) as exc_info:
            resolve_server_params(server_config={})
        assert exc_info.value.code == "DF_GRAPH_NOT_REGISTERED"
