"""uninstall [--purge]"""

from __future__ import annotations

import subprocess

from daily_agenda import uninstall
from ._common import Deps, emit


def handle(argv: list[str], deps: Deps) -> int:
    purge = "--purge" in argv
    runner = deps.runner or subprocess.run
    result = uninstall.uninstall(deps.home, purge=purge, runner=runner)
    return emit({"ok": True, "code": None, **result})
