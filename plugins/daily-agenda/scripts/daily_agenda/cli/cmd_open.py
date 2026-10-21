"""open — open the latest published agenda file with the OS default app.

Exists so a session that is already locked (it has seen raw third-party
text) can still open the report: the lock allows daily-agenda subcommands
but blocks `start`/`explorer`.  Only files recorded in our own publish
receipt are opened, never an arbitrary path.
"""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from daily_agenda import state
from daily_agenda.fsutil import read_json
from ._common import Deps, emit


def _latest_receipt(h: Path) -> dict[str, Any] | None:
    """Receipt of the newest generation that was published.

    Ledger operations (confirm / mark-done / …) commit a new generation that
    copies the outputs but not the receipt, so walk back from the current
    generation until a receipt is found.
    """
    gen = state.current_generation(h)
    if gen is None:
        return None
    for g in range(gen, 0, -1):
        data = read_json(state._gen_dir(h, g) / "published")
        if isinstance(data, dict) and data.get("targets"):
            return data
    return None


def handle(argv: list[str], deps: Deps) -> int:
    fmt = "html"
    for i, arg in enumerate(argv):
        if arg == "--format" and i + 1 < len(argv):
            fmt = argv[i + 1]
    if fmt not in ("html", "md"):
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "--format must be html or md"}, exit_code=2)

    receipt = _latest_receipt(deps.home)
    targets = (receipt or {}).get("targets", [])
    paths = [Path(t["path"]) for t in targets if isinstance(t, dict) and str(t.get("path", "")).lower().endswith(f".{fmt}")]
    if not paths:
        return emit({"ok": False, "code": "NO_OUTPUT_YET"})
    path = paths[-1]
    if not path.is_file():
        return emit({"ok": False, "code": "OUTPUT_MISSING", "path": str(path)})

    opener = getattr(os, "startfile", None)  # Windows only
    if opener is None:
        return emit({"ok": True, "code": None, "path": str(path), "opened": False,
                     "message": f"這個平台無法自動開啟，請自行打開：{path}"})
    try:
        opener(str(path))
    except OSError as exc:
        return emit({"ok": False, "code": "OPEN_FAILED", "path": str(path), "detail": str(exc)})
    return emit({"ok": True, "code": None, "path": str(path), "opened": True,
                 "message": f"已用預設程式開啟：{path}"})
