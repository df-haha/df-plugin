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

    def test_schema_version_2_accepted(self) -> None:
        result = validate({"schema_version": 2, "items": []}, BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        assert result.items == []

    def test_wrong_schema_version(self) -> None:
        with pytest.raises(ModelOutputInvalid):
            validate({"schema_version": 3, "items": []}, BUNDLE_SRC_IDS, OPEN_TODO_IDS)

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


# ---------------------------------------------------------------------------
# Digest validation (C1 lenient)
# ---------------------------------------------------------------------------

_CHAT_REF_A = "chat:aabbccdd11223344aabbcc01"
_CHAT_REF_B = "chat:aabbccdd11223344aabbcc02"

_LINE_RECORDS: list[dict] = [
    {"kind": "line_message", "chat_ref": _CHAT_REF_A, "ts": "2026-10-08T09:00:00+08:00",
     "src_id": "line:chat:aa:message:m1", "is_self": False},
    {"kind": "line_message", "chat_ref": _CHAT_REF_A, "ts": "2026-10-08T09:05:00+08:00",
     "src_id": "line:chat:aa:message:m2", "is_self": True},
    {"kind": "line_message", "chat_ref": _CHAT_REF_B, "ts": "2026-10-08T10:00:00+08:00",
     "src_id": "line:chat:bb:message:m3", "is_self": False},
]

_MAIL_RECORDS: list[dict] = [
    {"kind": "mail", "src_id": "mail:AAA"},
    {"kind": "mail", "src_id": "mail:BBB"},
]


class TestChatDigestValidation:
    def _v2_model(self, chat_digests: list | None = None, mail_digests: list | None = None) -> dict:
        m: dict = {"schema_version": 2, "items": []}
        if chat_digests is not None:
            m["chat_digests"] = chat_digests
        if mail_digests is not None:
            m["mail_digests"] = mail_digests
        return m

    def test_valid_chat_digest(self) -> None:
        digests = [{"chat_ref": _CHAT_REF_A, "bullets": ["point 1"], "needs_reply": [], "quiet": False}]
        result = validate(self._v2_model(chat_digests=digests), BUNDLE_SRC_IDS, OPEN_TODO_IDS,
                          records=_LINE_RECORDS)
        assert len(result.chat_digests) == 1
        assert result.chat_digests[0]["chat_ref"] == _CHAT_REF_A

    def test_missing_digests_treated_as_empty(self) -> None:
        result = validate(self._v2_model(), BUNDLE_SRC_IDS, OPEN_TODO_IDS, records=_LINE_RECORDS)
        assert result.chat_digests == []
        assert result.mail_digests == []

    def test_non_list_digests_treated_as_empty(self) -> None:
        result = validate(self._v2_model(chat_digests="bad"), BUNDLE_SRC_IDS, OPEN_TODO_IDS,
                          records=_LINE_RECORDS)
        assert result.chat_digests == []

    def test_unknown_chat_ref_dropped(self) -> None:
        digests = [{"chat_ref": "chat:nonexistent", "bullets": ["x"], "needs_reply": [], "quiet": False}]
        result = validate(self._v2_model(chat_digests=digests), BUNDLE_SRC_IDS, OPEN_TODO_IDS,
                          records=_LINE_RECORDS)
        assert result.chat_digests == []

    def test_bullets_truncated_to_5(self) -> None:
        digests = [{"chat_ref": _CHAT_REF_A, "bullets": [f"b{i}" for i in range(10)],
                     "needs_reply": [], "quiet": False}]
        result = validate(self._v2_model(chat_digests=digests), BUNDLE_SRC_IDS, OPEN_TODO_IDS,
                          records=_LINE_RECORDS)
        assert len(result.chat_digests[0]["bullets"]) == 5

    def test_bullet_string_truncated_to_80(self) -> None:
        digests = [{"chat_ref": _CHAT_REF_A, "bullets": ["x" * 200],
                     "needs_reply": [], "quiet": False}]
        result = validate(self._v2_model(chat_digests=digests), BUNDLE_SRC_IDS, OPEN_TODO_IDS,
                          records=_LINE_RECORDS)
        assert len(result.chat_digests[0]["bullets"][0]) == 80

    def test_needs_reply_is_self_dropped(self) -> None:
        # line:chat:aa:message:m2 is is_self=True
        digests = [{"chat_ref": _CHAT_REF_A,
                     "bullets": ["x"],
                     "needs_reply": [{"src_id": "line:chat:aa:message:m2", "ask": "hey"}],
                     "quiet": False}]
        result = validate(self._v2_model(chat_digests=digests), BUNDLE_SRC_IDS, OPEN_TODO_IDS,
                          records=_LINE_RECORDS)
        assert result.chat_digests[0]["needs_reply"] == []

    def test_needs_reply_ask_truncated(self) -> None:
        digests = [{"chat_ref": _CHAT_REF_A,
                     "bullets": ["x"],
                     "needs_reply": [{"src_id": "line:chat:aa:message:m1", "ask": "a" * 200}],
                     "quiet": False}]
        result = validate(self._v2_model(chat_digests=digests), BUNDLE_SRC_IDS, OPEN_TODO_IDS,
                          records=_LINE_RECORDS)
        assert len(result.chat_digests[0]["needs_reply"][0]["ask"]) == 80

    def test_non_dict_entry_dropped(self) -> None:
        digests = ["bad", {"chat_ref": _CHAT_REF_A, "bullets": ["ok"], "needs_reply": [], "quiet": False}]
        result = validate(self._v2_model(chat_digests=digests), BUNDLE_SRC_IDS, OPEN_TODO_IDS,
                          records=_LINE_RECORDS)
        assert len(result.chat_digests) == 1

    def test_items_still_validated_with_digests(self) -> None:
        """Items validation is unchanged even when digests are present."""
        model = self._v2_model(chat_digests=[])
        model["items"] = [NEW_ITEM]
        result = validate(model, BUNDLE_SRC_IDS, OPEN_TODO_IDS, records=_LINE_RECORDS)
        assert len(result.items) == 1
        assert result.rejected == []

    def test_needs_reply_wrong_chat_ref_dropped(self) -> None:
        """MEDIUM-2: needs_reply src_id that belongs to a DIFFERENT chat_ref → dropped."""
        # _LINE_RECORDS: m1 and m2 are CHAT_REF_A, m3 is CHAT_REF_B
        digests = [{
            "chat_ref": _CHAT_REF_A,
            "bullets": ["x"],
            "needs_reply": [
                {"src_id": "line:chat:aa:message:m1", "ask": "ok"},    # correct chat
                {"src_id": "line:chat:bb:message:m3", "ask": "wrong"}, # CHAT_REF_B's message
            ],
            "quiet": False,
        }]
        result = validate(self._v2_model(chat_digests=digests), BUNDLE_SRC_IDS, OPEN_TODO_IDS,
                          records=_LINE_RECORDS)
        assert len(result.chat_digests[0]["needs_reply"]) == 1
        assert result.chat_digests[0]["needs_reply"][0]["src_id"] == "line:chat:aa:message:m1"


class TestMailDigestValidation:
    def _v2_model(self, mail_digests: list | None = None) -> dict:
        m: dict = {"schema_version": 2, "items": []}
        if mail_digests is not None:
            m["mail_digests"] = mail_digests
        return m

    def test_valid_mail_digest(self) -> None:
        digests = [{"src_id": "mail:AAA", "category": "reply", "summary": "Need response"}]
        result = validate(self._v2_model(mail_digests=digests), BUNDLE_SRC_IDS, OPEN_TODO_IDS,
                          records=_MAIL_RECORDS)
        assert len(result.mail_digests) == 1

    def test_unknown_src_id_dropped(self) -> None:
        digests = [{"src_id": "mail:NONEXIST", "category": "reply", "summary": "x"}]
        result = validate(self._v2_model(mail_digests=digests), BUNDLE_SRC_IDS, OPEN_TODO_IDS,
                          records=_MAIL_RECORDS)
        assert result.mail_digests == []

    def test_invalid_category_dropped(self) -> None:
        digests = [{"src_id": "mail:AAA", "category": "spam", "summary": "x"}]
        result = validate(self._v2_model(mail_digests=digests), BUNDLE_SRC_IDS, OPEN_TODO_IDS,
                          records=_MAIL_RECORDS)
        assert result.mail_digests == []

    def test_summary_truncated(self) -> None:
        digests = [{"src_id": "mail:AAA", "category": "fyi", "summary": "x" * 200}]
        result = validate(self._v2_model(mail_digests=digests), BUNDLE_SRC_IDS, OPEN_TODO_IDS,
                          records=_MAIL_RECORDS)
        assert len(result.mail_digests[0]["summary"]) == 120

    def test_no_records_passes_through(self) -> None:
        """When no records are provided, digest entries pass through (no filtering)."""
        digests = [{"src_id": "mail:AAA", "category": "reply", "summary": "x"}]
        result = validate(self._v2_model(mail_digests=digests), BUNDLE_SRC_IDS, OPEN_TODO_IDS)
        # No records → no filtering context → entries pass through
        assert len(result.mail_digests) == 1

    def test_mail_only_records_drops_chat_digests(self) -> None:
        """With mail-only records, chat digests should be dropped (no LINE messages)."""
        model: dict = {"schema_version": 2, "items": [],
                       "chat_digests": [{"chat_ref": "chat:fake", "bullets": ["x"], "needs_reply": [], "quiet": False}],
                       "mail_digests": [{"src_id": "mail:AAA", "category": "reply", "summary": "x"}]}
        result = validate(model, BUNDLE_SRC_IDS, OPEN_TODO_IDS, records=_MAIL_RECORDS)
        assert result.chat_digests == []
        assert len(result.mail_digests) == 1

    def test_line_only_records_drops_mail_digests(self) -> None:
        """With LINE-only records, mail digests should be dropped (no mail)."""
        model: dict = {"schema_version": 2, "items": [],
                       "chat_digests": [{"chat_ref": _CHAT_REF_A, "bullets": ["x"], "needs_reply": [], "quiet": False}],
                       "mail_digests": [{"src_id": "mail:fake", "category": "reply", "summary": "x"}]}
        result = validate(model, BUNDLE_SRC_IDS, OPEN_TODO_IDS, records=_LINE_RECORDS)
        assert len(result.chat_digests) == 1
        assert result.mail_digests == []
