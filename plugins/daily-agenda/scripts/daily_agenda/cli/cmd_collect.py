"""probe, outlook-preview, chats list, exclude, ctx index commands."""

from __future__ import annotations

import json
from typing import Any

from daily_agenda import collect, ctx_index, state
from daily_agenda.fence import fence_untrusted
from ._common import Deps, emit, require_consent


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
        events = graph.calendar_list(days_ahead=1, days_back=0)
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

    return emit({"ok": True, "code": None, "mail": mail_preview, "events": event_preview})


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

def handle_exclude(argv: list[str], deps: Deps) -> int:
    cfg = state.load_config(deps.home)
    st = state.load_state(deps.home)
    known_chats = {c["ref"]: c.get("name", "") for c in st.get("known_chats", [])}

    existing = cfg.get("line", {}).get("excluded_chats", [])
    existing_map = {c["ref"]: c for c in existing}

    if "--set" in argv:
        idx = argv.index("--set")
        refs = argv[idx + 1:]
        new_excluded = [{"ref": r, "name": known_chats.get(r, "")} for r in refs]
        cfg.setdefault("line", {})["excluded_chats"] = new_excluded
    elif "--add" in argv:
        idx = argv.index("--add")
        if idx + 1 >= len(argv):
            return emit({"ok": False, "code": "ARG_ERROR", "detail": "--add requires a ref"}, exit_code=2)
        ref = argv[idx + 1]
        if ref not in existing_map:
            existing.append({"ref": ref, "name": known_chats.get(ref, "")})
            cfg.setdefault("line", {})["excluded_chats"] = existing
    elif "--remove" in argv:
        idx = argv.index("--remove")
        if idx + 1 >= len(argv):
            return emit({"ok": False, "code": "ARG_ERROR", "detail": "--remove requires a ref"}, exit_code=2)
        ref = argv[idx + 1]
        cfg.setdefault("line", {})["excluded_chats"] = [c for c in existing if c["ref"] != ref]
    else:
        return emit({"ok": False, "code": "ARG_ERROR",
                      "detail": "usage: exclude --set REF...|--add REF|--remove REF"}, exit_code=2)

    state.save_config(deps.home, cfg)
    count = len(cfg.get("line", {}).get("excluded_chats", []))
    return emit({"ok": True, "code": None, "excluded_count": count})


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
