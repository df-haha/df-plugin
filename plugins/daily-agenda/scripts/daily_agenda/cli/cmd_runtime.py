"""runtime install [--with-cua] / runtime status"""

from __future__ import annotations

import shutil
import subprocess

from daily_agenda import runtime
from ._common import Deps, emit


def handle(argv: list[str], deps: Deps) -> int:
    if not argv:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: runtime install|status"}, exit_code=2)

    sub = argv[0]
    runner = deps.runner or subprocess.run
    which = deps.which or shutil.which

    if sub == "install":
        components: list[str] | None = None
        if "--with-cua" in argv:
            components = list(runtime.DEFAULT_COMPONENTS) + ["cua"]
        with deps.progress.heartbeat():
            result = runtime.install(
                deps.home,
                plugin_root=deps.plugin_root,
                components=components,
                fetch=deps.fetch,
                runner=runner,
                which=which,
                arch_checker=deps.arch_checker,
            )
        return emit({"ok": result["ok"], "code": None, "components": result["components"]})

    elif sub == "status":
        result = runtime.status(deps.home, deps.plugin_root)
        return emit({"ok": result["ok"], "code": None, "components": result["components"]})

    else:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": f"unknown runtime subcommand: {sub}"}, exit_code=2)
