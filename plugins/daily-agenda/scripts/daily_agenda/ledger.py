"""Todo ledger — pure functions over list[dict] (§4 contract)."""

from __future__ import annotations

import copy
import random
import string
from datetime import datetime, timedelta
from typing import Callable


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class StaleReport(Exception):
    """Raised when a report-code-based operation targets an outdated report."""

    code: str = "STALE_REPORT"


class IllegalTransition(Exception):
    """Raised when a status transition is not allowed."""


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TRANSITIONS: dict[tuple[str, str], str] = {
    # (from, to) → by
    ("open", "maybe_done"): "model",
    ("maybe_done", "done"): "user",
    ("maybe_done", "open"): "user",
    ("open", "done"): "user",
    ("open", "dismissed"): "user",
    ("maybe_done", "collapsed"): "system",
    ("collapsed", "open"): "user",
    ("collapsed", "done"): "user",
}

_COLLAPSE_DAYS: int = 7

_ID_ALPHABET: str = string.digits
_ID_LENGTH: int = 4
_ID_MAX_ATTEMPTS: int = 5000

# Report code: R- + 3 chars from a safe alphabet (no 0/O/1/I/L).
_REPORT_ALPHABET: str = "23456789ABCDEFGHJKMNPQRSTUVWXYZ"
_REPORT_LENGTH: int = 3


# ---------------------------------------------------------------------------
# ID generators
# ---------------------------------------------------------------------------

def new_todo_id(existing: set[str]) -> str:
    """Return a fresh ``T-NNNN`` not in *existing*."""
    for _ in range(_ID_MAX_ATTEMPTS):
        candidate = "T-" + "".join(random.choices(_ID_ALPHABET, k=_ID_LENGTH))
        if candidate not in existing:
            return candidate
    raise RuntimeError(f"failed to generate unique todo id after {_ID_MAX_ATTEMPTS} attempts")


def new_report_code() -> str:
    """Return a fresh ``R-XXX`` report code."""
    return "R-" + "".join(random.choices(_REPORT_ALPHABET, k=_REPORT_LENGTH))


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def carry_days(todo: dict, *, now: datetime) -> int:
    """Calendar-day difference from creation to *now* in *now*'s timezone."""
    created = datetime.fromisoformat(todo["created_at"])
    created_local = created.astimezone(now.tzinfo)
    now_local = now.astimezone(now.tzinfo)
    return (now_local.date() - created_local.date()).days


def _transition_allowed(from_status: str, to_status: str) -> bool:
    return (from_status, to_status) in _TRANSITIONS


def _transition_by(from_status: str, to_status: str) -> str:
    return _TRANSITIONS[(from_status, to_status)]


def _history_entry(*, at: str, from_status: str, to_status: str, by: str) -> dict:
    return {"at": at, "from": from_status, "to": to_status, "by": by}


def _change_status(
    todo: dict,
    to_status: str,
    *,
    by: str,
    now: datetime,
    extra: dict | None = None,
) -> dict:
    """Return a copy of *todo* with *to_status* applied."""
    t = copy.deepcopy(todo)
    from_status = t["status"]
    iso = now.isoformat()
    t["status"] = to_status
    t["updated_at"] = iso
    t["history"] = t.get("history", []) + [
        _history_entry(at=iso, from_status=from_status, to_status=to_status, by=by),
    ]
    if extra:
        t.update(extra)
    return t


# ---------------------------------------------------------------------------
# apply_model — process validated model items
# ---------------------------------------------------------------------------

def apply_model(
    ledger: list[dict],
    items: list[dict],
    *,
    now: datetime,
    id_factory: Callable[[set[str]], str] | None = None,
) -> list[dict]:
    """Apply validated model items to *ledger*; return new ledger (no mutation).

    ``new`` items whose ``src_ids`` overlap with an existing open/maybe_done
    todo are treated as ``update`` (merge src_ids, apply priority/due, keep
    existing title).
    """
    result = copy.deepcopy(ledger)
    id_gen = id_factory or new_todo_id
    existing_ids = {t["id"] for t in result}
    iso = now.isoformat()

    # Build src_id → todo index for overlap detection.
    src_to_idx: dict[str, int] = {}
    for i, t in enumerate(result):
        if t["status"] in ("open", "maybe_done"):
            for sid in t.get("src_ids", []):
                src_to_idx[sid] = i

    for item in items:
        action = item["action"]

        if action == "new":
            # Check overlap with existing open/maybe_done todos.
            overlapping_indices: set[int] = set()
            for sid in item["src_ids"]:
                if sid in src_to_idx:
                    overlapping_indices.add(src_to_idx[sid])

            if overlapping_indices:
                # Merge into the first overlapping todo.
                target_idx = min(overlapping_indices)
                t = result[target_idx]
                merged_src = list(dict.fromkeys(t["src_ids"] + item["src_ids"]))
                t["src_ids"] = merged_src
                t["priority"] = item.get("priority", t["priority"])
                if "due" in item:
                    t["due"] = item["due"]
                t["updated_at"] = iso
                # Update src index.
                for sid in item["src_ids"]:
                    src_to_idx[sid] = target_idx
            else:
                # Genuine new todo.
                tid = id_gen(existing_ids)
                existing_ids.add(tid)
                new_todo: dict = {
                    "id": tid,
                    "title": item["title"],
                    "priority": item["priority"],
                    "due": item.get("due"),
                    "status": "open",
                    "src_ids": list(item["src_ids"]),
                    "created_at": iso,
                    "updated_at": iso,
                    "maybe_done_since": None,
                    "maybe_done_reason": None,
                    "history": [],
                }
                new_idx = len(result)
                result.append(new_todo)
                for sid in item["src_ids"]:
                    src_to_idx[sid] = new_idx

        elif action == "update":
            for i, t in enumerate(result):
                if t["id"] == item["todo_id"]:
                    t["priority"] = item.get("priority", t["priority"])
                    if "due" in item:
                        t["due"] = item["due"]
                    if item.get("src_ids"):
                        merged = list(dict.fromkeys(t["src_ids"] + item["src_ids"]))
                        t["src_ids"] = merged
                        for sid in item["src_ids"]:
                            src_to_idx[sid] = i
                    t["updated_at"] = iso
                    break

        elif action == "maybe_done":
            for i, t in enumerate(result):
                if t["id"] == item["todo_id"]:
                    if t["status"] == "maybe_done":
                        # Already maybe_done: merge src_ids, keep maybe_done_since.
                        merged = list(dict.fromkeys(t["src_ids"] + item["src_ids"]))
                        t["src_ids"] = merged
                        t["updated_at"] = iso
                        if item.get("reason"):
                            t["maybe_done_reason"] = item["reason"]
                    elif t["status"] == "open":
                        result[i] = _change_status(
                            t, "maybe_done", by="model", now=now,
                            extra={
                                "maybe_done_since": iso,
                                "maybe_done_reason": item.get("reason"),
                            },
                        )
                        # Merge src_ids.
                        merged = list(dict.fromkeys(
                            result[i]["src_ids"] + item["src_ids"],
                        ))
                        result[i]["src_ids"] = merged
                    break

        # action == "skip" → no ledger change.

    return result


# ---------------------------------------------------------------------------
# auto_collapse — maybe_done ≥ 7 days → collapsed
# ---------------------------------------------------------------------------

def auto_collapse(ledger: list[dict], *, now: datetime) -> list[dict]:
    """Collapse ``maybe_done`` items whose ``maybe_done_since`` is ≥ 7 days ago."""
    result = copy.deepcopy(ledger)
    for i, t in enumerate(result):
        if t["status"] != "maybe_done":
            continue
        since_str = t.get("maybe_done_since")
        if not since_str:
            continue
        since = datetime.fromisoformat(since_str)
        if (now - since).days >= _COLLAPSE_DAYS:
            result[i] = _change_status(t, "collapsed", by="system", now=now)
    return result


# ---------------------------------------------------------------------------
# mark_done — user marks items as done
# ---------------------------------------------------------------------------

def mark_done(
    ledger: list[dict],
    state: dict,
    *,
    report_code: str | None,
    numbers: list[int] = (),
    todo_ids: list[str] = (),
    now: datetime,
) -> tuple[list[dict], dict]:
    """Mark items done by number (requires report_code) or by todo_id.

    Returns ``(new_ledger, new_state)`` — state is returned unchanged (copy).
    Raises :class:`StaleReport` when *report_code* doesn't match the current report.
    """
    if numbers:
        # Number-based: require matching report_code.
        current_code = state.get("report_code")
        if not current_code or report_code != current_code:
            raise StaleReport(
                f"report code {report_code!r} does not match current {current_code!r}"
            )
        # Resolve numbers via report_index.
        report_index = state.get("report_index", {})
        resolved_ids: list[str] = []
        for n in numbers:
            tid = report_index.get(str(n))
            if tid is None:
                raise StaleReport(f"number {n} not found in report {report_code}")
            resolved_ids.append(tid)
        todo_ids = list(todo_ids) + resolved_ids

    result = copy.deepcopy(ledger)
    id_set = {t["id"] for t in result}

    for tid in todo_ids:
        if tid not in id_set:
            raise KeyError(f"todo {tid!r} not found")

        for i, t in enumerate(result):
            if t["id"] == tid:
                from_status = t["status"]
                if not _transition_allowed(from_status, "done"):
                    raise IllegalTransition(
                        f"cannot transition {tid} from {from_status} to done"
                    )
                result[i] = _change_status(t, "done", by="user", now=now)
                break

    return result, copy.deepcopy(state)


# ---------------------------------------------------------------------------
# restore — collapsed → open
# ---------------------------------------------------------------------------

def restore(ledger: list[dict], todo_id: str, *, now: datetime) -> list[dict]:
    """Restore a collapsed item back to open."""
    result = copy.deepcopy(ledger)
    for i, t in enumerate(result):
        if t["id"] == todo_id:
            if t["status"] != "collapsed":
                raise IllegalTransition(
                    f"cannot restore {todo_id}: status is {t['status']}, expected collapsed"
                )
            result[i] = _change_status(
                t, "open", by="user", now=now,
                extra={"maybe_done_since": None, "maybe_done_reason": None},
            )
            return result
    raise KeyError(f"todo {todo_id!r} not found")
