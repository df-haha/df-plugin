"""Bundle writer and batch splitter for daily-agenda.

Writes ``bundle.jsonl`` (contract §2) — first line is always ``meta``.
"""

from __future__ import annotations

import json
import logging
from pathlib import Path
from typing import Any

from .fsutil import atomic_write_text, jsonl_line

logger = logging.getLogger(__name__)


def _build_history_record(
    cref: str,
    entries_raw: list[dict[str, Any]],
) -> dict[str, Any]:
    """Build a single ``chat_history`` bundle record from raw history entries.

    Strips to spec: date + bullets only, max 3 entries x 5 bullets each.
    No ``src_id`` — history must never be citeable.
    """
    entries: list[dict[str, Any]] = []
    for e in entries_raw[:3]:
        created = e.get("created_at", "")
        date_str = created[:10] if created else ""
        bullets = e.get("bullets", [])
        if not isinstance(bullets, list):
            bullets = []
        entries.append({
            "date": date_str,
            "bullets": [b for b in bullets[:5] if isinstance(b, str)],
        })
    return {
        "kind": "chat_history",
        "chat_ref": cref,
        "untrusted": {"entries": entries},
    }


def write_bundle(
    path: Path,
    *,
    result: Any,
    criteria: str | None,
    open_todos: list[dict[str, Any]],
    ctx: list[dict[str, Any]],
    profile_text: str | None = None,
    batch_index: int = 1,
    batch_total: int = 1,
    chat_history: dict[str, list[dict[str, Any]]] | None = None,
) -> None:
    """Write a bundle.jsonl file at *path*.

    Parameters
    ----------
    result:
        A ``CollectResult`` (or dict-like with the required attributes).
    criteria:
        Full text of the current criteria version (``None`` → omitted).
    open_todos:
        Currently open/maybe_done todos from the ledger.
    ctx:
        Context index entries.
    batch_index, batch_total:
        Batch metadata for split runs.
    """
    lines: list[str] = []

    # -- meta (always first) --
    meta: dict[str, Any] = {
        "kind": "meta",
        "run_id": result.run_id if hasattr(result, "run_id") else result.get("run_id", ""),
        "generated_at": result.generated_at if hasattr(result, "generated_at") else result.get("generated_at", ""),
        "tz": result.tz if hasattr(result, "tz") else result.get("tz", "Asia/Taipei"),
        "sources": result.source_status if hasattr(result, "source_status") else result.get("source_status", {}),
        "new_chats": [{"ref": c["ref"]} for c in (result.new_chats if hasattr(result, "new_chats") else result.get("new_chats", []))],
        "batch": {"index": batch_index, "total": batch_total},
    }
    lines.append(jsonl_line(meta))

    # -- criteria --
    if criteria:
        lines.append(jsonl_line({
            "kind": "criteria",
            "version": result.criteria_version if hasattr(result, "criteria_version") else result.get("criteria_version", 1),
            "text": criteria,
        }))

    # -- profile (untrusted: may contain inferred third-party names) --
    if profile_text:
        lines.append(jsonl_line({
            "kind": "profile",
            "untrusted": {"text": profile_text},
        }))

    # -- open todos --
    for todo in open_todos:
        lines.append(jsonl_line({
            "kind": "open_todo",
            "todo_id": todo.get("id", ""),
            "status": todo.get("status", "open"),
            "priority": todo.get("priority", "P2"),
            "due": todo.get("due"),
            "created_at": todo.get("created_at", ""),
            "src_ids": todo.get("src_ids", []),
            "untrusted": {"title": todo.get("title", "")},
        }))

    # -- records --
    records = result.records if hasattr(result, "records") else result.get("records", [])
    for rec in records:
        lines.append(jsonl_line(rec))

    # -- chat_history (C4 §B): past AI digests as untrusted context --
    if chat_history:
        # Collect distinct chat_refs from line_message records in this batch
        seen_refs: list[str] = []
        seen_set: set[str] = set()
        for rec in records:
            if rec.get("kind") == "line_message":
                cref = rec.get("chat_ref", "")
                if cref and cref not in seen_set:
                    seen_refs.append(cref)
                    seen_set.add(cref)
        for cref in seen_refs:
            entries_raw = chat_history.get(cref, [])
            if not entries_raw:
                continue
            lines.append(jsonl_line(_build_history_record(cref, entries_raw)))

    # -- ctx --
    for entry in ctx:
        lines.append(jsonl_line({
            "kind": "ctx",
            "path": entry.get("path", ""),
            "untrusted": {
                "title": entry.get("title", ""),
                "summary": entry.get("summary", ""),
            },
        }))

    atomic_write_text(path, "\n".join(lines) + "\n")


def split_batches(
    records: list[dict[str, Any]],
    batch_bytes: int,
    *,
    history_sizes: dict[str, int] | None = None,
) -> list[list[dict[str, Any]]]:
    """Split *records* into batches that fit within *batch_bytes*.

    Grouping priority: by ``chat_ref`` (LINE), then by kind (``mail``,
    ``event``).  A single oversized group is further split by individual
    records.  No record is ever dropped.

    *history_sizes* maps ``chat_ref`` → byte cost of the ``chat_history``
    record that will be appended for that chat.  The cost is included in
    group sizing so the resulting batch (records + history) fits the budget.
    """
    if not records:
        return []

    hsizes = history_sizes or {}

    # Group records
    groups: dict[str, list[dict[str, Any]]] = {}
    for rec in records:
        key = rec.get("chat_ref") or rec.get("kind", "other")
        groups.setdefault(key, []).append(rec)

    batches: list[list[dict[str, Any]]] = []
    current_batch: list[dict[str, Any]] = []
    current_size = 0

    for _key, group in groups.items():
        group_size = sum(len(json.dumps(r, ensure_ascii=False).encode("utf-8")) + 1 for r in group)
        group_size += hsizes.get(_key, 0)

        if current_size + group_size <= batch_bytes:
            current_batch.extend(group)
            current_size += group_size
        elif group_size > batch_bytes:
            # Oversized group — flush current, then split the group by records
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                current_size = 0
            hist_overhead = hsizes.get(_key, 0)
            for rec in group:
                rec_size = len(json.dumps(rec, ensure_ascii=False).encode("utf-8")) + 1
                effective = rec_size + (hist_overhead if not current_batch else 0)
                if current_size + effective > batch_bytes and current_batch:
                    batches.append(current_batch)
                    current_batch = []
                    current_size = 0
                if not current_batch:
                    current_size = hist_overhead
                current_batch.append(rec)
                current_size += rec_size
        else:
            # Flush current batch, start new one with this group
            if current_batch:
                batches.append(current_batch)
            current_batch = list(group)
            current_size = group_size

    if current_batch:
        batches.append(current_batch)

    return batches


# ---------------------------------------------------------------------------
# Chat history loader (C4 §B)
# ---------------------------------------------------------------------------

def load_chat_history(
    home: Path,
    records: list[dict[str, Any]],
    run_id: str,
) -> dict[str, list[dict[str, Any]]]:
    """Load recent chat digests for every ``line_message`` chat_ref in *records*.

    Returns ``{chat_ref: [entry, ...]}`` where each entry has ``created_at``
    and ``bullets`` — ready to pass to :func:`write_bundle`.

    Failures (missing/corrupt DB) are swallowed — the run must not fail
    because history is unavailable.
    """
    from . import history

    chat_refs: list[str] = []
    seen: set[str] = set()
    for rec in records:
        if rec.get("kind") == "line_message":
            cref = rec.get("chat_ref", "")
            if cref and cref not in seen:
                chat_refs.append(cref)
                seen.add(cref)

    if not chat_refs:
        return {}

    result: dict[str, list[dict[str, Any]]] = {}
    for cref in chat_refs:
        try:
            rows = history.read_last(home, cref, n=3, exclude_run_id=run_id)
            if rows:
                result[cref] = rows
        except Exception:
            # Corrupt / inaccessible DB — proceed without history
            logger.debug("Failed to read history for %s", cref, exc_info=True)
    return result


def compute_history_sizes(
    chat_history: dict[str, list[dict[str, Any]]],
) -> dict[str, int]:
    """Compute the byte cost of each chat_history record for batch sizing.

    Returns ``{chat_ref: byte_count}`` for use with :func:`split_batches`.
    The size includes the trailing newline (``+1``) that ``write_bundle``
    adds when joining lines, so the estimate matches what is actually written.
    """
    sizes: dict[str, int] = {}
    for cref, entries_raw in chat_history.items():
        if not entries_raw:
            continue
        rec = _build_history_record(cref, entries_raw)
        sizes[cref] = len(jsonl_line(rec).encode("utf-8")) + 1
    return sizes
