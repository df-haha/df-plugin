"""Bundle writer and batch splitter for daily-agenda.

Writes ``bundle.jsonl`` (contract §2) — first line is always ``meta``.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from .fsutil import atomic_write_text


def write_bundle(
    path: Path,
    *,
    result: Any,
    criteria: str | None,
    open_todos: list[dict[str, Any]],
    ctx: list[dict[str, Any]],
    batch_index: int = 1,
    batch_total: int = 1,
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
    lines.append(json.dumps(meta, ensure_ascii=False))

    # -- criteria --
    if criteria:
        lines.append(json.dumps({
            "kind": "criteria",
            "version": result.criteria_version if hasattr(result, "criteria_version") else result.get("criteria_version", 1),
            "text": criteria,
        }, ensure_ascii=False))

    # -- open todos --
    for todo in open_todos:
        lines.append(json.dumps({
            "kind": "open_todo",
            "todo_id": todo.get("id", ""),
            "status": todo.get("status", "open"),
            "priority": todo.get("priority", "P2"),
            "due": todo.get("due"),
            "created_at": todo.get("created_at", ""),
            "src_ids": todo.get("src_ids", []),
            "untrusted": {"title": todo.get("title", "")},
        }, ensure_ascii=False))

    # -- records --
    records = result.records if hasattr(result, "records") else result.get("records", [])
    for rec in records:
        lines.append(json.dumps(rec, ensure_ascii=False))

    # -- ctx --
    for entry in ctx:
        lines.append(json.dumps({
            "kind": "ctx",
            "path": entry.get("path", ""),
            "untrusted": {
                "title": entry.get("title", ""),
                "summary": entry.get("summary", ""),
            },
        }, ensure_ascii=False))

    atomic_write_text(path, "\n".join(lines) + "\n")


def split_batches(
    records: list[dict[str, Any]],
    batch_bytes: int,
) -> list[list[dict[str, Any]]]:
    """Split *records* into batches that fit within *batch_bytes*.

    Grouping priority: by ``chat_ref`` (LINE), then by kind (``mail``,
    ``event``).  A single oversized group is further split by individual
    records.  No record is ever dropped.
    """
    if not records:
        return []

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

        if current_size + group_size <= batch_bytes:
            current_batch.extend(group)
            current_size += group_size
        elif group_size > batch_bytes:
            # Oversized group — flush current, then split the group by records
            if current_batch:
                batches.append(current_batch)
                current_batch = []
                current_size = 0
            for rec in group:
                rec_size = len(json.dumps(rec, ensure_ascii=False).encode("utf-8")) + 1
                if current_size + rec_size > batch_bytes and current_batch:
                    batches.append(current_batch)
                    current_batch = []
                    current_size = 0
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
