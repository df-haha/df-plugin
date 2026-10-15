"""schedule install --time HH:MM / remove / status"""

from __future__ import annotations

import shutil
import subprocess

from daily_agenda import schedule, state
from ._common import Deps, emit


def handle(argv: list[str], deps: Deps) -> int:
    if not argv:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: schedule install|remove|status"}, exit_code=2)

    sub = argv[0]
    runner = deps.runner or subprocess.run
    which = deps.which or shutil.which

    if sub == "install":
        time_val: str | None = None
        for i, arg in enumerate(argv):
            if arg == "--time" and i + 1 < len(argv):
                time_val = argv[i + 1]

        if not time_val:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: schedule install --time HH:MM"}, exit_code=2)

        try:
            schedule.validate_time(time_val)
        except ValueError as exc:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": str(exc)}, exit_code=2)

        uv = which("uv") or "uv"
        result = schedule.install(
            deps.home, time=time_val, plugin_root=deps.plugin_root, uv=uv, runner=runner,
        )
        return emit({"ok": result["ok"], "code": result.get("code"),
                      "time": result.get("time"), "launcher": result.get("launcher")})

    elif sub == "remove":
        result = schedule.remove(deps.home, runner=runner)
        return emit({"ok": result["ok"], "code": None})

    elif sub == "status":
        result = schedule.status(runner=runner)
        return emit({"ok": True, "code": None, **result})

    else:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: schedule install|remove|status"}, exit_code=2)
