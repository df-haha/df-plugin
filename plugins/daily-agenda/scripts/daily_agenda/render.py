"""View model builder and safe renderer (§5 contract)."""

from __future__ import annotations

import re
from datetime import datetime
from pathlib import Path
from typing import Any, Literal

import jinja2

from daily_agenda.ledger import carry_days as _carry_days
from daily_agenda.messages import humanize


# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

_TEMPLATES_DIR = Path(__file__).resolve().parents[2] / "templates"

_SAFE_HREF_PREFIXES = ("https://", "mailto:")

# Characters that could form a control sequence if left in a URL.
_CONTROL_CHAR_RE = re.compile(r"[\x00-\x1f\x7f]")

# Markdown meta-characters to escape (backslash first so we don't double-escape).
_MD_ESCAPE_CHARS = "\\`*_[]()<!>#|"

# Source status → Chinese label.
_STATUS_LABELS: dict[str, str] = {
    "ok": "已讀取",
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
) -> dict:
    """Build the view model for rendering.

    *src_index*: ``{src_id: {"label": str, "href": str|None}}`` for source
    annotations. Falls back to src_id as label if missing.

    Returns a dict ready for ``render()``, including ``report_index``.
    """
    si = src_index or {}
    skipped_items = list(skipped) if skipped else []

    # --- sources header ---
    sources_header: list[dict] = []
    for name, key in [("LINE", "line"), ("Outlook", "mail"), ("行事曆", "calendar")]:
        src_status = bundle_meta.get("sources", {}).get(key, {})
        status = src_status.get("status", "skipped")
        code = src_status.get("code")

        if status == "failed":
            message = humanize(code) if code else "讀取失敗"
        else:
            message = _STATUS_LABELS.get(status, status)

        coverage = ""
        if status == "ok":
            f = src_status.get("from", "")
            t = src_status.get("to", "")
            if f and t:
                coverage = f"{_format_dt(f)} – {_format_dt(t)}"

        sources_header.append({
            "name": name, "status": status, "message": message, "coverage": coverage,
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
    return template.render(v=view)


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
