"""Tests for daily_agenda.validate — model output & criteria draft validation."""

from __future__ import annotations

import copy

import pytest

from daily_agenda.validate import (
    ModelOutputInvalid,
    ValidationResult,
    validate,
    validate_criteria_draft,
)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

BUNDLE_SRC_IDS = {
    "mail:AAA",
    "mail:BBB",
    "line:chat:aabb:message:cc11",
    "event:ev1",
}

OPEN_TODO_IDS = {"T-4821", "T-1000"}


def _good_model(*items: dict) -> dict:
    return {"schema_version": 1, "items": list(items)}


NEW_ITEM = {
    "action": "new",
    "title": "Follow up on email",
    "priority": "P1",
    "due": "2026-10-10",
    "src_ids": ["mail:AAA"],
    "reason": "Important deadline",
}

UPDATE_ITEM = {
    "action": "update",
    "todo_id": "T-4821",
    "priority": "P0",
    "due": None,
    "src_ids": ["mail:BBB"],
    "reason": "Priority raised",
}

MAYBE_DONE_ITEM = {
    "action": "maybe_done",
    "todo_id": "T-4821",
    "src_ids": ["mail:AAA"],
    "reason": "Sender confirmed receipt",
}

SKIP_ITEM = {
    "action": "skip",
    "src_ids": ["mail:AAA"],
    "reason": "Not actionable",
}


# ---------------------------------------------------------------------------
# Top-level structure
# ---------------------------------------------------------------------------

class TestTopLevelRejection:
    def test_not_dict(self) -> None:
        with pytest.raises(ModelOutputInvalid):
            validate("bad", BUNDLE_SRC_IDS, OPEN_TODO_IDS)

    def test_missing_schema_version(self) -> None:
        with pytest.raises(ModelOutputInvalid):
            validate({"items": []}, BUNDLE_SRC_IDS, OPEN_TODO_IDS)

    def test_wrong_schema_version(self) -> None:
        with pytest.raises(ModelOutputInvalid):
            validate({"schema_version": 2, "items": []}, BUNDLE_SRC_IDS, OPEN_TODO_IDS)

    def test_missing_items(self) -> None:
        with pytest.raises(ModelOutputInvalid):
            validate({"schema_version": 1}, BUNDLE_SRC_IDS, OPEN_TODO_IDS)

    def test_items_not_list(self) -> None:
        with pytest.raises(ModelOutputInvalid):
            validate({"schema_version": 1, "items": "bad"}, BUNDLE_SRC_IDS, OPEN_TODO_IDS)

    def test_unknown_top_level_field(self) -> None:
        with pytest.raises(ModelOutputInvalid):
            validate(
                {"schema_version": 1, "items": [], "extra": 1},
                BUNDLE_SRC_IDS,
                OPEN_TODO_IDS,
            )

    def test_empty_items(self) -> None:
        result = validate(_good_model(), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert result.items == []
        assert result.rejected == []


# ---------------------------------------------------------------------------
# Valid items
# ---------------------------------------------------------------------------

class TestValidItems:
    def test_new(self) -> None:
        result = validate(_good_model(NEW_ITEM), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert len(result.items) == 1
        assert result.rejected == []

    def test_update(self) -> None:
        result = validate(_good_model(UPDATE_ITEM), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert len(result.items) == 1

    def test_update_empty_src_ids(self) -> None:
        item = {**UPDATE_ITEM, "src_ids": []}
        result = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert len(result.items) == 1

    def test_maybe_done(self) -> None:
        result = validate(_good_model(MAYBE_DONE_ITEM), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert len(result.items) == 1

    def test_skip(self) -> None:
        result = validate(_good_model(SKIP_ITEM), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert len(result.items) == 1

    def test_due_none(self) -> None:
        item = {**NEW_ITEM, "due": None}
        result = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert len(result.items) == 1

    def test_reason_none(self) -> None:
        item = {**NEW_ITEM, "reason": None}
        result = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert len(result.items) == 1

    def test_multiple(self) -> None:
        result = validate(
            _good_model(NEW_ITEM, UPDATE_ITEM, SKIP_ITEM),
            BUNDLE_SRC_IDS,
            OPEN_TODO_IDS,
        )
        assert len(result.items) == 3
        assert result.rejected == []


# ---------------------------------------------------------------------------
# Per-item rejections
# ---------------------------------------------------------------------------

class TestItemRejections:
    def test_unknown_action(self) -> None:
        item = {**NEW_ITEM, "action": "delete"}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert len(r.rejected) == 1
        assert r.rejected[0]["code"] == "SCHEMA_INVALID"

    def test_unknown_field(self) -> None:
        item = {**NEW_ITEM, "extra_field": "x"}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert len(r.rejected) == 1
        assert r.rejected[0]["code"] == "SCHEMA_INVALID"

    def test_unknown_src_id(self) -> None:
        item = {**NEW_ITEM, "src_ids": ["mail:NONEXIST"]}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert r.rejected[0]["code"] == "UNKNOWN_SRC_ID"

    def test_empty_src_ids_new(self) -> None:
        item = {**NEW_ITEM, "src_ids": []}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert r.rejected[0]["code"] == "EMPTY_SRC_IDS"

    def test_empty_src_ids_skip(self) -> None:
        item = {**SKIP_ITEM, "src_ids": []}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert r.rejected[0]["code"] == "EMPTY_SRC_IDS"

    def test_unknown_todo_id(self) -> None:
        item = {**UPDATE_ITEM, "todo_id": "T-9999"}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert r.rejected[0]["code"] == "UNKNOWN_TODO_ID"

    def test_title_too_long(self) -> None:
        item = {**NEW_ITEM, "title": "x" * 121}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert r.rejected[0]["code"] == "FIELD_TOO_LONG"

    def test_reason_too_long(self) -> None:
        item = {**NEW_ITEM, "reason": "x" * 201}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert r.rejected[0]["code"] == "FIELD_TOO_LONG"

    def test_invalid_due_format(self) -> None:
        item = {**NEW_ITEM, "due": "10/10/2026"}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert r.rejected[0]["code"] == "SCHEMA_INVALID"

    def test_priority_invalid(self) -> None:
        item = {**NEW_ITEM, "priority": "P3"}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert r.rejected[0]["code"] == "SCHEMA_INVALID"

    def test_item_not_dict(self) -> None:
        r = validate(_good_model("bad"), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert r.rejected[0]["code"] == "SCHEMA_INVALID"

    def test_title_empty_string(self) -> None:
        item = {**NEW_ITEM, "title": ""}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert r.rejected[0]["code"] == "SCHEMA_INVALID"

    def test_src_ids_non_string_element(self) -> None:
        item = {**NEW_ITEM, "src_ids": [123]}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert r.rejected[0]["code"] == "SCHEMA_INVALID"

    def test_mixed_valid_and_invalid(self) -> None:
        bad = {**NEW_ITEM, "src_ids": ["mail:NONEXIST"]}
        r = validate(_good_model(NEW_ITEM, bad, SKIP_ITEM), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert len(r.items) == 2
        assert len(r.rejected) == 1
        assert r.rejected[0]["index"] == 1


# ---------------------------------------------------------------------------
# Prompt injection invariant (AS-14) — no action fields possible in output
# ---------------------------------------------------------------------------

class TestPromptInjectionInvariant:
    """validate never produces items with action-like fields beyond the schema."""

    def test_action_field_injection_rejected(self) -> None:
        """An item with a field like 'send_to' is rejected (unknown field)."""
        item = {**NEW_ITEM, "send_to": "someone@example.com"}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert len(r.rejected) == 1
        assert r.rejected[0]["code"] == "SCHEMA_INVALID"

    def test_tool_call_field_rejected(self) -> None:
        item = {**SKIP_ITEM, "tool_call": {"name": "send_line", "args": {}}}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert r.rejected[0]["code"] == "SCHEMA_INVALID"

    def test_nested_action_in_reason_is_just_text(self) -> None:
        """Reason text that looks like JSON is just a string — accepted."""
        item = {**NEW_ITEM, "reason": '{"action":"send","target":"X"}'}
        r = validate(_good_model(item), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert len(r.items) == 1  # accepted — it's just a string

    def test_every_accepted_item_has_only_allowed_fields(self) -> None:
        """Property: every accepted item's keys are a subset of _ACTION_FIELDS."""
        from daily_agenda.validate import _ACTION_FIELDS

        model = _good_model(NEW_ITEM, UPDATE_ITEM, MAYBE_DONE_ITEM, SKIP_ITEM)
        r = validate(model, BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        for item in r.items:
            allowed = _ACTION_FIELDS[item["action"]]
            assert set(item.keys()) <= allowed, \
                f"item has extra keys: {set(item.keys()) - allowed}"


# ---------------------------------------------------------------------------
# Input not mutated
# ---------------------------------------------------------------------------

class TestNoMutation:
    def test_input_not_mutated(self) -> None:
        model = _good_model(NEW_ITEM, SKIP_ITEM)
        original = copy.deepcopy(model)
        validate(model, BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert model == original


# ---------------------------------------------------------------------------
# validate_criteria_draft
# ---------------------------------------------------------------------------

class TestValidateCriteriaDraft:
    def test_valid(self) -> None:
        obj = {"schema_version": 1, "criteria_markdown": "# Criteria\n\nSome rules."}
        text = validate_criteria_draft(obj)
        assert text == "# Criteria\n\nSome rules."

    def test_not_dict(self) -> None:
        with pytest.raises(ModelOutputInvalid):
            validate_criteria_draft("bad")

    def test_bad_version(self) -> None:
        with pytest.raises(ModelOutputInvalid):
            validate_criteria_draft({"schema_version": 2, "criteria_markdown": "x"})

    def test_missing_markdown(self) -> None:
        with pytest.raises(ModelOutputInvalid):
            validate_criteria_draft({"schema_version": 1})

    def test_empty_markdown(self) -> None:
        with pytest.raises(ModelOutputInvalid):
            validate_criteria_draft({"schema_version": 1, "criteria_markdown": "   "})

    def test_too_long(self) -> None:
        with pytest.raises(ModelOutputInvalid):
            validate_criteria_draft({"schema_version": 1, "criteria_markdown": "x" * 20001})

    def test_unknown_field(self) -> None:
        with pytest.raises(ModelOutputInvalid):
            validate_criteria_draft(
                {"schema_version": 1, "criteria_markdown": "ok", "extra": 1}
            )
