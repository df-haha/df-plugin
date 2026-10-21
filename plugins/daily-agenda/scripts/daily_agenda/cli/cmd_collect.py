"""probe, outlook-preview, chats list, exclude, ctx index commands."""

from __future__ import annotations

import json
from datetime import timedelta
from typing import Any

from daily_agenda import collect, ctx_index, history, state
from daily_agenda.fence import fence_untrusted
from daily_agenda.fsutil import atomic_write_json
from ._common import Deps, emit, require_consent

# Numbered listing written by `chats list --brief`; `exclude` resolves numbers
# against it and uses it to tell "chats the user was shown" from "chats the
# user never saw" (those keep their previous exclusion state).
_LISTING_FILE = "chat-listing.json"
_PREVIEW_DAYS_AHEAD = 1


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
            statuses_early["mail"] = {"ok": False, "code": code}
            statuses_early["calendar"] = {"ok": False, "code": code}
            ok = all(s.get("ok", False) for s in statuses_early.values())
            return emit({"ok": ok, "code": None, "sources": statuses_early})

    statuses = collect.probe(cfg, line=line_src, graph=graph_src)
    ok = all(s.get("ok", False) for s in statuses.values())
    return emit({"ok": ok, "code": None, "sources": statuses})


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
        return emit({"ok": False, "code": code})

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

    try:
        events = graph.calendar_list(days_ahead=_PREVIEW_DAYS_AHEAD, days_back=0)
        event_preview = [
            {"subject": fence_untrusted(e.get("subject", ""), raw=True),
             "start": e.get("start", ""), "end": e.get("end", "")}
            for e in events
        ]
    except Exception:
        event_preview = []

    # Enable mail after successful preview
    cfg.setdefault("mail", {})["enabled"] = True
    state.save_config(deps.home, cfg)

    today = deps.now_factory().date()
    return emit({
        "ok": True, "code": None, "mail": mail_preview, "events": event_preview,
        "events_tz": cfg.get("tz", "Asia/Taipei"),
        "events_range": {"from": today.isoformat(),
                         "to": (today + timedelta(days=_PREVIEW_DAYS_AHEAD)).isoformat()},
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
        return _emit_brief_listing(deps, chats, excluded_refs, days=days)
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


def _emit_brief_listing(deps: Deps, chats: list[dict[str, Any]], excluded_refs: set[str], *, days: int) -> int:
    """One fenced, numbered block (no refs) + persist n→ref so `exclude` can take numbers.

    The full listing is ~250 bytes per chat (per-name fence, ref, timestamp); with
    170+ chats that exceeds what the tool-result view shows, so the model ends up
    reading the raw file.  Brief mode keeps the whole list under a single fence.
    """
    items = []
    lines = []
    for n, c in enumerate(chats, start=1):
        items.append({"n": n, "ref": c["ref"], "name": c["name"]})
        date = (c.get("last_message_at") or "")[:10]
        tag = " [已排除]" if c["ref"] in excluded_refs else ""
        lines.append(f"{n}. {c['name']} ({c['type']}, {date}){tag}")
    atomic_write_json(deps.home / _LISTING_FILE, {
        "days": days, "listed_at": deps.now_factory().isoformat(), "items": items,
    })
    return emit({"ok": True, "code": None, "count": len(items),
                 "listing": fence_untrusted("\n".join(lines), raw=True)})


def _load_listing(deps: Deps) -> dict[str, Any] | None:
    try:
        data = json.loads((deps.home / _LISTING_FILE).read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    if not isinstance(data, dict) or not isinstance(data.get("items"), list):
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
    """Map `exclude` arguments to chat refs.  Plain digits are listing numbers."""
    by_n = {int(i["n"]): i["ref"] for i in (listing or {}).get("items", [])}
    refs: list[str] = []
    for tok in tokens:
        if tok.isdigit():
            if listing is None:
                return [], "numbers need a listing: run `chats list --brief` first"
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
    listed_refs = [i["ref"] for i in (listing or {}).get("items", [])]
    names: dict[str, str] = {c["ref"]: c.get("name", "") for c in st.get("known_chats", [])}
    names.update({i["ref"]: i.get("name", "") for i in (listing or {}).get("items", [])})

    existing = cfg.get("line", {}).get("excluded_chats", [])
    existing_map = {c["ref"]: c for c in existing}

    def _entry(ref: str) -> dict[str, Any]:
        return existing_map.get(ref) or {"ref": ref, "name": names.get(ref, "")}

    kept_unlisted = 0
    if "--set" in argv or "--keep" in argv:
        mode = "--set" if "--set" in argv else "--keep"
        refs, err = _resolve_refs(argv[argv.index(mode) + 1:], listing)
        if err:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": err}, exit_code=2)
        if mode == "--keep":
            if listing is None:
                return emit({"ok": False, "code": "ARG_ERROR",
                              "detail": "--keep needs a listing: run `chats list --brief` first"}, exit_code=2)
            refs = [r for r in listed_refs if r not in set(refs)]
        # Chats the latest listing never showed keep their previous state: the
        # user could not have un-excluded something they did not see.
        unlisted = [c for c in existing if c["ref"] not in set(listed_refs)] if listing else []
        kept_unlisted = len(unlisted)
        new_excluded = unlisted + [_entry(r) for r in refs if r not in {c["ref"] for c in unlisted}]
        cfg.setdefault("line", {})["excluded_chats"] = new_excluded
        # C4: delete history rows for every excluded chat
        for ref in refs:
            try:
                history.delete_by_chat_ref(deps.home, ref)
            except Exception:
                pass  # Non-fatal
    elif "--add" in argv:
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
    elif "--remove" in argv:
        refs, err = _resolve_refs(argv[argv.index("--remove") + 1:], listing)
        if err:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": err}, exit_code=2)
        if not refs:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": "--remove requires a ref"}, exit_code=2)
        cfg.setdefault("line", {})["excluded_chats"] = [c for c in existing if c["ref"] not in set(refs)]
    else:
        return emit({"ok": False, "code": "ARG_ERROR",
                      "detail": "usage: exclude --set REF|N...|--keep REF|N...|--add REF|N...|--remove REF|N..."},
                    exit_code=2)

    state.save_config(deps.home, cfg)
    count = len(cfg.get("line", {}).get("excluded_chats", []))
    return emit({"ok": True, "code": None, "excluded_count": count, "kept_unlisted": kept_unlisted})


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
