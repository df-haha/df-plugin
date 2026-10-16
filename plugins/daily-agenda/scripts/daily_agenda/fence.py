"""Untrusted-content fence helper.

Wraps raw third-party text in per-output random-nonce fences so that
Claude's context clearly separates data from instructions.

Fence format (model-derived, ``raw=False``)::

    <<<UNTRUSTED-<nonce>
    <text with internal fence markers neutralized>
    UNTRUSTED-<nonce>>>>

Raw format (``raw=True``) — adds a fixed sentinel that the PostToolUse
hook detects to taint the session::

    <<<UNTRUSTED-<nonce> DA-RAW-3f9a
    DA-RAW-3f9a <text with markers + sentinel neutralized>
    DA-RAW-3f9a UNTRUSTED-<nonce>>>>

The sentinel appears on every fence line so that truncated output
(``head``/``tail``/partial read) still carries it.

The nonce is 16 hex characters, unique per call.
"""

from __future__ import annotations

import json
import re
import secrets
from pathlib import Path
from typing import Any

# ---------------------------------------------------------------------------
# Raw sentinel — fixed ASCII token detected by the PostToolUse hook.
# Kept in sync with hooks/raw-commands.json ("raw_sentinel" key).
# test_fence.py::test_registry_consistency enforces.
# ---------------------------------------------------------------------------

RAW_SENTINEL: str = "DA-RAW-3f9a"

# Registry of CLI subcommands that emit raw third-party content.
# Kept in sync with hooks/raw-commands.json — test_fence.py enforces.
RAW_EMITTING_COMMANDS: frozenset[str] = frozenset({
    "chats",
    "outlook-preview",
    "raw",
    "scan",
})

# Subcommand → allowed --file draft basenames.
# Single source of truth: raw-commands.json::draft_policy.
# test_fence.py::test_draft_policy_matches_json enforces sync.
DRAFT_POLICY: dict[str, frozenset[str]] = {
    "draft show": frozenset({"criteria-draft.md", "profile-draft.md", "coverage-draft.json"}),
    "criteria save": frozenset({"criteria-draft.md"}),
    "refine apply": frozenset({"criteria-draft.md"}),
    "profile save": frozenset({"profile-draft.md"}),
    "profile coverage save": frozenset({"coverage-draft.json"}),
}


def load_draft_policy(plugin_root: Path | None = None) -> dict[str, frozenset[str]]:
    """Load draft_policy from raw-commands.json (single source of truth)."""
    if plugin_root is None:
        plugin_root = Path(__file__).resolve().parents[2]
    registry_path = plugin_root / "hooks" / "raw-commands.json"
    try:
        data = json.loads(registry_path.read_text(encoding="utf-8"))
        raw = data.get("draft_policy", {})
        return {k: frozenset(v) for k, v in raw.items()}
    except (FileNotFoundError, json.JSONDecodeError, KeyError):
        return DRAFT_POLICY

# F6: match any case of UNTRUSTED- token, any run of 3+ angle brackets,
# and fullwidth variants.
_UNTRUSTED_TOKEN_RE = re.compile(r"untrusted-", re.IGNORECASE)
_LT_RUN_RE = re.compile(r"[<＜]{3,}")
_GT_RUN_RE = re.compile(r"[>＞]{3,}")
_SENTINEL_RE = re.compile(re.escape(RAW_SENTINEL))


def _generate_nonce() -> str:
    """Return a 16-hex-char random nonce."""
    return secrets.token_hex(8)


def _neutralize(text: str) -> str:
    """Remove any fence-like markers AND the raw sentinel from *text*.

    Handles:
    - Any case of ``UNTRUSTED-`` / ``Untrusted-`` / ``untrusted-``
    - Any run of 3+ ``<`` or ``>`` (ASCII or fullwidth)
    - The raw sentinel token (so attacker-supplied text can't forge it)
    Replacement strings do not resemble fence markers or the sentinel.
    """
    result = _UNTRUSTED_TOKEN_RE.sub("[fence-marker-removed]", text)
    result = _LT_RUN_RE.sub("[lt-run]", result)
    result = _GT_RUN_RE.sub("[gt-run]", result)
    result = _SENTINEL_RE.sub("[raw-marker-removed]", result)
    return result


def fence_untrusted(text: str, *, raw: bool = False) -> str:
    """Wrap *text* in an untrusted fence with a fresh random nonce.

    Any fence-like markers already in *text* are neutralized first
    (including the raw sentinel itself, so attacker text can't collide).

    When *raw* is ``True``, the fixed sentinel ``DA-RAW-3f9a`` is stamped
    on every fence line.  The PostToolUse hook detects this sentinel in
    tool output and marks the session as tainted.

    When *raw* is ``False`` (the default), no sentinel is emitted.  Use
    ``raw=False`` for model-derived text (criteria, profile, draft_text,
    ledger titles, refine changes) which is fenced but non-tainting.
    """
    nonce = _generate_nonce()
    safe_text = _neutralize(text)
    if raw:
        s = RAW_SENTINEL
        return (
            f"<<<UNTRUSTED-{nonce} {s}\n"
            f"{s} {safe_text}\n"
            f"{s} UNTRUSTED-{nonce}>>>>"
        )
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
