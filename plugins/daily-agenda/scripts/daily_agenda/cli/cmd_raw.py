"""raw show command — show original messages for a todo."""

from __future__ import annotations

from daily_agenda import state
from daily_agenda.scan_raw import resolve_raw_for_todo
from ._common import Deps, emit, require_consent


def handle(argv: list[str], deps: Deps) -> int:
    if not argv or argv[0] != "show":
        return emit({"ok": False, "code": "ARG_ERROR",
                      "detail": "usage: raw show --todo <id>"},
                    exit_code=2)

    cfg = state.load_config(deps.home)
    rc = require_consent(cfg)
    if rc is not None:
        return rc

    # Parse --todo
    todo_id: str | None = None
    for i, arg in enumerate(argv):
        if arg == "--todo" and i + 1 < len(argv):
            todo_id = argv[i + 1]

    if not todo_id:
        return emit({"ok": False, "code": "ARG_ERROR",
                      "detail": "usage: raw show --todo <id>"}, exit_code=2)

    ledger = state.load_ledger(deps.home)
    runs_dir = deps.home / "runs"

    result = resolve_raw_for_todo(todo_id, ledger, runs_dir)
    return emit(result)
