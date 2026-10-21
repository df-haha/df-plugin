"""probe, outlook-preview, chats list, exclude, ctx index commands."""

from __future__ import annotations

import json
import re
from datetime import datetime, timedelta
from typing import Any
from zoneinfo import ZoneInfo

from daily_agenda import collect, ctx_index, history, state
from daily_agenda.fence import fence_untrusted
from daily_agenda.fsutil import atomic_write_json
from ._common import Deps, emit, require_consent

# Numbered listing written by `chats list --brief`; `exclude` resolves numbers
# against it and uses it to tell "chats the user was shown" from "chats the
# user never saw" (those keep their previous exclusion state).
_LISTING_FILE = "chat-listing.json"
_LISTING_MAX_AGE = timedelta(hours=12)
_PREVIEW_DAYS = 2  # today + tomorrow, by local calendar date
_PRESERVED_TAG = " [已排除，近期無訊息]"


# ---------------------------------------------------------------------------
# probe
# ---------------------------------------------------------------------------

def handle_probe(argv: list[str], deps: Deps) -> int:
    cfg = state.load_config(deps.home)
    rc = require_consent(cfg)
    if rc is not None:
        return rc

    line_src = None
    if deps.line_source_factory and cfg.get("line", {}).get("enabled"):
        try:
            line_src = deps.line_source_factory(
                main_database=cfg["line"].get("main_database", ""),
                trusted_builds_path=str(deps.home / "trusted_builds.json"),
            )
        except Exception:
            pass

    graph_src = None
    if deps.graph_factory:
        try:
            graph_src = deps.graph_factory()
        except Exception as exc:
            code = getattr(exc, "code", "DF_GRAPH_NOT_REGISTERED")
            statuses_early: dict[str, Any] = {}
            if line_src:
                statuses_early["line"] = line_src.probe()
            statuses_early["mail"] = {"ok": False, "code": code, **_start_detail(exc)}
            statuses_early["calendar"] = {"ok": False, "code": code}
            ok = all(s.get("ok", False) for s in statuses_early.values())
            return emit({"ok": ok, "code": None, "sources": statuses_early})

    statuses = collect.probe(cfg, line=line_src, graph=graph_src)
    ok = all(s.get("ok", False) for s in statuses.values())
    return emit({"ok": ok, "code": None, "sources": statuses})


def _start_detail(exc: Exception) -> dict[str, Any]:
    """Local df-graph stderr tail, only for start failures (it is our own
    subprocess's output, not third-party content — fenced but non-tainting)."""
    if getattr(exc, "code", "") != "DF_GRAPH_START_FAILED":
        return {}
    detail = getattr(exc, "detail", "") or ""
    return {"detail": fence_untrusted(detail[-2000:], raw=False)} if detail else {}


# ---------------------------------------------------------------------------
# outlook-preview (★ command — shows content after consent)
# ---------------------------------------------------------------------------

def handle_outlook_preview(argv: list[str], deps: Deps) -> int:
    cfg = state.load_config(deps.home)
    rc = require_consent(cfg)
    if rc is not None:
        return rc

    if not deps.graph_factory:
        return emit({"ok": False, "code": "DF_GRAPH_NOT_REGISTERED"})

    try:
        graph = deps.graph_factory()
    except Exception as exc:
        code = getattr(exc, "code", "DF_GRAPH_NOT_REGISTERED")
        return emit({"ok": False, "code": code, **_start_detail(exc)})

    try:
        mails = graph.mail_list(days=3, folder="inbox")[:3]
        mail_preview = [
            {"from": fence_untrusted(m.get("from", ""), raw=True),
             "subject": fence_untrusted(m.get("subject", ""), raw=True)}
            for m in mails
        ]
    except Exception as exc:
        code = getattr(exc, "code", "GRAPH_FAILED")
        return emit({"ok": False, "code": code})

    tz_name = cfg.get("tz") or "Asia/Taipei"
    try:
        today = deps.now_factory().astimezone(ZoneInfo(tz_name)).date()
    except Exception:
        tz_name, today = "Asia/Taipei", deps.now_factory().date()
    day_from, day_to = today.isoformat(), (today + timedelta(days=_PREVIEW_DAYS - 1)).isoformat()
    try:
        # df-graph's window is now±N (rolling), so ask for a day more on each
        # side and keep only events whose local start date is today/tomorrow.
        events = graph.calendar_list(days_ahead=_PREVIEW_DAYS, days_back=1, tz=tz_name)
        event_preview = [
            {"subject": fence_untrusted(e.get("subject", ""), raw=True),
             "start": e.get("start", ""), "end": e.get("end", "")}
            for e in events
            if day_from <= str(e.get("start", ""))[:10] <= day_to
        ]
    except Exception:
        event_preview = []

    # Enable mail after successful preview
    cfg.setdefault("mail", {})["enabled"] = True
    state.save_config(deps.home, cfg)

    return emit({
        "ok": True, "code": None, "mail": mail_preview, "events": event_preview,
        "events_tz": tz_name,
        "events_range": {"from": day_from, "to": day_to},
    })


# ---------------------------------------------------------------------------
# chats list --days N (★ command)
# ---------------------------------------------------------------------------

def handle_chats(argv: list[str], deps: Deps) -> int:
    if not argv or argv[0] not in ("list", "new"):
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: chats list --days N | chats new"}, exit_code=2)

    if argv[0] == "new":
        return _handle_chats_new(deps)


    cfg = state.load_config(deps.home)
    rc = require_consent(cfg)
    if rc is not None:
        return rc

    days = 30
    for i, arg in enumerate(argv):
        if arg == "--days" and i + 1 < len(argv):
            try:
                days = int(argv[i + 1])
            except ValueError:
                return emit({"ok": False, "code": "ARG_ERROR", "detail": "--days must be an integer"}, exit_code=2)

    if not deps.line_source_factory:
        return emit({"ok": False, "code": "LINE_RUNTIME_MISSING"})

    try:
        line_src = deps.line_source_factory(
            main_database=cfg.get("line", {}).get("main_database", ""),
            trusted_builds_path=str(deps.home / "trusted_builds.json"),
        )
    except Exception as exc:
        return emit({"ok": False, "code": getattr(exc, "code", "LINE_RUNTIME_MISSING")})

    chats = collect.list_chats(cfg, line_src, days=days)

    # Mark excluded
    excluded_refs = {c["ref"] for c in cfg.get("line", {}).get("excluded_chats", [])}
    if "--brief" in argv:
        return _emit_brief_listing(deps, chats, cfg.get("line", {}).get("excluded_chats", []), days=days)
    result_chats = []
    for c in chats:
        result_chats.append({
            "ref": c["ref"],
            "name": fence_untrusted(c["name"], raw=True),
            "type": c["type"],
            "last_at": c.get("last_message_at", ""),
            "excluded": c["ref"] in excluded_refs,
        })

    return emit({"ok": True, "code": None, "chats": result_chats})


def _emit_brief_listing(deps: Deps, chats: list[dict[str, Any]], excluded: list[dict[str, Any]], *, days: int) -> int:
    """One fenced, numbered block (no refs) + persist n→ref so `exclude` can take numbers.

    The full listing is ~250 bytes per chat (per-name fence, ref, timestamp); with
    170+ chats that exceeds what the tool-result view shows, so the model ends up
    reading the raw file.  Brief mode keeps the whole list under a single fence.

    Chats excluded earlier but silent in this window are appended as extra rows
    (``preserved``) so they still get a number and can be un-excluded; `exclude`
    keeps them excluded unless the user names their number explicitly.
    """
    excluded_refs = {c["ref"] for c in excluded}
    items: list[dict[str, Any]] = []
    lines: list[str] = []
    for c in chats:
        n = len(items) + 1
        items.append({"n": n, "ref": c["ref"], "name": c["name"], "preserved": False})
        date = (c.get("last_message_at") or "")[:10]
        tag = " [已排除]" if c["ref"] in excluded_refs else ""
        lines.append(f"{n}. {_one_line(c['name'])} ({_one_line(c['type'])}, {date}){tag}")
    active = {c["ref"] for c in chats}
    for c in excluded:
        if c["ref"] in active:
            continue
        n = len(items) + 1
        items.append({"n": n, "ref": c["ref"], "name": c.get("name", ""), "preserved": True})
        lines.append(f"{n}. {_one_line(c.get('name') or '(名稱未知)')}{_PRESERVED_TAG}")
    atomic_write_json(deps.home / _LISTING_FILE, {
        "days": days, "listed_at": deps.now_factory().isoformat(), "items": items,
    })
    return emit({"ok": True, "code": None, "count": len(chats), "preserved_count": len(items) - len(chats),
                 "listing": fence_untrusted("\n".join(lines), raw=True)})


_CTRL_RE = re.compile(r"[\x00-\x1f\x7f\u2028\u2029\u0085]+")


def _one_line(text: str) -> str:
    """Flatten third-party text so it cannot forge extra listing rows or the
    `[已排除]` marker (numbering/status must come from us, not the name)."""
    flat = _CTRL_RE.sub(" ", str(text)).strip()
    return flat.replace("[已排除", "［已排除")  # fullwidth bracket: visible, but not our marker


def _load_listing(deps: Deps) -> dict[str, Any] | None:
    """Latest `chats list --brief` listing, or None if missing/corrupt/stale."""
    try:
        data = json.loads((deps.home / _LISTING_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
        return None
    try:
        listed_at = datetime.fromisoformat(str(data.get("listed_at")))
        if deps.now_factory() - listed_at > _LISTING_MAX_AGE:
            return None
    except (TypeError, ValueError):
        return None
    return data


# ---------------------------------------------------------------------------
# chats new — list new chats from the latest run (fenced names)
# ---------------------------------------------------------------------------

def _handle_chats_new(deps: Deps) -> int:
    """Return fenced names of chats first seen in the latest run."""
    st = state.load_state(deps.home)
    last_run = st.get("last_run", {})
    new_chats = last_run.get("new_chats", [])

    if not new_chats:
        return emit({"ok": True, "code": None, "chats": [], "count": 0})

    result_chats = []
    for c in new_chats:
        result_chats.append({
            "ref": c.get("ref", ""),
            "name": fence_untrusted(c.get("name", ""), raw=True),
        })

    return emit({"ok": True, "code": None, "chats": result_chats, "count": len(result_chats)})


# ---------------------------------------------------------------------------
# exclude --set REF...|--add REF|--remove REF
# ---------------------------------------------------------------------------

def _resolve_refs(tokens: list[str], listing: dict[str, Any] | None) -> tuple[list[str], str | None]:
    """Map `exclude` arguments to chat refs.  Plain ASCII digits are listing numbers."""
    by_n = {int(i["n"]): i["ref"] for i in (listing or {}).get("items", [])}
    refs: list[str] = []
    for tok in tokens:
        if tok.isascii() and tok.isdigit():
            if listing is None:
                return [], "numbers need a fresh listing: run `chats list --brief` first"
            ref = by_n.get(int(tok))
            if ref is None:
                return [], f"number {tok} is not in the latest listing (1..{len(by_n)})"
            refs.append(ref)
        else:
            refs.append(tok)
    return list(dict.fromkeys(refs)), None


def handle_exclude(argv: list[str], deps: Deps) -> int:
    cfg = state.load_config(deps.home)
    st = state.load_state(deps.home)
    listing = _load_listing(deps)
    items = (listing or {}).get("items", [])
    # "active" = chats the user actually saw with messages in the window;
    # preserved rows are old exclusions appended for reference only.
    active_refs = [i["ref"] for i in items if not i.get("preserved")]  # listing order
    names: dict[str, str] = {c["ref"]: c.get("name", "") for c in st.get("known_chats", [])}
    names.update({i["ref"]: i.get("name", "") for i in items if i.get("name")})

    existing = cfg.get("line", {}).get("excluded_chats", [])
    existing_map = {c["ref"]: c for c in existing}

    def _entry(ref: str) -> dict[str, Any]:
        return existing_map.get(ref) or {"ref": ref, "name": names.get(ref, "")}

    def _result(**extra: Any) -> int:
        state.save_config(deps.home, cfg)
        count = len(cfg.get("line", {}).get("excluded_chats", []))
        out = {"ok": True, "code": None, "excluded_count": count, "kept_unlisted": 0}
        if listing:
            out["listing"] = {"days": listing.get("days"), "listed_at": listing.get("listed_at")}
        out.update(extra)
        return emit(out)

    if "--set" in argv or "--keep" in argv:
        mode = "--set" if "--set" in argv else "--keep"
        refs, err = _resolve_refs(argv[argv.index(mode) + 1:], listing)
        if err:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": err}, exit_code=2)
        if mode == "--keep":
            if listing is None:
                return emit({"ok": False, "code": "ARG_ERROR",
                              "detail": "--keep needs a fresh listing: run `chats list --brief` first"}, exit_code=2)
            keep = set(refs)
            refs = [r for r in active_refs if r not in keep]
        else:
            keep = set()
        # Exclusions the user was not shown as active chats keep their state —
        # unless the user explicitly asked to keep (read) one of them.
        unlisted = [c for c in existing if c["ref"] not in active_refs and c["ref"] not in keep] if listing else []
        unlisted_refs = {c["ref"] for c in unlisted}
        new_excluded = unlisted + [_entry(r) for r in refs if r not in unlisted_refs]
        cfg.setdefault("line", {})["excluded_chats"] = new_excluded
        # C4: delete history rows for every excluded chat
        for ref in refs:
            try:
                history.delete_by_chat_ref(deps.home, ref)
            except Exception:
                pass  # Non-fatal
        return _result(kept_unlisted=len(unlisted))

    if "--add" in argv:
        refs, err = _resolve_refs(argv[argv.index("--add") + 1:], listing)
        if err:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": err}, exit_code=2)
        if not refs:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": "--add requires a ref"}, exit_code=2)
        for ref in refs:
            if ref not in existing_map:
                existing.append(_entry(ref))
                existing_map[ref] = existing[-1]
            # C4: delete history rows for the excluded chat
            try:
                history.delete_by_chat_ref(deps.home, ref)
            except Exception:
                pass  # Non-fatal
        cfg.setdefault("line", {})["excluded_chats"] = existing
        return _result()

    if "--remove" in argv:
        refs, err = _resolve_refs(argv[argv.index("--remove") + 1:], listing)
        if err:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": err}, exit_code=2)
        if not refs:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": "--remove requires a ref"}, exit_code=2)
        cfg.setdefault("line", {})["excluded_chats"] = [c for c in existing if c["ref"] not in set(refs)]
        return _result()

    return emit({"ok": False, "code": "ARG_ERROR",
                  "detail": "usage: exclude --set REF|N...|--keep REF|N...|--add REF|N...|--remove REF|N..."},
                exit_code=2)


# ---------------------------------------------------------------------------
# ctx index --dir D|--skip
# ---------------------------------------------------------------------------

def handle_ctx(argv: list[str], deps: Deps) -> int:
    if not argv or argv[0] != "index":
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: ctx index --dir D|--skip"}, exit_code=2)

    if "--skip" in argv:
        cfg = state.load_config(deps.home)
        cfg.setdefault("ctx", {})["dir"] = None
        state.save_config(deps.home, cfg)
        return emit({"ok": True, "code": None, "files": 0})

    ctx_dir: str | None = None
    for i, arg in enumerate(argv):
        if arg == "--dir" and i + 1 < len(argv):
            ctx_dir = argv[i + 1]

    if not ctx_dir:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "--dir or --skip required"}, exit_code=2)

    from pathlib import Path
    out_path = deps.home / "ctx-index.jsonl"
    count = ctx_index.write_index(Path(ctx_dir), out_path)

    cfg = state.load_config(deps.home)
    cfg.setdefault("ctx", {})["dir"] = ctx_dir
    state.save_config(deps.home, cfg)

    return emit({"ok": True, "code": None, "files": count})
