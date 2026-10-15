"""Model output validation (§3) and criteria draft validation."""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from datetime import date


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ModelOutputInvalid(Exception):
    """Entire model output rejected (structural failure)."""

    code: str = "MODEL_OUTPUT_INVALID"


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_VALID_ACTIONS: frozenset[str] = frozenset({"new", "update", "maybe_done", "skip"})
_VALID_PRIORITIES: frozenset[str] = frozenset({"P0", "P1", "P2"})

_MAX_TITLE: int = 120
_MAX_REASON: int = 200
_MAX_CRITERIA_LEN: int = 20_000

# Allowed fields per action — anything else → reject item.
_ACTION_FIELDS: dict[str, frozenset[str]] = {
    "new": frozenset({"action", "title", "priority", "due", "src_ids", "reason"}),
    "update": frozenset({"action", "todo_id", "priority", "due", "src_ids", "reason"}),
    "maybe_done": frozenset({"action", "todo_id", "src_ids", "reason"}),
    "skip": frozenset({"action", "src_ids", "reason"}),
}


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class ValidationResult:
    items: list[dict] = field(default_factory=list)
    rejected: list[dict] = field(default_factory=list)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _is_valid_date(value: str) -> bool:
    """Return True if *value* is a strict YYYY-MM-DD date string."""
    try:
        date.fromisoformat(value)
        return True
    except (ValueError, TypeError):
        return False


def _reject(index: int, code: str) -> dict:
    return {"index": index, "code": code}


# ---------------------------------------------------------------------------
# Public API
# ---------------------------------------------------------------------------

def validate(
    model: dict,
    bundle_src_ids: set[str],
    open_todo_ids: set[str],
) -> ValidationResult:
    """Validate model output against §3 contract.

    Raises :class:`ModelOutputInvalid` when the top-level structure is invalid.
    Per-item failures are collected in ``result.rejected``.
    """
    if not isinstance(model, dict):
        raise ModelOutputInvalid("top-level value is not an object")

    if model.get("schema_version") != 1:
        raise ModelOutputInvalid("missing or unsupported schema_version")

    items_raw = model.get("items")
    if not isinstance(items_raw, list):
        raise ModelOutputInvalid("missing or invalid 'items'")

    # Any extra top-level key → reject entire output.
    allowed_top = {"schema_version", "items"}
    extra_top = set(model.keys()) - allowed_top
    if extra_top:
        raise ModelOutputInvalid(f"unknown top-level fields: {extra_top}")

    result = ValidationResult()

    for idx, item in enumerate(items_raw):
        if not isinstance(item, dict):
            result.rejected.append(_reject(idx, "SCHEMA_INVALID"))
            continue

        action = item.get("action")
        if action not in _VALID_ACTIONS:
            result.rejected.append(_reject(idx, "SCHEMA_INVALID"))
            continue

        # Unknown fields → reject.
        allowed = _ACTION_FIELDS[action]
        if set(item.keys()) - allowed:
            result.rejected.append(_reject(idx, "SCHEMA_INVALID"))
            continue

        # --- src_ids ---
        src_ids = item.get("src_ids")
        if action == "update":
            # update may have empty src_ids list
            if src_ids is None:
                result.rejected.append(_reject(idx, "SCHEMA_INVALID"))
                continue
            if not isinstance(src_ids, list):
                result.rejected.append(_reject(idx, "SCHEMA_INVALID"))
                continue
            # If non-empty, every src_id must exist.
            if src_ids:
                if any(not isinstance(s, str) for s in src_ids):
                    result.rejected.append(_reject(idx, "SCHEMA_INVALID"))
                    continue
                if any(s not in bundle_src_ids for s in src_ids):
                    result.rejected.append(_reject(idx, "UNKNOWN_SRC_ID"))
                    continue
        else:
            # new / maybe_done / skip: at least 1 src_id required
            if not isinstance(src_ids, list) or len(src_ids) == 0:
                result.rejected.append(_reject(idx, "EMPTY_SRC_IDS"))
                continue
            if any(not isinstance(s, str) for s in src_ids):
                result.rejected.append(_reject(idx, "SCHEMA_INVALID"))
                continue
            if any(s not in bundle_src_ids for s in src_ids):
                result.rejected.append(_reject(idx, "UNKNOWN_SRC_ID"))
                continue

        # --- todo_id (update / maybe_done) ---
        if action in ("update", "maybe_done"):
            todo_id = item.get("todo_id")
            if not isinstance(todo_id, str) or todo_id not in open_todo_ids:
                result.rejected.append(_reject(idx, "UNKNOWN_TODO_ID"))
                continue

        # --- title (new only) ---
        if action == "new":
            title = item.get("title")
            if not isinstance(title, str) or not title:
                result.rejected.append(_reject(idx, "SCHEMA_INVALID"))
                continue
            if len(title) > _MAX_TITLE:
                result.rejected.append(_reject(idx, "FIELD_TOO_LONG"))
                continue

        # --- priority (new / update) ---
        if action in ("new", "update"):
            priority = item.get("priority")
            if not isinstance(priority, str) or priority not in _VALID_PRIORITIES:
                result.rejected.append(_reject(idx, "SCHEMA_INVALID"))
                continue

        # --- due (new / update) ---
        if action in ("new", "update"):
            due = item.get("due")
            if due is not None:
                if not isinstance(due, str) or not _is_valid_date(due):
                    result.rejected.append(_reject(idx, "SCHEMA_INVALID"))
                    continue

        # --- reason ---
        reason = item.get("reason")
        if reason is not None:
            if not isinstance(reason, str):
                result.rejected.append(_reject(idx, "SCHEMA_INVALID"))
                continue
            if len(reason) > _MAX_REASON:
                result.rejected.append(_reject(idx, "FIELD_TOO_LONG"))
                continue

        result.items.append(item)

    return result


def validate_criteria_draft(obj: dict) -> str:
    """Validate setup step-8 criteria output.

    Returns the criteria markdown on success; raises :class:`ModelOutputInvalid`
    on structural or content failure.
    """
    if not isinstance(obj, dict):
        raise ModelOutputInvalid("criteria output is not an object")

    if obj.get("schema_version") != 1:
        raise ModelOutputInvalid("missing or unsupported schema_version")

    allowed_keys = {"schema_version", "criteria_markdown"}
    if set(obj.keys()) - allowed_keys:
        raise ModelOutputInvalid("unknown fields in criteria output")

    text = obj.get("criteria_markdown")
    if not isinstance(text, str) or not text.strip():
        raise ModelOutputInvalid("criteria_markdown is empty or missing")

    if len(text) > _MAX_CRITERIA_LEN:
        raise ModelOutputInvalid(
            f"criteria_markdown exceeds {_MAX_CRITERIA_LEN} characters"
        )

    return text


# ---------------------------------------------------------------------------
# Observations (criteria map-reduce, batch analysis)
# ---------------------------------------------------------------------------

_MAX_OBSERVATIONS: int = 40
_MAX_OBS_CATEGORY: int = 50
_MAX_OBS_PATTERN: int = 200
_VALID_FREQUENCIES: frozenset[str] = frozenset({"high", "medium", "low"})
_VALID_OBS_PRIORITIES: frozenset[str] = frozenset({"P0", "P1", "P2"})
_ALLOWED_OBS_KEYS: frozenset[str] = frozenset({"category", "pattern", "frequency", "priority_signal"})


def validate_observations(obj: dict) -> list[dict]:
    """Validate criteria-observe model output.

    Returns the list of observation dicts on success; raises
    :class:`ModelOutputInvalid` on structural or content failure.
    """
    if not isinstance(obj, dict):
        raise ModelOutputInvalid("observations output is not an object")

    if obj.get("schema_version") != 1:
        raise ModelOutputInvalid("missing or unsupported schema_version")

    allowed_top = {"schema_version", "observations"}
    if set(obj.keys()) - allowed_top:
        raise ModelOutputInvalid("unknown fields in observations output")

    obs_list = obj.get("observations")
    if not isinstance(obs_list, list):
        raise ModelOutputInvalid("'observations' must be a list")

    if len(obs_list) > _MAX_OBSERVATIONS:
        raise ModelOutputInvalid(
            f"too many observations ({len(obs_list)} > {_MAX_OBSERVATIONS})"
        )

    result: list[dict] = []
    for i, item in enumerate(obs_list):
        if not isinstance(item, dict):
            raise ModelOutputInvalid(f"observations[{i}] is not an object")
        if set(item.keys()) != _ALLOWED_OBS_KEYS:
            raise ModelOutputInvalid(f"observations[{i}] has invalid keys")

        cat = item.get("category")
        if not isinstance(cat, str) or not cat.strip():
            raise ModelOutputInvalid(f"observations[{i}].category is empty or missing")
        if len(cat) > _MAX_OBS_CATEGORY:
            raise ModelOutputInvalid(f"observations[{i}].category too long")

        pattern = item.get("pattern")
        if not isinstance(pattern, str) or not pattern.strip():
            raise ModelOutputInvalid(f"observations[{i}].pattern is empty or missing")
        if len(pattern) > _MAX_OBS_PATTERN:
            raise ModelOutputInvalid(f"observations[{i}].pattern too long")

        freq = item.get("frequency")
        if freq not in _VALID_FREQUENCIES:
            raise ModelOutputInvalid(f"observations[{i}].frequency invalid")

        prio = item.get("priority_signal")
        if prio not in _VALID_OBS_PRIORITIES:
            raise ModelOutputInvalid(f"observations[{i}].priority_signal invalid")

        result.append({
            "category": cat,
            "pattern": pattern,
            "frequency": freq,
            "priority_signal": prio,
        })

    return result


_MAX_REFINE_CHANGES: int = 10
_MAX_CHANGE_SUMMARY: int = 200
_MAX_CHANGE_REASON: int = 200


def validate_refine_draft(obj: dict) -> tuple[str, list[dict]]:
    """Validate refine-draft model output.

    Returns ``(criteria_markdown, changes)`` on success; raises
    :class:`ModelOutputInvalid` on structural or content failure.
    """
    if not isinstance(obj, dict):
        raise ModelOutputInvalid("refine output is not an object")

    if obj.get("schema_version") != 1:
        raise ModelOutputInvalid("missing or unsupported schema_version")

    allowed_keys = {"schema_version", "criteria_markdown", "changes"}
    if set(obj.keys()) - allowed_keys:
        raise ModelOutputInvalid("unknown fields in refine output")

    # criteria_markdown
    text = obj.get("criteria_markdown")
    if not isinstance(text, str) or not text.strip():
        raise ModelOutputInvalid("criteria_markdown is empty or missing")

    if len(text) > _MAX_CRITERIA_LEN:
        raise ModelOutputInvalid(
            f"criteria_markdown exceeds {_MAX_CRITERIA_LEN} characters"
        )

    # changes (required)
    changes_raw = obj.get("changes")
    if changes_raw is None:
        raise ModelOutputInvalid("missing 'changes' field")
    if not isinstance(changes_raw, list):
        raise ModelOutputInvalid("'changes' must be a list")
    if len(changes_raw) > _MAX_REFINE_CHANGES:
        raise ModelOutputInvalid(
            f"too many changes ({len(changes_raw)} > {_MAX_REFINE_CHANGES})"
        )

    changes: list[dict] = []
    allowed_change_keys = {"summary", "reason"}
    for i, ch in enumerate(changes_raw):
        if not isinstance(ch, dict):
            raise ModelOutputInvalid(f"changes[{i}] is not an object")
        if set(ch.keys()) != allowed_change_keys:
            raise ModelOutputInvalid(f"changes[{i}] has invalid keys")

        summary = ch.get("summary")
        if not isinstance(summary, str) or not summary.strip():
            raise ModelOutputInvalid(f"changes[{i}].summary is empty or missing")
        if len(summary) > _MAX_CHANGE_SUMMARY:
            raise ModelOutputInvalid(f"changes[{i}].summary too long")

        reason = ch.get("reason")
        if not isinstance(reason, str) or not reason.strip():
            raise ModelOutputInvalid(f"changes[{i}].reason is empty or missing")
        if len(reason) > _MAX_CHANGE_REASON:
            raise ModelOutputInvalid(f"changes[{i}].reason too long")

        changes.append({"summary": summary, "reason": reason})

    return text, changes
