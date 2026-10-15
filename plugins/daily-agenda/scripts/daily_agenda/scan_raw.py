"""Raw message sample writer for profile inference.

Writes a capped raw sample to ``<home>/runs/<id>/raw/`` — per-chat and
per-mail-folder markdown files, plus a stats file with per-sender and
per-chat counts.

Also saves the full collected records to ``<home>/runs/<id>/records.jsonl``
for reuse by ``criteria draft --from-run``.

Caps are chosen to keep the whole sample well under ~200k chars.
"""

from __future__ import annotations

import hashlib
import json
from pathlib import Path
from typing import Any

from .fence import fence_untrusted
from .fsutil import atomic_write_text, atomic_write_json, jsonl_line


# ---------------------------------------------------------------------------
# Caps
# ---------------------------------------------------------------------------

TOP_CHATS = 20              # Top N chats by message volume
MAX_MESSAGES_PER_CHAT = 30  # Last K messages per chat
MAX_CHARS_PER_MESSAGE = 500 # Truncate individual messages
TOTAL_CHARS_CAP = 200_000   # Total chars across all output files
MAX_MAIL_PER_FOLDER = 30    # Last K mail items per folder


# ---------------------------------------------------------------------------
# Records persistence (for reuse by criteria draft --from-run)
# ---------------------------------------------------------------------------

def save_records(records: list[dict[str, Any]], run_dir: Path) -> Path:
    """Save full collected records as JSONL for later reuse.

    Returns the path to the saved file.
    """
    path = run_dir / "records.jsonl"
    lines = [jsonl_line(rec) for rec in records]
    atomic_write_text(path, "\n".join(lines) + "\n" if lines else "")
    return path


def load_records(run_dir: Path) -> list[dict[str, Any]]:
    """Load collected records from a previous run.

    Raises FileNotFoundError if the file does not exist.
    """
    path = run_dir / "records.jsonl"
    text = path.read_text(encoding="utf-8").strip()
    if not text:
        return []
    return [json.loads(line) for line in text.splitlines() if line.strip()]


def save_source_status(source_status: dict[str, Any], run_dir: Path) -> Path:
    """Save source status alongside records for reuse."""
    path = run_dir / "source-status.json"
    atomic_write_json(path, source_status)
    return path


def load_source_status(run_dir: Path) -> dict[str, Any]:
    """Load source status from a previous run."""
    path = run_dir / "source-status.json"
    return json.loads(path.read_text(encoding="utf-8"))


# ---------------------------------------------------------------------------
# Writers
# ---------------------------------------------------------------------------

def _safe_filename(name: str, prefix: str, idx: int) -> str:
    """Create a safe filename from index (NOT from untrusted name)."""
    # Use index-based naming to avoid path traversal from untrusted chat names
    h = hashlib.md5(name.encode()).hexdigest()[:8]
    return f"{prefix}-{idx:03d}-{h}.md"


def write_raw_sample(
    records: list[dict[str, Any]],
    output_dir: Path,
) -> dict[str, Any]:
    """Write raw sample files from collect records.

    Returns result with paths + counts.
    """
    output_dir.mkdir(parents=True, exist_ok=True)

    # Group by source
    line_chats: dict[str, list[dict[str, Any]]] = {}
    mail_folders: dict[str, list[dict[str, Any]]] = {}
    chat_counts: dict[str, int] = {}
    sender_counts: dict[str, int] = {}

    for rec in records:
        kind = rec.get("kind")
        if kind == "line_message":
            ref = rec.get("chat_ref", "unknown")
            line_chats.setdefault(ref, []).append(rec)
            # Per-sender count
            sender = rec.get("untrusted", {}).get("sender", "?")
            sender_counts[sender] = sender_counts.get(sender, 0) + 1
        elif kind == "mail":
            folder = rec.get("folder", "inbox")
            mail_folders.setdefault(folder, []).append(rec)
            # Per-sender count
            sender = rec.get("untrusted", {}).get("from", "?")
            sender_counts[sender] = sender_counts.get(sender, 0) + 1

    # Sort chats by volume, take top N
    sorted_chats = sorted(line_chats.items(), key=lambda x: len(x[1]), reverse=True)
    top_chats = sorted_chats[:TOP_CHATS]

    total_chars = 0
    output_files: list[str] = []

    # Write LINE chat files
    for idx, (chat_ref, messages) in enumerate(top_chats, 1):
        if total_chars >= TOTAL_CHARS_CAP:
            break

        # Take last K messages (sorted by timestamp)
        messages.sort(key=lambda m: m.get("ts", ""))
        sample = messages[-MAX_MESSAGES_PER_CHAT:]

        # Get chat name from first message (untrusted)
        chat_name = ""
        if sample:
            ut = sample[0].get("untrusted", {})
            chat_name = ut.get("chat_name", chat_ref)

        lines = [f"# LINE: {chat_name}\n"]
        lines.append(f"chat_ref: {chat_ref}\n")
        lines.append(f"messages: {len(messages)} total, {len(sample)} sampled\n\n")

        for msg in sample:
            ut = msg.get("untrusted", {})
            sender = ut.get("sender", "?")
            text = ut.get("text", "")
            ts = msg.get("ts", "")
            if len(text) > MAX_CHARS_PER_MESSAGE:
                text = text[:MAX_CHARS_PER_MESSAGE] + "…"
            lines.append(f"[{ts}] {sender}: {text}\n")

        content = "".join(lines)
        remaining = TOTAL_CHARS_CAP - total_chars
        if len(content) > remaining:
            content = content[:remaining]

        fname = _safe_filename(chat_ref, "line", idx)
        fpath = output_dir / fname
        atomic_write_text(fpath, content)
        total_chars += len(content)
        output_files.append(str(fpath))
        chat_counts[f"line:{chat_ref}"] = len(messages)

    # Write mail folder files
    for idx, (folder, items) in enumerate(mail_folders.items(), 1):
        if total_chars >= TOTAL_CHARS_CAP:
            break

        # Take last K items
        items.sort(key=lambda m: m.get("received_at", ""))
        sample = items[-MAX_MAIL_PER_FOLDER:]

        lines = [f"# Mail: {folder}\n"]
        lines.append(f"messages: {len(items)} total, {len(sample)} sampled\n\n")

        for item in sample:
            ut = item.get("untrusted", {})
            sender = ut.get("from", "?")
            subject = ut.get("subject", "")
            body = ut.get("body", "")
            ts = item.get("received_at", "")
            if len(body) > MAX_CHARS_PER_MESSAGE:
                body = body[:MAX_CHARS_PER_MESSAGE] + "…"
            lines.append(f"## [{ts}] {sender}: {subject}\n{body}\n\n")

        content = "".join(lines)
        remaining = TOTAL_CHARS_CAP - total_chars
        if len(content) > remaining:
            content = content[:remaining]

        fname = _safe_filename(folder, "mail", idx)
        fpath = output_dir / fname
        atomic_write_text(fpath, content)
        total_chars += len(content)
        output_files.append(str(fpath))
        chat_counts[f"mail:{folder}"] = len(items)

    # Write stats (includes per-sender counts — file only, taints when read)
    stats_path = output_dir / "stats.json"
    atomic_write_json(stats_path, {
        "total_chars": total_chars,
        "line_chats": len(top_chats),
        "mail_folders": len(mail_folders),
        "message_counts_per_chat": chat_counts,
        "message_counts_per_sender": sender_counts,
    })
    output_files.append(str(stats_path))

    return {
        "ok": True,
        "code": None,
        "raw_dir": str(output_dir),
        "files": output_files,
        "total_chars": total_chars,
        "line_chats": len(top_chats),
        "mail_folders": len(mail_folders),
    }


# ---------------------------------------------------------------------------
# Raw show: resolve todo → src_ids → find in bundles
# ---------------------------------------------------------------------------

def resolve_raw_for_todo(
    todo_id: str,
    ledger: list[dict[str, Any]],
    runs_dir: Path,
) -> dict[str, Any]:
    """Find raw source records for a todo's src_ids.

    Scans bundle files in newest-first order.
    Returns fenced source records or a not-found response.
    """
    # Find the todo
    todo = None
    for t in ledger:
        if t.get("id") == todo_id:
            todo = t
            break

    if todo is None:
        return {"ok": False, "code": "TODO_NOT_FOUND", "detail": f"todo {todo_id} not found"}

    src_ids = set(todo.get("src_ids", []))
    if not src_ids:
        return {"ok": False, "code": "NO_SRC_IDS", "detail": f"todo {todo_id} has no src_ids"}

    # Scan bundles newest-first
    found: dict[str, dict[str, Any]] = {}
    missing = set(src_ids)

    if runs_dir.exists():
        run_dirs = sorted(runs_dir.iterdir(), reverse=True)
        for run_dir in run_dirs:
            if not run_dir.is_dir():
                continue
            if not missing:
                break
            for bp in sorted(run_dir.glob("bundle*.jsonl")):
                try:
                    text = bp.read_text(encoding="utf-8")
                except OSError:
                    continue
                for line in text.splitlines():
                    if not line.strip():
                        continue
                    try:
                        rec = json.loads(line)
                    except json.JSONDecodeError:
                        continue
                    sid = rec.get("src_id")
                    if sid and sid in missing:
                        found[sid] = rec
                        missing.discard(sid)

    # Fence all found records (they contain untrusted content)
    fenced_records: list[str] = []
    for sid in sorted(found.keys()):
        fenced_records.append(fence_untrusted(
            json.dumps(found[sid], ensure_ascii=False, indent=2)
        ))

    return {
        "ok": True,
        "code": None,
        "todo_id": todo_id,
        "found": len(found),
        "missing": sorted(missing),
        "records": fenced_records,
    }
