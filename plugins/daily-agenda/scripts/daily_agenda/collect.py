"""Data collection orchestrator — reads LINE, Outlook mail, and calendar.

Each source is independent: a failure in one does not block the others.
Checkpoints advance only on success (SC-9).  Excluded LINE chats are
filtered **before** any read (SC-3 / C-5).

Contract reference: impl-contracts.md §6.
"""

from __future__ import annotations

import math
import re
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone
from typing import Any, Protocol
from zoneinfo import ZoneInfo

from .fence import RAW_SENTINEL
from .progress import Progress


# ---------------------------------------------------------------------------
# Protocols (duck-typed sources — tests inject fakes)
# ---------------------------------------------------------------------------

class LineSource(Protocol):
    def probe(self) -> dict[str, Any]: ...
    def recent_chats(self, *, days: int, offset: int, limit: int) -> dict[str, Any]: ...
    def messages(
        self,
        *,
        chat_name: str,
        chat_ref: str,
        chat_type: str,
        date_from: str,
        date_to: str,
        cursor: str | None,
        message_limit: int,
    ) -> dict[str, Any]: ...


class GraphSource(Protocol):
    def mail_list(self, *, days: int, folder: str) -> list[dict[str, Any]]: ...
    def mail_get(self, message_id: str) -> dict[str, Any]: ...
    def calendar_list(self, *, days_ahead: int, days_back: int, tz: str = "") -> list[dict[str, Any]]: ...


# ---------------------------------------------------------------------------
# Result
# ---------------------------------------------------------------------------

@dataclass
class CollectResult:
    run_id: str
    generated_at: str
    tz: str
    records: list[dict[str, Any]] = field(default_factory=list)
    source_status: dict[str, dict[str, Any]] = field(default_factory=dict)
    new_checkpoints: dict[str, Any] = field(default_factory=dict)
    known_chats: list[dict[str, Any]] = field(default_factory=list)
    new_chats: list[dict[str, Any]] = field(default_factory=list)
    mail_index: dict[str, str] = field(default_factory=dict)
    criteria_version: int = 1


class CollectError(Exception):
    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_MAX_LINE_DAYS_PER_SEGMENT = 31  # reader enforces dateTo - dateFrom ≤ 30
_OVERLAP_HOURS = 2
_RECENT_CHATS_PAGE_LIMIT = 50
_RECENT_CHATS_MAX_OFFSET = 1000
_TAIPEI = ZoneInfo("Asia/Taipei")


def _iso_now(tz: ZoneInfo) -> str:
    return datetime.now(tz).isoformat()


def _parse_iso(s: str) -> datetime:
    return datetime.fromisoformat(s)


def _taipei_date(dt_obj: datetime) -> str:
    """Convert a datetime to a Taipei-local date string (YYYY-MM-DD)."""
    return dt_obj.astimezone(_TAIPEI).strftime("%Y-%m-%d")


def _checkpoint_from(checkpoint: str | None, now: datetime, initial_days: int) -> datetime:
    """Compute the read-from timestamp: checkpoint − 2h, or now − initial_days."""
    if checkpoint:
        return _parse_iso(checkpoint) - timedelta(hours=_OVERLAP_HOURS)
    return now - timedelta(days=initial_days)


# ---------------------------------------------------------------------------
# LINE collection
# ---------------------------------------------------------------------------

def _collect_line(
    cfg: dict[str, Any],
    state: dict[str, Any],
    *,
    line: LineSource,
    now: datetime,
    initial_days: int,
    progress: Progress = Progress(None),
) -> tuple[list[dict[str, Any]], dict[str, Any], list[dict[str, Any]], list[dict[str, Any]]]:
    """Collect LINE messages.

    Returns (records, status_dict, known_chats, new_chats).
    """
    checkpoint = (state.get("checkpoints") or {}).get("line")
    from_dt = _checkpoint_from(checkpoint, now, initial_days)
    to_dt = now
    from_ms = int(from_dt.timestamp() * 1000)

    # Clamp to 30 days (recentChats only accepts 14 or 30)
    max_lookback = now - timedelta(days=30)
    if from_dt < max_lookback:
        from_dt = max_lookback

    excluded_refs = {
        c["ref"] for c in cfg.get("line", {}).get("excluded_chats", [])
    }
    known_refs = {c["ref"] for c in state.get("known_chats", [])}
    # No LINE baseline yet (no known chats and LINE never completed a run —
    # the LINE checkpoint only advances on success, so a first run where LINE
    # was closed does not count): nothing is "new".  Otherwise every chat the
    # user chose to read would be reported and the skill would re-ask the
    # exclusion question right after setup.
    first_run = not known_refs and not (state.get("checkpoints") or {}).get("line")

    # Paginate recentChats
    all_chats: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = line.recent_chats(days=30, offset=offset, limit=_RECENT_CHATS_PAGE_LIMIT)
        all_chats.extend(page.get("chats", []))
        if not page.get("hasMore", False):
            break
        offset += _RECENT_CHATS_PAGE_LIMIT
        if offset >= _RECENT_CHATS_MAX_OFFSET:
            raise CollectError("RECENT_SCOPE_TOO_LARGE")

    # Filter: skip excluded chats, track new ones
    new_chats: list[dict[str, Any]] = []
    active_chats: list[dict[str, Any]] = []
    for chat in all_chats:
        ref = chat.get("chatRef", "")
        if ref in excluded_refs:
            continue
        if ref not in known_refs and not first_run:
            new_chats.append({"ref": ref, "name": chat.get("chatName", "")})
        active_chats.append(chat)

    # Build updated known_chats
    known_chats_map = {c["ref"]: c for c in state.get("known_chats", [])}
    for chat in active_chats:
        ref = chat.get("chatRef", "")
        if ref not in known_chats_map:
            known_chats_map[ref] = {
                "ref": ref,
                "name": chat.get("chatName", ""),
                "first_seen": _iso_now(ZoneInfo(cfg.get("tz", "Asia/Taipei"))),
            }
    known_chats = list(known_chats_map.values())

    # Read messages for each active chat
    progress.event("line_list", n=len(active_chats))
    records: list[dict[str, Any]] = []
    seen_src_ids: set[str] = set()
    chat_index = 0

    for chat in active_chats:
        chat_index += 1
        progress.throttled_chat(chat_index, len(active_chats))
        chat_ref = chat.get("chatRef", "")
        chat_name = chat.get("chatName", "")
        chat_type = chat.get("chatType", "auto")

        # Skip if last message is before our from_dt
        last_ts = chat.get("lastMessageTimestamp")
        if isinstance(last_ts, (int, float)) and last_ts < from_ms:
            continue

        # Segment by 31-day windows
        seg_from = from_dt
        while seg_from < to_dt:
            seg_to = min(seg_from + timedelta(days=30), to_dt)
            date_from = _taipei_date(seg_from)
            date_to = _taipei_date(seg_to)

            cursor: str | None = None
            while True:
                page = line.messages(
                    chat_name=chat_name,
                    chat_ref=chat_ref,
                    chat_type=chat_type,
                    date_from=date_from,
                    date_to=date_to,
                    cursor=cursor,
                    message_limit=1000,
                )

                for msg in page.get("messages", []):
                    source_ref = msg.get("sourceRef", "")
                    src_id = f"line:{chat_ref}:{source_ref}"

                    # Time filter: only messages after from_ms
                    ts = msg.get("sourceTimestamp", 0)
                    if ts < from_ms:
                        continue

                    if src_id in seen_src_ids:
                        continue
                    seen_src_ids.add(src_id)

                    tz_info = ZoneInfo(cfg.get("tz", "Asia/Taipei"))
                    msg_dt = datetime.fromtimestamp(ts / 1000, tz=tz_info)

                    # Determine is_self
                    own_ref = page.get("ownSenderRef")
                    is_self = (msg.get("senderRef") == own_ref) if own_ref else False

                    records.append({
                        "_raw": RAW_SENTINEL,
                        "kind": "line_message",
                        "src_id": src_id,
                        "chat_ref": chat_ref,
                        "chat_type": chat_type,
                        "ts": msg_dt.isoformat(),
                        "is_self": is_self,
                        "untrusted": {
                            "chat_name": chat_name,
                            "sender": msg.get("sender") or "",
                            "text": msg.get("text") or "",
                        },
                    })

                if page.get("hasMore") and page.get("nextCursor"):
                    cursor = page["nextCursor"]
                else:
                    break

            seg_from = seg_to

    status = {
        "status": "ok",
        "code": None,
        "from": from_dt.astimezone(ZoneInfo(cfg.get("tz", "Asia/Taipei"))).isoformat(),
        "to": to_dt.astimezone(ZoneInfo(cfg.get("tz", "Asia/Taipei"))).isoformat(),
        "chats": len(active_chats),
        "messages": len(records),
    }
    return records, status, known_chats, new_chats


# ---------------------------------------------------------------------------
# Mail collection
# ---------------------------------------------------------------------------

_MISSING_FOLDER_RE = re.compile(r"Graph 404\b")


def _is_missing_folder(detail: str) -> bool:
    """Detect Graph API responses meaning "folder does not exist".

    Graph 404 = folder deleted/moved; 400 ErrorInvalidIdMalformed = bad id.
    We match the structured prefix ``Graph 404`` (other 400s are not "missing") that
    ``GraphHTTPError.__str__`` emits, plus ``ErrorItemNotFound`` /
    ``ErrorInvalidIdMalformed`` in the body — NOT bare substrings like
    ``"404"`` which could appear in folder ids or URLs.
    """
    return bool(
        _MISSING_FOLDER_RE.match(detail)
        or "ErrorItemNotFound" in detail
        or "ErrorInvalidIdMalformed" in detail
    )


def _migrate_mail_checkpoint(
    checkpoints: dict[str, Any],
    folders: list[str],
) -> dict[str, str | None]:
    """Migrate legacy scalar ``checkpoints["mail"]`` to per-folder dict.

    Legacy: ``checkpoints["mail"] = "2026-10-08T09:00:00+08:00"``
    New:    ``checkpoints["mail"] = {"inbox": "2026-10-08T09:00:00+08:00"}``

    The legacy value is attributed to ``"inbox"`` only; other folders
    get ``None`` (initial window).

    Stale keys (folders no longer in config) are **preserved** so that
    removing then re-adding a folder resumes from checkpoint−2h instead
    of re-reading the full initial window.
    """
    raw = checkpoints.get("mail")
    if raw is None:
        return {f: None for f in folders}
    if isinstance(raw, str):
        # Legacy scalar → per-folder dict
        result: dict[str, str | None] = {f: None for f in folders}
        if "inbox" in folders:
            result["inbox"] = raw
        return result
    if isinstance(raw, dict):
        # Already per-folder; include ALL existing keys (stale preservation)
        # plus any newly added folders with None
        merged: dict[str, str | None] = dict(raw)
        for f in folders:
            if f not in merged:
                merged[f] = None
        return merged
    return {f: None for f in folders}


def _collect_mail(
    cfg: dict[str, Any],
    state: dict[str, Any],
    *,
    graph: GraphSource,
    now: datetime,
    initial_days: int,
    progress: Progress = Progress(None),
) -> tuple[list[dict[str, Any]], dict[str, Any], dict[str, str], dict[str, str | None]]:
    """Collect Outlook mail.

    Returns (records, status_dict, updated_mail_index, per_folder_checkpoints).
    """
    tz = ZoneInfo(cfg.get("tz", "Asia/Taipei"))
    folders = cfg.get("mail", {}).get("folders", ["inbox"])
    folder_checkpoints = _migrate_mail_checkpoint(
        state.get("checkpoints") or {}, folders,
    )

    mail_index = dict(state.get("mail_index") or {})
    records: list[dict[str, Any]] = []
    seen_src_ids: set[str] = set()
    missing_folders: list[str] = []
    failed_folders: list[str] = []
    new_checkpoints: dict[str, str | None] = dict(folder_checkpoints)

    for folder_idx, folder in enumerate(folders, 1):
        progress.event("mail", i=folder_idx, n=len(folders))

        checkpoint = folder_checkpoints.get(folder)
        from_dt = _checkpoint_from(checkpoint, now, initial_days)
        from_ms = int(from_dt.timestamp() * 1000)

        # mail_list_recent only accepts whole days
        delta = now - from_dt
        days = max(1, math.ceil(delta.total_seconds() / 86400))

        try:
            items = graph.mail_list(days=days, folder=folder)
        except Exception as exc:
            code = getattr(exc, "code", "")
            detail = getattr(exc, "detail", str(exc))
            # Fatal errors: re-raise to fail the whole mail source
            if code in ("DF_GRAPH_AUTH", "DF_GRAPH_TOO_OLD", "GRAPH_TRUNCATED"):
                raise
            # Detect missing folder (Graph API 404/400 malformed id)
            if code == "GRAPH_FAILED" and _is_missing_folder(detail):
                missing_folders.append(folder)
            else:
                failed_folders.append(folder)
            # Don't advance this folder's checkpoint
            continue

        for item in items:
            msg_id = item.get("id", "")
            internet_message_id = item.get("internet_message_id", "")

            # SC-11: normalize via mail_index
            if internet_message_id and internet_message_id in mail_index:
                src_id = mail_index[internet_message_id]
            else:
                src_id = f"mail:{msg_id}"
                if internet_message_id:
                    mail_index[internet_message_id] = src_id

            if src_id in seen_src_ids:
                continue

            # Time filter
            date_str = item.get("date", "")
            try:
                received = _parse_iso(date_str)
                if received.timestamp() * 1000 < from_ms:
                    continue
            except (ValueError, TypeError):
                pass

            seen_src_ids.add(src_id)

            # Fetch full message
            full = graph.mail_get(msg_id)

            # Update mail_index with internet_message_id from full response
            imid = full.get("internet_message_id") or internet_message_id
            if imid and imid not in mail_index:
                mail_index[imid] = src_id

            received_at = full.get("received_at", date_str)

            records.append({
                "_raw": RAW_SENTINEL,
                "kind": "mail",
                "src_id": src_id,
                "internet_message_id": imid,
                "received_at": received_at,
                "folder": folder,
                "untrusted": {
                    "from": full.get("from", ""),
                    "to": full.get("to", ""),
                    "subject": full.get("subject", ""),
                    "body": full.get("body", ""),
                },
            })

        # Advance this folder's checkpoint on success
        new_checkpoints[folder] = now.isoformat()

    # Determine overall status
    if missing_folders:
        status_code = "MAIL_FOLDER_MISSING"
        overall_status = "partial"
    elif failed_folders:
        status_code = "MAIL_FOLDER_PARTIAL"
        overall_status = "partial"
    else:
        status_code = None
        overall_status = "ok"

    # Use the earliest from_dt across all folders for the status range
    all_from = [
        _checkpoint_from(folder_checkpoints.get(f), now, initial_days)
        for f in folders
    ]
    earliest_from = min(all_from) if all_from else now

    status: dict[str, Any] = {
        "status": overall_status,
        "code": status_code,
        "from": earliest_from.astimezone(tz).isoformat(),
        "to": now.astimezone(tz).isoformat(),
        "messages": len(records),
    }
    if missing_folders:
        status["missing_folders"] = missing_folders
    if failed_folders:
        status["failed_folders"] = failed_folders

    return records, status, mail_index, new_checkpoints


# ---------------------------------------------------------------------------
# Calendar collection
# ---------------------------------------------------------------------------

def _collect_calendar(
    cfg: dict[str, Any],
    *,
    graph: GraphSource,
    now: datetime,
    progress: Progress = Progress(None),
) -> tuple[list[dict[str, Any]], dict[str, Any]]:
    """Collect calendar events (today + 3 days, always full refresh)."""
    progress.event("calendar")
    tz = ZoneInfo(cfg.get("tz", "Asia/Taipei"))
    items = graph.calendar_list(days_ahead=3, days_back=1)

    records: list[dict[str, Any]] = []
    seen: set[str] = set()

    for item in items:
        event_id = item.get("id", "")
        src_id = f"event:{event_id}"
        if src_id in seen:
            continue
        seen.add(src_id)

        records.append({
            "_raw": RAW_SENTINEL,
            "kind": "event",
            "src_id": src_id,
            "start": item.get("start", ""),
            "end": item.get("end", ""),
            "all_day": item.get("all_day", False),
            "web_link": item.get("web_link", ""),
            "untrusted": {
                "subject": item.get("subject", ""),
                "location": item.get("location", ""),
                "organizer": item.get("organizer", ""),
                "preview": item.get("preview", ""),
            },
        })

    status = {
        "status": "ok",
        "code": None,
        "from": now.astimezone(tz).isoformat(),
        "to": (now + timedelta(days=3)).astimezone(tz).isoformat(),
        "events": len(records),
    }
    return records, status


# ---------------------------------------------------------------------------
# Main orchestrator
# ---------------------------------------------------------------------------

def collect(
    cfg: dict[str, Any],
    state: dict[str, Any],
    *,
    now: datetime | None = None,
    line: LineSource | None = None,
    graph: GraphSource | None = None,
    run_id: str = "",
    initial_days: int = 14,
    progress: Progress = Progress(None),
) -> CollectResult:
    """Run collection across all enabled sources.

    Parameters
    ----------
    cfg : config dict (contract §1)
    state : current generation state dict
    now : override for the current time (default: ``datetime.now(tz)``)
    line : LINE source (None = disabled / not configured)
    graph : Graph source (None = disabled / not configured)
    run_id : unique identifier for this run
    initial_days : lookback days when no checkpoint exists
    progress : progress writer (default no-op)
    """
    # -- consent gate (must be first) --
    consent = (cfg.get("consent") or {}).get("accepted_at")
    if not consent:
        raise CollectError("CONSENT_REQUIRED")

    tz = ZoneInfo(cfg.get("tz", "Asia/Taipei"))
    if now is None:
        now = datetime.now(tz)

    result = CollectResult(
        run_id=run_id,
        generated_at=now.isoformat(),
        tz=cfg.get("tz", "Asia/Taipei"),
    )
    checkpoints = dict(state.get("checkpoints") or {})

    # -- LINE --
    line_enabled = cfg.get("line", {}).get("enabled", False)
    if line_enabled and line is not None:
        try:
            line_records, line_status, known_chats, new_chats = _collect_line(
                cfg, state, line=line, now=now, initial_days=initial_days,
                progress=progress,
            )
            result.records.extend(line_records)
            result.source_status["line"] = line_status
            result.known_chats = known_chats
            result.new_chats = new_chats
            checkpoints["line"] = now.isoformat()
        except Exception as exc:
            code = getattr(exc, "code", None) or "LOCAL_READER_FAILED"
            result.source_status["line"] = {
                "status": "failed", "code": code,
                "from": "", "to": "", "chats": 0, "messages": 0,
            }
            # Don't advance checkpoint on failure (SC-9)
    elif line_enabled:
        result.source_status["line"] = {
            "status": "skipped", "code": "LINE_RUNTIME_MISSING",
            "from": "", "to": "", "chats": 0, "messages": 0,
        }
    else:
        result.source_status["line"] = {
            "status": "skipped", "code": None,
            "from": "", "to": "", "chats": 0, "messages": 0,
        }

    # -- Mail --
    mail_enabled = cfg.get("mail", {}).get("enabled", True)
    if mail_enabled and graph is not None:
        try:
            mail_records, mail_status, mail_index, mail_folder_cps = _collect_mail(
                cfg, state, graph=graph, now=now, initial_days=initial_days,
                progress=progress,
            )
            result.records.extend(mail_records)
            result.source_status["mail"] = mail_status
            result.mail_index = mail_index
            checkpoints["mail"] = mail_folder_cps
        except Exception as exc:
            code = getattr(exc, "code", None) or "GRAPH_FAILED"
            result.source_status["mail"] = {
                "status": "failed", "code": code,
                "from": "", "to": "", "messages": 0,
            }
    elif not mail_enabled:
        result.source_status["mail"] = {
            "status": "skipped", "code": None,
            "from": "", "to": "", "messages": 0,
        }
    else:
        result.source_status["mail"] = {
            "status": "skipped", "code": "DF_GRAPH_NOT_REGISTERED",
            "from": "", "to": "", "messages": 0,
        }

    # -- Calendar --
    if graph is not None:
        try:
            cal_records, cal_status = _collect_calendar(cfg, graph=graph, now=now, progress=progress)
            result.records.extend(cal_records)
            result.source_status["calendar"] = cal_status
        except Exception as exc:
            code = getattr(exc, "code", None) or "GRAPH_FAILED"
            result.source_status["calendar"] = {
                "status": "failed", "code": code,
                "from": "", "to": "", "events": 0,
            }
    else:
        result.source_status["calendar"] = {
            "status": "skipped", "code": None,
            "from": "", "to": "", "events": 0,
        }

    result.new_checkpoints = checkpoints
    return result


# ---------------------------------------------------------------------------
# Probe mode (setup steps 2–4)
# ---------------------------------------------------------------------------

def probe(
    cfg: dict[str, Any],
    *,
    line: LineSource | None = None,
    graph: GraphSource | None = None,
) -> dict[str, Any]:
    """Probe each source and return status + counts (no data collection)."""
    statuses: dict[str, Any] = {}

    if line is not None:
        statuses["line"] = line.probe()

    if graph is not None:
        try:
            mail_items = graph.mail_list(days=1, folder="inbox")
            statuses["mail"] = {"ok": True, "count": len(mail_items)}
        except Exception as exc:
            statuses["mail"] = {"ok": False, "code": getattr(exc, "code", "GRAPH_FAILED")}

        try:
            cal_items = graph.calendar_list(days_ahead=3, days_back=1)
            statuses["calendar"] = {"ok": True, "count": len(cal_items)}
        except Exception as exc:
            statuses["calendar"] = {"ok": False, "code": getattr(exc, "code", "GRAPH_FAILED")}

    return statuses


# ---------------------------------------------------------------------------
# Chat listing (setup step 5)
# ---------------------------------------------------------------------------

def list_chats(
    cfg: dict[str, Any],
    line: LineSource,
    *,
    days: int = 30,
) -> list[dict[str, Any]]:
    """List recent LINE chats for user selection (setup step 5).

    Returns ``[{"ref", "name", "type", "last_message_at"}]``.
    """
    if days not in (14, 30):
        days = 30

    all_chats: list[dict[str, Any]] = []
    offset = 0
    while True:
        page = line.recent_chats(days=days, offset=offset, limit=_RECENT_CHATS_PAGE_LIMIT)
        all_chats.extend(page.get("chats", []))
        if not page.get("hasMore", False):
            break
        offset += _RECENT_CHATS_PAGE_LIMIT
        if offset >= _RECENT_CHATS_MAX_OFFSET:
            break

    return [
        {
            "ref": c.get("chatRef", ""),
            "name": c.get("chatName", ""),
            "type": c.get("chatType", ""),
            "last_message_at": c.get("lastMessageAt", ""),
        }
        for c in all_chats
    ]
