"""View model builder and safe renderer (§5 contract)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import jinja2
import markupsafe

from daily_agenda.digest_script import DIGEST_SCRIPT, DIGEST_SCRIPT_HASH
from daily_agenda.ledger import carry_days as _carry_days
from daily_agenda.messages import humanize


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

_SAFE_HREF_PREFIXES = ("https://", "mailto:")

# Report code validation — only codes matching this pattern are emitted
# into the data-report attribute.
_REPORT_CODE_RE = re.compile(r"^R-[A-Z0-9]{3}$")

# Characters that could form a control sequence if left in a URL.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

# Markdown meta-characters to escape (backslash first so we don't double-escape).
_MD_ESCAPE_CHARS = "\\`*_[]()<!>#|"

# Source status → Chinese label.
_STATUS_LABELS: dict[str, str] = {
    "ok": "已讀取",
    "partial": "部分讀取",
    "skipped": "略過",
}


# ---------------------------------------------------------------------------
# href sanitisation
# ---------------------------------------------------------------------------

def _sanitize_href(raw: Any) -> str | None:
    """Return a safe href or None.

    - Strip leading/trailing whitespace.
    - If ANY control character remains → None (blocks ``java\\tscript:`` etc.).
    - Must start with ``https://`` or ``mailto:`` (case-insensitive).
    """
    if raw is None or not isinstance(raw, str):
        return None
    stripped = raw.strip()
    if not stripped:
        return None
    if _CONTROL_CHAR_RE.search(stripped):
        return None
    if not stripped.lower().startswith(_SAFE_HREF_PREFIXES):
        return None
    return stripped


# ---------------------------------------------------------------------------
# Markdown escaping
# ---------------------------------------------------------------------------

def _md_escape(text: Any) -> str:
    """Escape a string for safe Markdown rendering.

    Collapses \\r, \\n, \\t → space (a newline in a title would break table rows
    and ``# evil`` could start a heading).
    """
    if text is None:
        return ""
    s = str(text)
    # Collapse control whitespace to space.
    s = re.sub(r"[\r\n\t]", " ", s)
    # Backslash-escape markdown meta-characters.
    for ch in _MD_ESCAPE_CHARS:
        s = s.replace(ch, f"\\{ch}")
    return s


def _md_href(url: str) -> str:
    """Percent-encode characters unsafe in a Markdown link href."""
    out: list[str] = []
    for ch in url:
        if ch in "() <>`\\":
            out.append(f"%{ord(ch):02X}")
        else:
            out.append(ch)
    return "".join(out)


# ---------------------------------------------------------------------------
# build_view
# ---------------------------------------------------------------------------

def build_view(
    *,
    state: dict,
    ledger: list[dict],
    bundle_meta: dict,
    events: list[dict],
    new_chat_names: list[str],
    notices: list[str],
    now: datetime,
    report_code: str,
    skipped: list[dict] | None = None,
    src_index: dict[str, dict] | None = None,
    # C3 digest additions (all optional for backward compat)
    records: list[dict] | None = None,
    chat_digests: list[dict] | None = None,
    mail_digests: list[dict] | None = None,
    folder_labels: dict[str, str] | None = None,
    chat_history: dict[str, list[dict]] | None = None,
) -> dict:
    """Build the view model for rendering.

    *src_index*: ``{src_id: {"label": str, "href": str|None}}`` for source
    annotations. Falls back to src_id as label if missing.

    Returns a dict ready for ``render()``, including ``report_index``.
    """
    si = src_index or {}
    skipped_items = list(skipped) if skipped else []
    all_records = records or []
    all_chat_digests = chat_digests or []
    all_mail_digests = mail_digests or []
    fl = folder_labels or {}
    ch = chat_history or {}

    # --- sources header ---
    sources_header: list[dict] = []
    for name, key in [("LINE", "line"), ("Outlook", "mail"), ("行事曆", "calendar")]:
        src_status = bundle_meta.get("sources", {}).get(key, {})
        status = src_status.get("status", "skipped")
        code = src_status.get("code")

        if status == "failed":
            message = humanize(code) if code else "讀取失敗"
        elif status == "partial":
            label = _STATUS_LABELS["partial"]
            hint = humanize(code) if code else ""
            message = f"{label}（{hint}）" if hint else label
        else:
            message = _STATUS_LABELS.get(status, status)

        coverage_str = ""
        if status in ("ok", "partial"):
            f = src_status.get("from", "")
            t = src_status.get("to", "")
            if f and t:
                coverage_str = f"{_format_dt(f)} – {_format_dt(t)}"

        sources_header.append({
            "name": name, "status": status, "message": message, "coverage": coverage_str,
        })

    # --- today (open items) ---
    today: list[dict] = []
    maybe_done_list: list[dict] = []
    collapsed_list: list[dict] = []
    report_index: dict[str, str] = {}
    counter = 1

    for t in ledger:
        if t["status"] == "open":
            entry = _todo_view(t, counter, si, now)
            today.append(entry)
            report_index[str(counter)] = t["id"]
            counter += 1

    for t in ledger:
        if t["status"] == "maybe_done":
            entry = _todo_view(t, counter, si, now)
            entry["reason"] = t.get("maybe_done_reason", "")
            maybe_done_list.append(entry)
            report_index[str(counter)] = t["id"]
            counter += 1

    for t in ledger:
        if t["status"] == "collapsed":
            entry = _todo_view(t, counter, si, now)
            entry["reason"] = t.get("maybe_done_reason", "")
            collapsed_list.append(entry)
            report_index[str(counter)] = t["id"]
            counter += 1

    # --- events ---
    event_views: list[dict] = []
    for ev in events:
        ut = ev.get("untrusted", {})
        event_views.append({
            "start": _format_dt(ev.get("start", "")),
            "end": _format_dt(ev.get("end", "")),
            "subject": ut.get("subject", ""),
            "location": ut.get("location", ""),
            "href": _sanitize_href(ev.get("web_link")),
        })

    # --- skipped ---
    skipped_views: list[dict] = []
    for sk in skipped_items:
        src_labels = []
        for sid in sk.get("src_ids", []):
            info = si.get(sid, {"label": sid, "href": None})
            src_labels.append({
                "label": info.get("label", sid),
                "href": _sanitize_href(info.get("href")),
            })
        skipped_views.append({
            "summary": sk.get("reason", ""),
            "reason": sk.get("reason", ""),
            "sources": src_labels,
        })

    # --- batch coverage ---
    batch = bundle_meta.get("batch", {})
    total = batch.get("total", 1)
    coverage = {"batched": total > 1, "batches": total}

    # --- C3: digest view keys ---
    chats, quiet_chats, mail_folders, other_todos = _build_digest_views(
        ledger=ledger,
        today=today,
        report_index=report_index,
        all_records=all_records,
        all_chat_digests=all_chat_digests,
        all_mail_digests=all_mail_digests,
        folder_labels=fl,
        chat_history=ch,
        bundle_meta=bundle_meta,
        si=si,
        now=now,
    )

    return {
        "report_code": report_code,
        "date": now.strftime("%Y-%m-%d"),
        "generated_at": now.isoformat(),
        "sources": sources_header,
        "new_chats": [str(n) for n in new_chat_names],
        "notices": list(notices),
        "today": today,
        "maybe_done": maybe_done_list,
        "collapsed": collapsed_list,
        "events": event_views,
        "skipped": skipped_views,
        "coverage": coverage,
        "report_index": report_index,
        # C3 new keys
        "chats": chats,
        "quiet_chats": quiet_chats,
        "mail_folders": mail_folders,
        "other_todos": other_todos,
    }


# ---------------------------------------------------------------------------
# C3 digest view helpers
# ---------------------------------------------------------------------------

_CHAT_TYPE_LABELS: dict[str, str] = {
    "group": "群組",
    "direct": "一對一",
}

_MAIL_CATEGORY_ORDER: dict[str, int] = {
    "reply": 0,
    "action": 1,
    "fyi": 2,
}

_DEFAULT_FOLDER_LABELS: dict[str, str] = {
    "inbox": "收件匣",
}


def _build_digest_views(
    *,
    ledger: list[dict],
    today: list[dict],
    report_index: dict[str, str],
    all_records: list[dict],
    all_chat_digests: list[dict],
    all_mail_digests: list[dict],
    folder_labels: dict[str, str],
    chat_history: dict[str, list[dict]],
    bundle_meta: dict,
    si: dict,
    now: datetime,
) -> tuple[list[dict], list[dict], list[dict], list[dict]]:
    """Build chats, quiet_chats, mail_folders, other_todos per C3."""

    # --- Build indexes from records ---
    # src_id → chat_ref for line_message records
    src_to_chat: dict[str, str] = {}
    # chat_ref → list of records
    chat_records: dict[str, list[dict]] = {}
    # chat_ref → chat info (name, type)
    chat_info: dict[str, dict[str, str]] = {}
    # src_id → mail record
    mail_records_by_id: dict[str, dict] = {}

    for rec in all_records:
        kind = rec.get("kind", "")
        if kind == "line_message":
            cref = rec.get("chat_ref", "")
            sid = rec.get("src_id", "")
            if cref and sid:
                src_to_chat[sid] = cref
                chat_records.setdefault(cref, []).append(rec)
            ut = rec.get("untrusted", {})
            if cref and cref not in chat_info:
                chat_info[cref] = {
                    "name": ut.get("chat_name", ""),
                    "chat_type": rec.get("chat_type", ""),
                }
        elif kind == "mail":
            sid = rec.get("src_id", "")
            if sid:
                mail_records_by_id[sid] = rec

    # src_id → mail src_id set (for attribution)
    mail_src_ids: set[str] = set(mail_records_by_id.keys())

    # --- Todo attribution ---
    # For each open todo, determine if it belongs to a chat or mail
    todo_to_chat: dict[str, str] = {}  # todo_id → chat_ref
    todo_to_mail: dict[str, str] = {}  # todo_id → mail src_id
    attributed_todo_ids: set[str] = set()

    for t in ledger:
        if t["status"] != "open":
            continue
        tid = t["id"]
        for sid in t.get("src_ids", []):
            if sid in src_to_chat:
                todo_to_chat[tid] = src_to_chat[sid]
                attributed_todo_ids.add(tid)
                break
            if sid in mail_src_ids:
                todo_to_mail[tid] = sid
                attributed_todo_ids.add(tid)
                break

    # --- Build chat_ref → digest lookup ---
    digest_by_chat: dict[str, dict] = {}
    for d in all_chat_digests:
        cref = d.get("chat_ref", "")
        if cref:
            digest_by_chat[cref] = d

    # --- Build needs_reply src_id set per chat ---
    nr_src_ids_by_chat: dict[str, set[str]] = {}
    for d in all_chat_digests:
        cref = d.get("chat_ref", "")
        nr_sids: set[str] = set()
        for nr in d.get("needs_reply", []):
            s = nr.get("src_id", "")
            if s:
                nr_sids.add(s)
        if cref:
            nr_src_ids_by_chat[cref] = nr_sids

    # --- Pulse window ---
    line_src = bundle_meta.get("sources", {}).get("line", {})
    pulse_from = line_src.get("from", "")
    pulse_to = line_src.get("to", "")

    # --- Build chat cards ---
    chats: list[dict] = []
    quiet_chats: list[dict] = []

    for d in all_chat_digests:
        cref = d.get("chat_ref", "")
        if not cref:
            continue
        info = chat_info.get(cref, {"name": "", "chat_type": ""})
        msgs = chat_records.get(cref, [])
        bullets = d.get("bullets", [])
        needs_reply = d.get("needs_reply", [])
        quiet = d.get("quiet", False)

        # Collect attributed todos for this chat
        chat_todos: list[dict] = []
        for tv in today:
            tid = tv["todo_id"]
            if todo_to_chat.get(tid) == cref:
                chat_todos.append(tv)

        # Determine state
        if needs_reply:
            card_state = "needs"
        elif chat_todos:
            card_state = "todo"
        else:
            card_state = "seen"

        # If quiet and no attributed todos → quiet_chats
        if quiet and not chat_todos and not needs_reply:
            quiet_chats.append({
                "name": info.get("name", ""),
                "kind_label": _CHAT_TYPE_LABELS.get(info.get("chat_type", ""), "對話"),
                "msg_count": len(msgs),
                "summary": "；".join(bullets) if bullets else "",
            })
            continue

        # Build needs_reply views (lookup who/when from records)
        nr_views: list[dict] = []
        for nr in needs_reply:
            nr_sid = nr.get("src_id", "")
            # Find the record to get who/when
            who = ""
            when = ""
            for rec in msgs:
                if rec.get("src_id") == nr_sid:
                    ut = rec.get("untrusted", {})
                    who = ut.get("sender", "")
                    when = _format_dt(rec.get("ts", ""))
                    break
            nr_views.append({
                "who": who,
                "when": when,
                "ask": nr.get("ask", ""),
            })

        # Pulse
        pulse = _build_pulse(
            msgs, pulse_from, pulse_to,
            nr_src_ids_by_chat.get(cref, set()),
        )

        # History
        hist_entries = chat_history.get(cref, [])
        history_views: list[dict] = []
        for h in hist_entries[:3]:
            history_views.append({
                "date": _format_dt(h.get("created_at", h.get("period_to", ""))),
                "bullets": h.get("bullets", []),
            })

        chats.append({
            "_chat_ref": cref,  # internal, for sorting/testing
            "name": info.get("name", ""),
            "kind_label": _CHAT_TYPE_LABELS.get(info.get("chat_type", ""), "對話"),
            "msg_count": len(msgs),
            "needs_reply": nr_views,
            "bullets": bullets,
            "todos": chat_todos,
            "history": history_views,
            "pulse": pulse,
            "state": card_state,
        })

    # US-2: chats with messages but no digest → missing-digest handling
    # If the chat has attributed todos → active card (state "todo"); else → quiet_chats
    digested_refs = {d.get("chat_ref", "") for d in all_chat_digests}
    for cref, msgs in chat_records.items():
        if cref in digested_refs:
            continue
        info = chat_info.get(cref, {"name": "", "chat_type": ""})
        n = len(msgs)

        # Check if this missing-digest chat has attributed todos
        missing_chat_todos: list[dict] = []
        for tv in today:
            tid = tv["todo_id"]
            if todo_to_chat.get(tid) == cref:
                missing_chat_todos.append(tv)

        if missing_chat_todos:
            # HIGH-1: missing-digest chat with todos → active card
            pulse = _build_pulse(msgs, pulse_from, pulse_to, set())
            chats.append({
                "_chat_ref": cref,
                "name": info.get("name", ""),
                "kind_label": _CHAT_TYPE_LABELS.get(info.get("chat_type", ""), "對話"),
                "msg_count": n,
                "needs_reply": [],
                "bullets": ["AI 這次沒有產生整理，請到 LINE 查看。"],
                "todos": missing_chat_todos,
                "history": [],
                "pulse": pulse,
                "state": "todo",
                "missing": True,
            })
        else:
            quiet_chats.append({
                "name": info.get("name", ""),
                "kind_label": _CHAT_TYPE_LABELS.get(info.get("chat_type", ""), "對話"),
                "msg_count": n,
                "summary": f"AI 這次沒有產生整理（{n} 則訊息），請到 LINE 查看。",
                "missing": True,
            })

    # Sort chats: needs_reply count desc → has todos → other
    chats.sort(key=lambda c: (-len(c["needs_reply"]), 0 if c["todos"] else 1))

    # --- Build mail folders ---
    # mail digest → mail record lookup
    mail_folder_map: dict[str, list[dict]] = {}
    for md in all_mail_digests:
        sid = md.get("src_id", "")
        rec = mail_records_by_id.get(sid)
        if not rec:
            continue
        folder_ref = rec.get("folder", "inbox")
        ut = rec.get("untrusted", {})

        # Collect attributed todos for this mail
        mail_todos: list[dict] = []
        for tv in today:
            tid = tv["todo_id"]
            if todo_to_mail.get(tid) == sid:
                mail_todos.append(tv)

        mail_view = {
            "from": ut.get("from", ""),
            "when": _format_dt(rec.get("received_at", "")),
            "subject": ut.get("subject", ""),
            "category": md.get("category", "fyi"),
            "summary": md.get("summary", ""),
            "todos": mail_todos,
        }
        mail_folder_map.setdefault(folder_ref, []).append(mail_view)

    mail_folders: list[dict] = []
    for folder_ref, mails in mail_folder_map.items():
        # Sort: reply → action → fyi
        mails.sort(key=lambda m: _MAIL_CATEGORY_ORDER.get(m["category"], 9))
        # Label lookup
        label = folder_labels.get(folder_ref, _DEFAULT_FOLDER_LABELS.get(folder_ref, folder_ref))
        mail_folders.append({
            "folder_label": label,
            "mails": mails,
        })

    # --- other_todos: attributed todos not emitted in any card/mail → other_todos ---
    # Collect all todo_ids that were actually placed in a chat card or mail row
    emitted_todo_ids: set[str] = set()
    for c in chats:
        for tv in c.get("todos", []):
            emitted_todo_ids.add(tv["todo_id"])
    for mf in mail_folders:
        for mail_row in mf.get("mails", []):
            for tv in mail_row.get("todos", []):
                emitted_todo_ids.add(tv["todo_id"])

    other_todos: list[dict] = []
    for tv in today:
        tid = tv["todo_id"]
        if tid not in emitted_todo_ids:
            other_todos.append(tv)

    return chats, quiet_chats, mail_folders, other_todos


def _build_pulse(
    msgs: list[dict],
    pulse_from: str,
    pulse_to: str,
    nr_src_ids: set[str],
) -> dict:
    """Build pulse visualization data for a chat card.

    ``dots[].x`` is a Python-computed float 0–800 from message timestamps.
    """
    # Parse window boundaries
    try:
        dt_from = datetime.fromisoformat(pulse_from)
        dt_to = datetime.fromisoformat(pulse_to)
    except (ValueError, TypeError):
        # Fall back to message range
        ts_list = sorted(m.get("ts", "") for m in msgs if m.get("ts"))
        if len(ts_list) >= 2:
            try:
                dt_from = datetime.fromisoformat(ts_list[0])
                dt_to = datetime.fromisoformat(ts_list[-1])
            except (ValueError, TypeError):
                return {"dots": [], "from": "", "to": ""}
        else:
            return {"dots": [], "from": pulse_from, "to": pulse_to}

    span = (dt_to - dt_from).total_seconds()
    if span <= 0:
        span = 1.0  # Avoid division by zero

    dots: list[dict] = []
    for m in msgs:
        ts_str = m.get("ts", "")
        try:
            dt_msg = datetime.fromisoformat(ts_str)
        except (ValueError, TypeError):
            continue
        offset = (dt_msg - dt_from).total_seconds()
        x = max(0.0, min(800.0, float(offset / span * 800.0)))
        sid = m.get("src_id", "")
        dots.append({
            "x": x,
            "reply": sid in nr_src_ids,
            "when": _format_dt(ts_str),
        })

    return {
        "dots": dots,
        "from": _format_dt(pulse_from) if pulse_from else "",
        "to": _format_dt(pulse_to) if pulse_to else "",
    }


def _todo_view(t: dict, no: int, si: dict, now: datetime) -> dict:
    """Build a single todo entry for the view."""
    src_labels: list[dict] = []
    for sid in t.get("src_ids", []):
        info = si.get(sid, {"label": sid, "href": None})
        src_labels.append({
            "label": info.get("label", sid),
            "href": _sanitize_href(info.get("href")),
        })

    return {
        "no": no,
        "todo_id": t["id"],
        "priority": t.get("priority", "P2"),
        "title": t.get("title", ""),
        "due": t.get("due"),
        "carry_days": _carry_days(t, now=now),
        "sources": src_labels,
    }


def _format_dt(iso: str) -> str:
    """Format ISO datetime to ``MM/DD HH:MM``."""
    try:
        dt = datetime.fromisoformat(iso)
        return dt.strftime("%m/%d %H:%M")
    except (ValueError, TypeError):
        return str(iso)


# ---------------------------------------------------------------------------
# render
# ---------------------------------------------------------------------------

def render(view: dict, fmt: Literal["md", "html"]) -> str:
    """Render view model to markdown or HTML string."""
    if fmt == "html":
        return _render_html(view)
    elif fmt == "md":
        return _render_md(view)
    else:
        raise ValueError(f"unsupported format: {fmt}")


def _render_html(view: dict) -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=True,
        undefined=jinja2.StrictUndefined,
    )
    template = env.get_template("agenda.html.j2")

    # Validate report code — only emit into data-report if it matches
    # the fixed pattern R-[A-Z0-9]{3}.
    raw_code = str(view.get("report_code", ""))
    report_attr = raw_code if _REPORT_CODE_RE.fullmatch(raw_code) else ""

    return template.render(
        v=view,
        digest_script=markupsafe.Markup(DIGEST_SCRIPT),
        csp_hash=DIGEST_SCRIPT_HASH,
        report_attr=report_attr,
    )


def _render_md(view: dict) -> str:
    env = jinja2.Environment(
        loader=jinja2.FileSystemLoader(str(_TEMPLATES_DIR)),
        autoescape=False,
        undefined=jinja2.StrictUndefined,
    )
    env.filters["md"] = _md_escape
    env.filters["md_href"] = _md_href
    template = env.get_template("agenda.md.j2")
    return template.render(v=view)
