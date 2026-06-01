from __future__ import annotations

import copy

import pytest

from mt_core.merge import (
    ACHIEVEMENT_FIELDS,
    apply_decision,
    fact_hash,
    find_narrative_block,
    is_placeholder,
    normalize,
    plan_merge,
    reject_key,
    upsert_narrative_block,
)

# ---------------------------------------------------------------------------
# normalize
# ---------------------------------------------------------------------------

def test_normalize_strips():
    assert normalize("  hello  ") == "hello"


def test_normalize_collapses_internal_whitespace():
    assert normalize("a  b") == "a b"
    assert normalize("a\t\tb") == "a b"
    assert normalize("a\n  b\n  c") == "a b c"


def test_normalize_empty():
    assert normalize("") == ""
    assert normalize("   ") == ""


# ---------------------------------------------------------------------------
# fact_hash
# ---------------------------------------------------------------------------

def test_fact_hash_stable():
    h = fact_hash("hello")
    assert h == fact_hash("hello")
    assert len(h) == 64  # sha256 hex


def test_fact_hash_normalize_invariant():
    # "a  b" and "a b" normalize to same → same hash
    assert fact_hash("a  b") == fact_hash("a b")
    assert fact_hash("  hello  ") == fact_hash("hello")


def test_fact_hash_distinct_for_different_values():
    assert fact_hash("alpha") != fact_hash("beta")


# ---------------------------------------------------------------------------
# reject_key
# ---------------------------------------------------------------------------

def test_reject_key_format():
    rk = reject_key("m1", "progress", "msg-001", "deadbeef")
    assert rk == "m1|progress|msg-001|deadbeef"


def test_reject_key_distinct_when_any_component_differs():
    base = reject_key("m1", "progress", "msg-001", "aaa")
    assert base != reject_key("m2", "progress", "msg-001", "aaa")
    assert base != reject_key("m1", "卡關", "msg-001", "aaa")
    assert base != reject_key("m1", "progress", "msg-002", "aaa")
    assert base != reject_key("m1", "progress", "msg-001", "bbb")


# ---------------------------------------------------------------------------
# find_narrative_block
# ---------------------------------------------------------------------------

SAMPLE_MD = """\
Some intro text.

<!-- mt:narrative id=m1 field=progress -->
Week 22 in progress.
<!-- /mt:narrative -->

Other content.

<!-- mt:narrative id=m1 field=卡關 -->
No blockers.
<!-- /mt:narrative -->
"""


def test_find_narrative_block_present():
    content = find_narrative_block(SAMPLE_MD, "m1", "progress")
    assert content == "Week 22 in progress."


def test_find_narrative_block_cjk_field():
    content = find_narrative_block(SAMPLE_MD, "m1", "卡關")
    assert content == "No blockers."


def test_find_narrative_block_absent_returns_none():
    assert find_narrative_block(SAMPLE_MD, "m1", "nonexistent") is None
    assert find_narrative_block(SAMPLE_MD, "m99", "progress") is None


def test_find_narrative_block_tolerates_extra_spaces():
    md = "<!-- mt:narrative  id=m1   field=progress  -->\nContent here.\n<!-- /mt:narrative -->"
    assert find_narrative_block(md, "m1", "progress") == "Content here."


def test_find_narrative_block_multiline_content():
    md = "<!-- mt:narrative id=m1 field=progress -->\nLine 1.\nLine 2.\n<!-- /mt:narrative -->"
    content = find_narrative_block(md, "m1", "progress")
    assert content == "Line 1.\nLine 2."


# ---------------------------------------------------------------------------
# upsert_narrative_block
# ---------------------------------------------------------------------------

EMPTY_MD = "# Tracking\n\nSome text.\n"


def test_upsert_creates_when_absent():
    result = upsert_narrative_block(EMPTY_MD, "m1", "progress", "First fill.")
    found = find_narrative_block(result, "m1", "progress")
    assert found == "First fill."


def test_upsert_replaces_when_present():
    md = upsert_narrative_block(EMPTY_MD, "m1", "progress", "Old content.")
    md2 = upsert_narrative_block(md, "m1", "progress", "New content.")
    assert find_narrative_block(md2, "m1", "progress") == "New content."
    # Old content should be gone
    assert "Old content." not in md2


def test_upsert_idempotent():
    md_once = upsert_narrative_block(EMPTY_MD, "m1", "progress", "Same content.")
    md_twice = upsert_narrative_block(md_once, "m1", "progress", "Same content.")
    assert md_once == md_twice


def test_upsert_second_field_does_not_clobber_first():
    md = upsert_narrative_block(EMPTY_MD, "m1", "progress", "Progress text.")
    md = upsert_narrative_block(md, "m1", "卡關", "Blocker text.")
    assert find_narrative_block(md, "m1", "progress") == "Progress text."
    assert find_narrative_block(md, "m1", "卡關") == "Blocker text."


def test_upsert_different_metrics_independent():
    md = upsert_narrative_block(EMPTY_MD, "m1", "progress", "M1 text.")
    md = upsert_narrative_block(md, "m2", "progress", "M2 text.")
    assert find_narrative_block(md, "m1", "progress") == "M1 text."
    assert find_narrative_block(md, "m2", "progress") == "M2 text."


def test_upsert_result_can_be_found():
    md = upsert_narrative_block(EMPTY_MD, "x-metric", "owner_report", "Weekly update.")
    assert find_narrative_block(md, "x-metric", "owner_report") == "Weekly update."


# ---------------------------------------------------------------------------
# is_placeholder
# ---------------------------------------------------------------------------

def test_is_placeholder_none():
    assert is_placeholder(None) is True


def test_is_placeholder_empty():
    assert is_placeholder("") is True
    assert is_placeholder("   ") is True


def test_is_placeholder_known_values():
    assert is_placeholder("⏳ 待會議") is True
    assert is_placeholder("—") is True
    assert is_placeholder("(尚無)") is True
    assert is_placeholder("待回填") is True


def test_is_placeholder_real_content():
    assert is_placeholder("本週完成投標文件") is False
    assert is_placeholder("No blockers.") is False
    assert is_placeholder("0") is False


# ---------------------------------------------------------------------------
# plan_merge
# ---------------------------------------------------------------------------

def _make_state(provenance: dict | None = None, rejected: dict | None = None) -> dict:
    state: dict = {}
    if provenance is not None or rejected is not None:
        state["merge"] = {}
        if provenance is not None:
            state["merge"]["cell_provenance"] = provenance
        if rejected is not None:
            state["merge"]["rejected"] = rejected
    return state


def test_plan_merge_skips_achievement_fields():
    md = EMPTY_MD
    state = _make_state()
    proposals = [
        {"metric_id": "m1", "field": "達成率", "new_value": "90%", "source_message_id": "msg1"},
        {"metric_id": "m1", "field": "achievement", "new_value": "OK", "source_message_id": "msg2"},
        {"metric_id": "m1", "field": "achieved", "new_value": "done", "source_message_id": "msg3"},
    ]
    items = plan_merge(md, state, proposals)
    assert all(item["status"] == "skipped_achievement" for item in items)


def test_plan_merge_skips_rejected():
    md = EMPTY_MD
    fh = fact_hash("Some report text.")
    rk = reject_key("m1", "progress", "msg-001", fh)
    state = _make_state(rejected={rk: {"metric_id": "m1", "field": "progress",
                                        "source_message_id": "msg-001",
                                        "fact_hash": fh, "rejected_at": ""}})
    proposals = [
        {"metric_id": "m1", "field": "progress", "new_value": "Some report text.",
         "source_message_id": "msg-001"},
    ]
    items = plan_merge(md, state, proposals)
    assert len(items) == 1
    assert items[0]["status"] == "skipped_rejected"


def test_plan_merge_first_fill_when_empty_no_provenance():
    md = EMPTY_MD  # no narrative blocks
    state = _make_state()
    proposals = [
        {"metric_id": "m1", "field": "progress", "new_value": "Initial report.",
         "source_message_id": "msg-001"},
    ]
    items = plan_merge(md, state, proposals)
    assert len(items) == 1
    assert items[0]["status"] == "first_fill"


def test_plan_merge_clean_update_when_provenance_matches():
    # Simulate AI wrote "Old AI content" last time (set provenance to its hash)
    old_value = "Old AI content."
    md = upsert_narrative_block(EMPTY_MD, "m1", "progress", old_value)
    old_hash = fact_hash(old_value)
    state = _make_state(provenance={"m1": {"progress": old_hash}})
    proposals = [
        {"metric_id": "m1", "field": "progress", "new_value": "New AI content.",
         "source_message_id": "msg-002"},
    ]
    items = plan_merge(md, state, proposals)
    assert len(items) == 1
    assert items[0]["status"] == "clean_update"


def test_plan_merge_conflict_when_human_edited_block():
    # AI wrote "AI content" → set provenance; then human changes to "Human edit"
    ai_value = "AI content."
    human_value = "Human edit."
    md = upsert_narrative_block(EMPTY_MD, "m1", "progress", human_value)
    ai_hash = fact_hash(ai_value)
    state = _make_state(provenance={"m1": {"progress": ai_hash}})
    proposals = [
        {"metric_id": "m1", "field": "progress", "new_value": "New AI proposal.",
         "source_message_id": "msg-003"},
    ]
    items = plan_merge(md, state, proposals)
    assert len(items) == 1
    assert items[0]["status"] == "conflict"


def test_plan_merge_does_not_mutate_state():
    state = _make_state()
    original_state = copy.deepcopy(state)
    md = EMPTY_MD
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "x",
                  "source_message_id": "msg1"}]
    plan_merge(md, state, proposals)
    assert state == original_state


def test_plan_merge_does_not_mutate_md():
    md = EMPTY_MD
    original_md = md
    state = _make_state()
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "x",
                  "source_message_id": "msg1"}]
    plan_merge(md, state, proposals)
    assert md == original_md  # str is immutable, just confirm it wasn't replaced


def test_plan_merge_item_has_reviewed_false():
    md = EMPTY_MD
    state = _make_state()
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "report",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    assert items[0]["reviewed"] is False


def test_plan_merge_item_carries_reject_key():
    md = EMPTY_MD
    state = _make_state()
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "report",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    expected_fh = fact_hash("report")
    expected_rk = reject_key("m1", "progress", "msg1", expected_fh)
    assert items[0]["reject_key"] == expected_rk
    assert items[0]["new_hash"] == expected_fh


def test_plan_merge_empty_state_treated_as_no_merge_block():
    """state with no 'merge' key → treated as no provenance/rejected."""
    state: dict = {}
    md = EMPTY_MD
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "x",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    # Empty md → first_fill
    assert items[0]["status"] == "first_fill"


# ---------------------------------------------------------------------------
# apply_decision — accept
# ---------------------------------------------------------------------------

def test_apply_decision_accept_writes_block():
    md = EMPTY_MD
    state = _make_state()
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "Accepted report.",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    item = items[0]
    new_md, new_state = apply_decision(md, state, item, "accept")
    assert find_narrative_block(new_md, "m1", "progress") == "Accepted report."


def test_apply_decision_accept_records_provenance():
    md = EMPTY_MD
    state = _make_state()
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "Accepted report.",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    item = items[0]
    _, new_state = apply_decision(md, state, item, "accept")
    prov = new_state["merge"]["cell_provenance"]["m1"]["progress"]
    assert prov["hash"] == fact_hash("Accepted report.")
    assert prov["origin"] == "ai"


def test_apply_decision_accept_then_replan_gives_clean_update():
    """After accept, re-running plan_merge with the same value → clean_update."""
    md = EMPTY_MD
    state = _make_state()
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "Report v1.",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    new_md, new_state = apply_decision(md, state, items[0], "accept")

    # Re-run plan with same value
    proposals2 = [{"metric_id": "m1", "field": "progress", "new_value": "Report v1.",
                   "source_message_id": "msg1"}]
    items2 = plan_merge(new_md, new_state, proposals2)
    assert items2[0]["status"] == "clean_update"


def test_apply_decision_does_not_mutate_input_state():
    md = EMPTY_MD
    state = _make_state()
    original = copy.deepcopy(state)
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "x",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    apply_decision(md, state, items[0], "accept")
    assert state == original


# ---------------------------------------------------------------------------
# apply_decision — reject
# ---------------------------------------------------------------------------

def test_apply_decision_reject_records_reject_key():
    md = EMPTY_MD
    state = _make_state()
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "Rejected report.",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    item = items[0]
    _, new_state = apply_decision(md, state, item, "reject")
    assert item["reject_key"] in new_state["merge"]["rejected"]


def test_apply_decision_reject_does_not_write_md():
    md = EMPTY_MD
    state = _make_state()
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "Rejected text.",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    new_md, _ = apply_decision(md, state, items[0], "reject")
    assert find_narrative_block(new_md, "m1", "progress") is None


def test_apply_decision_reject_then_replan_gives_skipped_rejected():
    """After reject, re-running plan_merge with same value → skipped_rejected."""
    md = EMPTY_MD
    state = _make_state()
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "Some text.",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    _, new_state = apply_decision(md, state, items[0], "reject")

    proposals2 = [{"metric_id": "m1", "field": "progress", "new_value": "Some text.",
                   "source_message_id": "msg1"}]
    items2 = plan_merge(md, new_state, proposals2)
    assert items2[0]["status"] == "skipped_rejected"


def test_apply_decision_reject_does_not_mutate_input_state():
    md = EMPTY_MD
    state = _make_state()
    original = copy.deepcopy(state)
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "x",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    apply_decision(md, state, items[0], "reject")
    assert state == original


# ---------------------------------------------------------------------------
# apply_decision — rewrite
# ---------------------------------------------------------------------------

def test_apply_decision_rewrite_writes_human_value():
    md = EMPTY_MD
    state = _make_state()
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "AI proposal.",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    new_md, _ = apply_decision(md, state, items[0], "rewrite", human_value="Human edit.")
    assert find_narrative_block(new_md, "m1", "progress") == "Human edit."


def test_apply_decision_rewrite_sets_provenance_to_human_hash():
    md = EMPTY_MD
    state = _make_state()
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "AI proposal.",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    _, new_state = apply_decision(md, state, items[0], "rewrite", human_value="Human edit.")
    prov = new_state["merge"]["cell_provenance"]["m1"]["progress"]
    assert prov["hash"] == fact_hash("Human edit.")
    assert prov["origin"] == "human"


def test_apply_decision_rewrite_then_different_ai_proposal_surfaces_conflict():
    """After a human rewrite, a DIFFERENT AI proposal must surface as `conflict`.

    DESIGN (spec §機制 3): provenance carries origin (ai|human). A human-rewritten cell
    is human-owned; a differing AI proposal against it is a `conflict` so it gets loud
    review attention rather than blending into routine `clean_update`s (which a
    time-pressed human, triaging by label before the weekly meeting, may rubber-stamp).
    This is the structural guard against silently overwriting a human's explicit edit —
    the core purpose of the merge contract (the 82/50/90 inflation trauma). The data
    model grows from a bare sha256 string to {hash, origin}; a back-compat shim reads
    legacy bare strings as origin='ai'.
    """
    md = EMPTY_MD
    state = _make_state()
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "AI proposal.",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    new_md, new_state = apply_decision(md, state, items[0], "rewrite", human_value="Human edit.")

    # AI proposes a value different from the human edit → conflict (origin=human).
    proposals2 = [{"metric_id": "m1", "field": "progress", "new_value": "AI proposal.",
                   "source_message_id": "msg1"}]
    items2 = plan_merge(new_md, new_state, proposals2)
    assert items2[0]["status"] == "conflict"
    assert items2[0]["reviewed"] is False  # still surfaces for the human gate


def test_apply_decision_rewrite_then_same_value_proposal_is_clean_noop():
    """Companion: after a human rewrite, an AI proposal that MATCHES the human value is a
    no-op `clean_update` (new_hash == stored hash), NOT a conflict — origin-aware logic
    must not over-fire when there is nothing to change."""
    md = EMPTY_MD
    state = _make_state()
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "AI proposal.",
                  "source_message_id": "msg1"}]
    items = plan_merge(md, state, proposals)
    new_md, new_state = apply_decision(md, state, items[0], "rewrite", human_value="Human edit.")

    # AI proposes the SAME value the human wrote → nothing to change → clean_update.
    proposals2 = [{"metric_id": "m1", "field": "progress", "new_value": "Human edit.",
                   "source_message_id": "msg1"}]
    items2 = plan_merge(new_md, new_state, proposals2)
    assert items2[0]["status"] == "clean_update"


def test_plan_merge_legacy_bare_string_provenance_treated_as_ai():
    """Back-compat shim: legacy provenance stored as a bare sha256 string (pre-origin) is
    read as origin='ai'. A differing AI proposal against it stays `clean_update` — the
    human-rewrite protection only applies to entries written with origin='human' after
    the migration (acknowledged migration loss for legacy state, recorded in spec §機制 2)."""
    old_value = "Legacy AI content."
    md = upsert_narrative_block(EMPTY_MD, "m1", "progress", old_value)
    state = _make_state(provenance={"m1": {"progress": fact_hash(old_value)}})  # bare string
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": "Different AI content.",
                  "source_message_id": "msg2"}]
    items = plan_merge(md, state, proposals)
    assert items[0]["status"] == "clean_update"


# ---------------------------------------------------------------------------
# Achievement field — structural exclusion (end-to-end)
# ---------------------------------------------------------------------------

def test_achievement_field_in_frozenset():
    assert "達成率" in ACHIEVEMENT_FIELDS
    assert "achievement" in ACHIEVEMENT_FIELDS
    assert "achieved" in ACHIEVEMENT_FIELDS


def test_achievement_cannot_be_written_even_if_apply_called():
    """Structural guarantee: apply_decision refuses achievement fields by returning unchanged md."""
    md = EMPTY_MD
    state = _make_state()
    # Manually construct an item with achievement field (bypassing plan_merge's skip)
    fh = fact_hash("90%")
    rk = reject_key("m1", "達成率", "msg1", fh)
    item = {
        "metric_id": "m1",
        "field": "達成率",
        "new_value": "90%",
        "source_message_id": "msg1",
        "cur_value": None,
        "new_hash": fh,
        "reject_key": rk,
        "status": "skipped_achievement",
        "reviewed": False,
    }
    new_md, new_state = apply_decision(md, state, item, "accept")
    # Block must NOT appear
    assert find_narrative_block(new_md, "m1", "達成率") is None
    # md unchanged
    assert new_md == md


def test_plan_merge_achievement_skipped_never_produces_narrative():
    """Confirming that achievement proposals are fully filtered before block writes can happen."""
    md = EMPTY_MD
    state = _make_state()
    proposals = [
        {"metric_id": "m1", "field": "達成率", "new_value": "95%", "source_message_id": "msg1"},
    ]
    items = plan_merge(md, state, proposals)
    # All skipped — none should be written
    assert all(i["status"] == "skipped_achievement" for i in items)
    # Confirm no block written (plan_merge is pure, doesn't write, but test the intent)
    assert find_narrative_block(md, "m1", "達成率") is None


# ===========================================================================
# Codex BLOCK fixes (P7 review) — RED first
# ===========================================================================

# --- #1: re.sub replacement-template injection (content treated literally) ---

def test_upsert_replace_path_content_with_backref_chars_literal():
    """Replace path: content with \\1, \\g<0>, backslash paths must round-trip literally."""
    md = upsert_narrative_block(EMPTY_MD, "m1", "progress", "placeholder")
    tricky = r"see \1 and \g<0> at C:\new\tab path"
    md2 = upsert_narrative_block(md, "m1", "progress", tricky)
    assert find_narrative_block(md2, "m1", "progress") == tricky


def test_upsert_append_path_content_with_backref_chars_literal():
    """Append path (no existing block): content must also be treated literally."""
    tricky = r"value \1 \2 \g<name> end"
    md = upsert_narrative_block(EMPTY_MD, "m1", "progress", tricky)
    assert find_narrative_block(md, "m1", "progress") == tricky


# --- #2: content containing narrative markers must be rejected (both markers) ---

def test_upsert_rejects_content_with_close_marker():
    with pytest.raises(ValueError):
        upsert_narrative_block(EMPTY_MD, "m1", "progress",
                               "evil <!-- /mt:narrative --> tail")


def test_upsert_rejects_content_with_open_marker():
    with pytest.raises(ValueError):
        upsert_narrative_block(EMPTY_MD, "m1", "progress",
                               "evil <!-- mt:narrative id=x field=y --> tail")


def test_apply_decision_accept_rejects_marker_content():
    md = EMPTY_MD
    state = _make_state()
    fh = fact_hash("bad")
    item = {"metric_id": "m1", "field": "progress",
            "new_value": "bad <!-- /mt:narrative --> smuggle",
            "source_message_id": "msg1", "cur_value": None, "new_hash": fh,
            "reject_key": "k", "status": "first_fill", "reviewed": False}
    with pytest.raises(ValueError):
        apply_decision(md, state, item, "accept")


# --- #4: achievement exclusion — case/whitespace variants and aliases ---

def test_plan_merge_skips_achievement_case_whitespace_variants():
    md = EMPTY_MD
    state = _make_state()
    for f in ["Achievement", "ACHIEVEMENT", " achievement ", "Achieved", "ACHIEVED"]:
        items = plan_merge(md, state,
                           [{"metric_id": "m1", "field": f, "new_value": "90%",
                             "source_message_id": "x"}])
        assert items[0]["status"] == "skipped_achievement", f


def test_apply_decision_refuses_achievement_variant():
    md = EMPTY_MD
    state = _make_state()
    fh = fact_hash("90%")
    item = {"metric_id": "m1", "field": " Achievement ", "new_value": "90%",
            "source_message_id": "x", "cur_value": None, "new_hash": fh,
            "reject_key": "k", "status": "skipped_achievement", "reviewed": False}
    new_md, _ = apply_decision(md, state, item, "accept")
    assert new_md == md
    assert find_narrative_block(new_md, "m1", " Achievement ") is None


def test_achievement_cjk_still_excluded_after_normalize():
    """CJK 達成率 must stay excluded (casefold is identity for CJK)."""
    md = EMPTY_MD
    state = _make_state()
    items = plan_merge(md, state,
                       [{"metric_id": "m1", "field": "達成率", "new_value": "90%",
                         "source_message_id": "x"}])
    assert items[0]["status"] == "skipped_achievement"


# --- #5: duplicate blocks must fail loud (not silently use first match) ---

_DUP_MD = (
    "<!-- mt:narrative id=m1 field=progress -->\nFirst.\n<!-- /mt:narrative -->\n\n"
    "<!-- mt:narrative id=m1 field=progress -->\nSecond.\n<!-- /mt:narrative -->\n"
)


def test_find_narrative_block_raises_on_duplicate():
    with pytest.raises(ValueError):
        find_narrative_block(_DUP_MD, "m1", "progress")


def test_upsert_raises_on_duplicate():
    with pytest.raises(ValueError):
        upsert_narrative_block(_DUP_MD, "m1", "progress", "new content")


# --- #6: reject_key component-pipe collision ---

def test_reject_key_no_collision_when_metric_field_boundary_shifts():
    k1 = reject_key("a|b", "c", "msg", "h")
    k2 = reject_key("a", "b|c", "msg", "h")
    assert k1 != k2


def test_reject_key_no_collision_when_field_source_boundary_shifts():
    k1 = reject_key("m", "f|msg", "s", "h")
    k2 = reject_key("m", "f", "msg|s", "h")
    assert k1 != k2


def test_plan_merge_legacy_raw_reject_key_still_matches():
    """Migration (codex P7 re-review): a rejected entry persisted with the pre-escape raw
    pipe-joined key — which happens whenever source_message_id contains a pipe, e.g. the
    synthetic 'metric_id|week' — must still be honored by the sticky-reject check via a
    read-only legacy fallback. New writes always use the escaped reject_key."""
    md = EMPTY_MD
    src = "m1|2026-W22"  # synthetic source_message_id contains a pipe
    value = "Some rejected fact."
    fh = fact_hash(value)
    legacy_rk = f"m1|progress|{src}|{fh}"  # OLD raw format (pre-#6 escaping)
    state = _make_state(rejected={legacy_rk: {"metric_id": "m1", "field": "progress",
                                              "source_message_id": src, "fact_hash": fh,
                                              "rejected_at": ""}})
    proposals = [{"metric_id": "m1", "field": "progress", "new_value": value,
                  "source_message_id": src}]
    items = plan_merge(md, state, proposals)
    assert items[0]["status"] == "skipped_rejected"
