"""envcheck command."""

from __future__ import annotations

import os

from daily_agenda import envcheck
from ._common import Deps, emit


def handle(argv: list[str], deps: Deps) -> int:
    is_windows = os.name == "nt"
    runner = deps.runner
    if runner is None:
        import subprocess
        runner = subprocess.run
    result = envcheck.check_environment(runner=runner, is_windows=is_windows)
    # Use the first failing check's code as the top-level code
    first_fail_code = None
    if not result["ok"]:
        for c in result["checks"]:
            if not c["ok"] and c.get("code"):
                first_fail_code = c["code"]
                break
    return emit({"ok": result["ok"], "code": first_fail_code, "checks": result["checks"]})
