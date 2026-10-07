"""Tests for graph_client.py — output parsing and error handling.

The MCP session lifecycle tests use a minimal fake FastMCP server.
"""

from __future__ import annotations

import json
import secrets
from typing import Any

import pytest

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
