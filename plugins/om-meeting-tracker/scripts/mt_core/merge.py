from __future__ import annotations

import copy
import hashlib
import re

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

ACHIEVEMENT_FIELDS: frozenset[str] = frozenset({"達成率", "achievement", "achieved"})

# Common placeholder values that indicate a cell has no real content yet.
_PLACEHOLDER_EXACT = frozenset({
    "⏳ 待會議",
    "—",
    "(尚無)",
    "待回填",
})

# ---------------------------------------------------------------------------
# Pure text helpers
# ---------------------------------------------------------------------------

def normalize(s: str) -> str:
    """strip + collapse internal whitespace runs to a single space."""
    return " ".join(s.split())


def fact_hash(s: str) -> str:
    """SHA-256 hex digest of normalize(s)."""
    return hashlib.sha256(normalize(s).encode("utf-8")).hexdigest()


def reject_key(metric_id: str, field: str, source_message_id: str, fh: str) -> str:
    """Composite reject key: <metric_id>|<field>|<source_message_id>|<fact_hash>."""
    return f"{metric_id}|{field}|{source_message_id}|{fh}"


# ---------------------------------------------------------------------------
# Narrative block locate / upsert
# ---------------------------------------------------------------------------

def _open_marker(metric_id: str, field: str) -> str:
    """Canonical open-marker string (id= then field=)."""
    return f"<!-- mt:narrative id={metric_id} field={field} -->"


_CLOSE_MARKER = "<!-- /mt:narrative -->"


def _block_pattern(metric_id: str, field: str) -> re.Pattern[str]:
    """Regex that matches the full marker pair for (metric_id, field), capturing content."""
    eid = re.escape(metric_id)
    ef = re.escape(field)
    # Tolerate extra spaces around/between attributes; match open-marker on its own line.
    return re.compile(
        r"<!--\s*mt:narrative\s+id="
        + eid
        + r"\s+field="
        + ef
        + r"\s*-->\n(.*?)\n<!--\s*/mt:narrative\s*-->",
        re.DOTALL,
    )


def find_narrative_block(md: str, metric_id: str, field: str) -> str | None:
    """Return content between the marker pair, stripped of leading/trailing newlines, or None."""
    m = _block_pattern(metric_id, field).search(md)
    if m is None:
        return None
    return m.group(1).strip("\n")


def upsert_narrative_block(md: str, metric_id: str, field: str, content: str) -> str:
    """Replace block content in-place if block exists; otherwise append a new block.

    Idempotent: upsert(upsert(md, mid, f, c), mid, f, c) == upsert(md, mid, f, c).
    """
    pattern = _block_pattern(metric_id, field)
    replacement = (
        _open_marker(metric_id, field) + "\n" + content + "\n" + _CLOSE_MARKER
    )

    if pattern.search(md):
        # Replace the full match (open marker + content + close marker) with updated replacement.
        new_md = pattern.sub(replacement, md, count=1)
    else:
        # Append: ensure the md ends with a newline, then add the block.
        sep = "" if md.endswith("\n") else "\n"
        new_md = md + sep + "\n" + replacement + "\n"

    return new_md


# ---------------------------------------------------------------------------
# Placeholder detection
# ---------------------------------------------------------------------------

def is_placeholder(cur: str | None) -> bool:
    """True if cur is None, empty/whitespace, or a known placeholder string."""
    if cur is None:
        return True
    stripped = cur.strip()
    if not stripped:
        return True
    return stripped in _PLACEHOLDER_EXACT


# ---------------------------------------------------------------------------
# plan_merge — pure, no mutations
# ---------------------------------------------------------------------------

def plan_merge(md: str, state: dict, proposals: list[dict]) -> list[dict]:
    """Compute a merge plan without mutating md or state.

    Each proposal: {metric_id, field, new_value, source_message_id}
    Each result item: {metric_id, field, new_value, source_message_id,
                       cur_value, new_hash, reject_key, status, reviewed}
    status ∈ {clean_update, first_fill, conflict,
               skipped_achievement, skipped_rejected}
    """
    merge_block = state.get("merge", {})
    provenance = merge_block.get("cell_provenance", {})
    rejected = merge_block.get("rejected", {})

    items: list[dict] = []
    for proposal in proposals:
        mid = proposal["metric_id"]
        field = proposal["field"]
        new_value = proposal["new_value"]
        src_msg_id = proposal["source_message_id"]

        fh = fact_hash(new_value)
        rk = reject_key(mid, field, src_msg_id, fh)

        # (1) Structural exclusion of achievement fields.
        if field in ACHIEVEMENT_FIELDS:
            items.append({
                "metric_id": mid,
                "field": field,
                "new_value": new_value,
                "source_message_id": src_msg_id,
                "cur_value": None,
                "new_hash": fh,
                "reject_key": rk,
                "status": "skipped_achievement",
                "reviewed": False,
            })
            continue

        # (2) Sticky-reject check.
        if rk in rejected:
            cur = find_narrative_block(md, mid, field)
            items.append({
                "metric_id": mid,
                "field": field,
                "new_value": new_value,
                "source_message_id": src_msg_id,
                "cur_value": cur,
                "new_hash": fh,
                "reject_key": rk,
                "status": "skipped_rejected",
                "reviewed": False,
            })
            continue

        # (3) Determine current block content and status.
        cur = find_narrative_block(md, mid, field)
        stored_hash = provenance.get(mid, {}).get(field)

        if stored_hash is not None:
            # Provenance exists: check if human edited the block.
            cur_hash = fact_hash(cur) if cur is not None else fact_hash("")
            if cur_hash == stored_hash:
                status = "clean_update"
            else:
                status = "conflict"
        elif is_placeholder(cur):
            # No provenance and cur is empty/placeholder → first fill.
            status = "first_fill"
        else:
            # No provenance but cur has real content → treat as human-owned → conflict.
            status = "conflict"

        items.append({
            "metric_id": mid,
            "field": field,
            "new_value": new_value,
            "source_message_id": src_msg_id,
            "cur_value": cur,
            "new_hash": fh,
            "reject_key": rk,
            "status": status,
            "reviewed": False,
        })

    return items


# ---------------------------------------------------------------------------
# apply_decision — returns (new_md, new_state), never mutates inputs
# ---------------------------------------------------------------------------

def apply_decision(
    md: str,
    state: dict,
    item: dict,
    decision: str,
    human_value: str | None = None,
) -> tuple[str, dict]:
    """Apply a human decision to a single merge item.

    decision ∈ {"accept", "reject", "rewrite"}
    Returns (new_md, new_state) — deep-copies state, md is immutable str.

    Structural guarantee: achievement fields are never written regardless of decision.
    """
    mid = item["metric_id"]
    field = item["field"]
    new_state = copy.deepcopy(state)

    # Structural exclusion — achievement fields can never be written.
    if field in ACHIEVEMENT_FIELDS:
        return md, new_state

    # Ensure merge sub-structure exists in new_state.
    new_state.setdefault("merge", {})
    new_state["merge"].setdefault("cell_provenance", {})
    new_state["merge"].setdefault("rejected", {})

    if decision == "accept":
        new_md = upsert_narrative_block(md, mid, field, item["new_value"])
        new_state["merge"]["cell_provenance"].setdefault(mid, {})
        new_state["merge"]["cell_provenance"][mid][field] = item["new_hash"]
        return new_md, new_state

    elif decision == "reject":
        rk = item["reject_key"]
        rejected_at = item.get("rejected_at", "")
        new_state["merge"]["rejected"][rk] = {
            "metric_id": mid,
            "field": field,
            "source_message_id": item["source_message_id"],
            "fact_hash": item["new_hash"],
            "rejected_at": rejected_at,
        }
        return md, new_state

    elif decision == "rewrite":
        if human_value is None:
            raise ValueError("rewrite decision requires human_value")
        new_md = upsert_narrative_block(md, mid, field, human_value)
        h = fact_hash(human_value)
        new_state["merge"]["cell_provenance"].setdefault(mid, {})
        new_state["merge"]["cell_provenance"][mid][field] = h
        return new_md, new_state

    else:
        raise ValueError(f"Unknown decision: {decision!r}")
