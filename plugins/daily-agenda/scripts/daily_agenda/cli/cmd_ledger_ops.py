"""show, mark-done, reject-done, dismiss, restore commands."""

from __future__ import annotations

import copy
from pathlib import Path

from daily_agenda import ledger, state
from daily_agenda.fence import fence_untrusted
from ._common import Deps, emit, require_consent


def _commit_ledger_change(deps: Deps, new_ledger: list[dict], new_state: dict | None = None) -> int:
    """Stage and commit a ledger-only change (no new outputs, copy from current gen)."""
    h = deps.home
    cur_gen = state.current_generation(h)
    if cur_gen is None:
        return emit({"ok": False, "code": "CONFIG_MISSING", "detail": "no generation exists"})

    cur_dir = h / "gen" / str(cur_gen)
    st = new_state if new_state is not None else state.load_state(h)

    # Copy outputs from current gen
    outputs: dict[str, str] = {}
    publish_targets: list[dict] = []
    for fmt in ("html", "md"):
        out_file = cur_dir / f"agenda.{fmt}"
        if out_file.exists():
            outputs[fmt] = out_file.read_text(encoding="utf-8")

    from daily_agenda.fsutil import read_json
    pub = read_json(cur_dir / "publish.json")
    if pub:
        publish_targets = pub.get("targets", [])

    staging = state.stage_generation(
        h, state=st, ledger=new_ledger, outputs=outputs, publish_targets=publish_targets,
    )
    state.commit_generation(h, staging)
    return -1  # sentinel: success, caller emits


# ---------------------------------------------------------------------------
# show (★ command)
# ---------------------------------------------------------------------------

def handle_show(argv: list[str], deps: Deps) -> int:
    cfg = state.load_config(deps.home)
    rc = require_consent(cfg)
    if rc is not None:
        return rc

    st = state.load_state(deps.home)
    ldg = state.load_ledger(deps.home)

    report_code: str | None = None
    items: list[int] = []
    todo_ids: list[str] = []

    for i, arg in enumerate(argv):
        if arg == "--report" and i + 1 < len(argv):
            report_code = argv[i + 1]
        elif arg == "--items" and i + 1 < len(argv):
            try:
                items = [int(x) for x in argv[i + 1].split(",") if x.strip()]
            except ValueError:
                return emit({"ok": False, "code": "ARG_ERROR",
                              "detail": "--items must be comma-separated integers"}, exit_code=2)
        elif arg == "--todo" and i + 1 < len(argv):
            todo_ids = [x.strip() for x in argv[i + 1].split(",") if x.strip()]

    results: list[dict] = []
    report_index = st.get("report_index", {})

    if items and report_code:
        # Fail-closed: report code must match current (same as mark-done)
        current_code = st.get("report_code")
        if not current_code or report_code != current_code:
            return emit({"ok": False, "code": "STALE_REPORT"})
        for n in items:
            tid = report_index.get(str(n))
            if tid:
                todo_ids.append(tid)

    for tid in todo_ids:
        for t in ldg:
            if t["id"] == tid:
                # F4c: fence model-derived titles (originally from third-party)
                results.append({
                    "todo_id": t["id"],
                    "title": fence_untrusted(t.get("title", "")),
                    "priority": t.get("priority", ""),
                    "status": t["status"],
                })
                break

    return emit({"ok": True, "code": None, "items": results})


# ---------------------------------------------------------------------------
# mark-done
# ---------------------------------------------------------------------------

def handle_mark_done(argv: list[str], deps: Deps) -> int:
    st = state.load_state(deps.home)
    ldg = state.load_ledger(deps.home)

    report_code: str | None = None
    items: list[int] = []
    todo_ids: list[str] = []

    for i, arg in enumerate(argv):
        if arg == "--report" and i + 1 < len(argv):
            report_code = argv[i + 1]
        elif arg == "--items" and i + 1 < len(argv):
            try:
                items = [int(x) for x in argv[i + 1].split(",") if x.strip()]
            except ValueError:
                return emit({"ok": False, "code": "ARG_ERROR",
                              "detail": "--items must be comma-separated integers"}, exit_code=2)
        elif arg == "--todo" and i + 1 < len(argv):
            todo_ids = [x.strip() for x in argv[i + 1].split(",") if x.strip()]

    now = deps.now_factory()

    try:
        new_ldg, new_st = ledger.mark_done(
            ldg, st, report_code=report_code, numbers=items, todo_ids=todo_ids, now=now,
        )
    except ledger.StaleReport:
        return emit({"ok": False, "code": "STALE_REPORT"})
    except (ledger.IllegalTransition, KeyError) as exc:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": str(exc)}, exit_code=1)

    rc = _commit_ledger_change(deps, new_ldg, new_st)
    if rc != -1:
        return rc

    done_ids = list(set(todo_ids) | {st.get("report_index", {}).get(str(n), "") for n in items})
    return emit({"ok": True, "code": None, "done": [tid for tid in done_ids if tid]})


# ---------------------------------------------------------------------------
# reject-done (maybe_done → open)
# ---------------------------------------------------------------------------

def handle_reject_done(argv: list[str], deps: Deps) -> int:
    if not argv or argv[0] != "--todo":
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: reject-done --todo T-xxxx"}, exit_code=2)

    todo_id = argv[1] if len(argv) > 1 else ""
    if not todo_id:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "missing todo ID"}, exit_code=2)

    ldg = state.load_ledger(deps.home)
    now = deps.now_factory()

    new_ldg = copy.deepcopy(ldg)
    found = False
    for i, t in enumerate(new_ldg):
        if t["id"] == todo_id:
            if t["status"] != "maybe_done":
                return emit({"ok": False, "code": "ARG_ERROR",
                              "detail": f"{todo_id} status is {t['status']}, expected maybe_done"})
            from daily_agenda.ledger import _change_status
            new_ldg[i] = _change_status(
                t, "open", by="user", now=now,
                extra={"maybe_done_since": None, "maybe_done_reason": None},
            )
            found = True
            break

    if not found:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": f"todo {todo_id} not found"})

    rc = _commit_ledger_change(deps, new_ldg)
    if rc != -1:
        return rc
    return emit({"ok": True, "code": None, "todo_id": todo_id, "new_status": "open"})


# ---------------------------------------------------------------------------
# dismiss (open → dismissed)
# ---------------------------------------------------------------------------

def handle_dismiss(argv: list[str], deps: Deps) -> int:
    if not argv or argv[0] != "--todo":
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: dismiss --todo T-xxxx"}, exit_code=2)

    todo_id = argv[1] if len(argv) > 1 else ""
    if not todo_id:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "missing todo ID"}, exit_code=2)

    ldg = state.load_ledger(deps.home)
    now = deps.now_factory()

    try:
        new_ldg = ledger.dismiss(ldg, todo_id, now=now)
    except (ledger.IllegalTransition, KeyError) as exc:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": str(exc)})

    rc = _commit_ledger_change(deps, new_ldg)
    if rc != -1:
        return rc
    return emit({"ok": True, "code": None, "todo_id": todo_id, "new_status": "dismissed"})


# ---------------------------------------------------------------------------
# restore T-xxxx (collapsed → open)
# ---------------------------------------------------------------------------

def handle_restore(argv: list[str], deps: Deps) -> int:
    # restore T-xxxx (positional, no flag)
    todo_id = argv[0] if argv else ""
    if not todo_id or not todo_id.startswith("T-"):
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: restore T-xxxx"}, exit_code=2)

    ldg = state.load_ledger(deps.home)
    now = deps.now_factory()

    try:
        new_ldg = ledger.restore(ldg, todo_id, now=now)
    except (ledger.IllegalTransition, KeyError) as exc:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": str(exc)})

    rc = _commit_ledger_change(deps, new_ldg)
    if rc != -1:
        return rc
    return emit({"ok": True, "code": None, "todo_id": todo_id, "new_status": "open"})
