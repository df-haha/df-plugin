"""Walk a mailbox folder tree and return a flat list with display paths.

The walker is independent of GraphSource (which handles MCP plumbing) —
it takes a callable ``list_fn(parent_id: str) -> list[dict]`` so tests
can inject a simple dict-based fake without MCP machinery.

Well-known folders are identified by name heuristic so the skill can
recommend Inbox + subfolders and advise against Sent/Deleted/Junk/Drafts.
"""

from __future__ import annotations

from typing import Any, Callable


# Well-known folder name → canonical key.  Matched case-insensitively
# against both English and zh-TW display names.
_WELL_KNOWN_NAMES: dict[str, str] = {
    # English
    "inbox": "inbox",
    "sent items": "sentitems",
    "sent": "sentitems",
    "deleted items": "deleteditems",
    "deleted": "deleteditems",
    "junk email": "junkemail",
    "junk": "junkemail",
    "drafts": "drafts",
    "archive": "archive",
    "outbox": "outbox",
    # zh-TW (Outlook 繁中)
    "收件匣": "inbox",
    "寄件備份": "sentitems",
    "已傳送的郵件": "sentitems",
    "刪除的郵件": "deleteditems",
    "垃圾郵件": "junkemail",
    "草稿": "drafts",
    "封存": "archive",
    "寄件匣": "outbox",
}

# Folders the skill should advise against selecting
SKIP_WELL_KNOWN: frozenset[str] = frozenset({
    "sentitems", "deleteditems", "junkemail", "drafts", "outbox",
})


def _detect_well_known(name: str) -> str | None:
    """Return the well-known key for *name*, or None."""
    return _WELL_KNOWN_NAMES.get(name.strip().lower())


def walk_folders(
    list_fn: Callable[[str], list[dict[str, Any]]],
    *,
    max_depth: int = 3,
    max_total: int = 200,
) -> list[dict[str, Any]]:
    """Walk the mailbox folder tree breadth-first.

    Parameters
    ----------
    list_fn:
        ``list_fn(parent_id)`` returns ``[{id, name, total, unread,
        child_count, hidden}]``.  ``parent_id=""`` for top-level.
    max_depth:
        Maximum recursion depth (1 = top-level only).
    max_total:
        Stop collecting after this many folders.

    Returns
    -------
    Flat list of ``{id, path, total, unread, well_known, depth}``
    sorted by path.  Hidden folders are skipped.
    """
    results: list[dict[str, Any]] = []
    # Queue: (parent_id, parent_path_parts, current_depth)
    queue: list[tuple[str, list[str], int]] = [("", [], 1)]

    while queue and len(results) < max_total:
        parent_id, parent_parts, depth = queue.pop(0)
        try:
            children = list_fn(parent_id)
        except Exception:
            if parent_id == "":
                # Top-level failure is fatal (auth expired, tool missing, etc.)
                raise
            # Child folder failures (deleted, etc.) skip that subtree
            continue

        for child in children:
            if len(results) >= max_total:
                break

            # Skip hidden folders
            if child.get("hidden"):
                continue

            name = child.get("name", "")
            folder_id = child.get("id", "")
            path_parts = parent_parts + [name]
            display_path = " / ".join(path_parts)
            # Well-known folders are always top-level (depth 1).
            # A subfolder named "Sent" under Inbox is NOT the real Sent Items.
            well_known = _detect_well_known(name) if depth == 1 else None

            results.append({
                "id": folder_id,
                "path": display_path,
                "total": child.get("total", 0),
                "unread": child.get("unread", 0),
                "well_known": well_known,
                "depth": depth,
            })

            # Recurse into children if they exist and depth allows
            child_count = child.get("child_count", 0)
            if child_count and child_count > 0 and depth < max_depth:
                queue.append((folder_id, path_parts, depth + 1))

    results.sort(key=lambda f: f["path"])
    return results
