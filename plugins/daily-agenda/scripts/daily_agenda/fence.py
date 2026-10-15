"""Untrusted-content fence helper.

Wraps raw third-party text in per-output random-nonce fences so that
Claude's context clearly separates data from instructions.

Fence format::

    <<<UNTRUSTED-<nonce>
    <text with internal fence markers neutralized>
    UNTRUSTED-<nonce>>>>

The nonce is 16 hex characters, unique per call.
"""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any

# Registry of CLI subcommands that emit raw third-party content.
# Kept in sync with hooks/raw-commands.json — test_fence.py enforces.
RAW_EMITTING_COMMANDS: frozenset[str] = frozenset({
    "chats",
    "outlook-preview",
})

# F6: match any case of UNTRUSTED- token, any run of 3+ angle brackets,
# and fullwidth variants.
_UNTRUSTED_TOKEN_RE = re.compile(r"untrusted-", re.IGNORECASE)
_LT_RUN_RE = re.compile(r"[<＜]{3,}")
_GT_RUN_RE = re.compile(r"[>＞]{3,}")


def _generate_nonce() -> str:
    """Return a 16-hex-char random nonce."""
    return secrets.token_hex(8)


def _neutralize(text: str) -> str:
    """Remove any fence-like markers from *text* so nested fences are impossible.

    Handles:
    - Any case of ``UNTRUSTED-`` / ``Untrusted-`` / ``untrusted-``
    - Any run of 3+ ``<`` or ``>`` (ASCII or fullwidth)
    Replacement strings do not resemble fence markers.
    """
    result = _UNTRUSTED_TOKEN_RE.sub("[fence-marker-removed]", text)
    result = _LT_RUN_RE.sub("[lt-run]", result)
    result = _GT_RUN_RE.sub("[gt-run]", result)
    return result


def fence_untrusted(text: str) -> str:
    """Wrap *text* in an untrusted fence with a fresh random nonce.

    Any fence-like markers already in *text* are neutralized first.
    """
    nonce = _generate_nonce()
    safe_text = _neutralize(text)
    return f"<<<UNTRUSTED-{nonce}\n{safe_text}\nUNTRUSTED-{nonce}>>>>"


def load_registry(plugin_root: Path | None = None) -> frozenset[str]:
    """Load the raw-commands registry from the JSON file.

    Falls back to the hardcoded ``RAW_EMITTING_COMMANDS`` constant
    if the file cannot be read.
    """
    if plugin_root is None:
        # Derive from package location
        plugin_root = Path(__file__).resolve().parents[2]
    registry_path = plugin_root / "hooks" / "raw-commands.json"
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        return frozenset(data.get("commands", []))
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return RAW_EMITTING_COMMANDS
