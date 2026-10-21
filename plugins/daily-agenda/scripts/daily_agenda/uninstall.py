"""Rollback (task 6.4): remove the scheduled task, optionally delete the state directory.

Output files (the user's agenda folder) are never deleted. The plugin registers no
user-scope MCP server, so there is nothing else to undo.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from . import history, schedule

Runner = Callable[..., subprocess.CompletedProcess]


def _assert_safe_home(h: Path) -> Path:
    resolved = Path(h).resolve()
    if resolved == Path(resolved.anchor) or resolved == Path.home().resolve() or len(resolved.parts) < 3:
        raise ValueError("refusing to delete a suspicious state directory")
    return resolved


def uninstall(h: Path, *, purge: bool, runner: Runner = subprocess.run) -> dict[str, Any]:
    resolved = _assert_safe_home(h)
    removed = schedule.remove(resolved, runner=runner)
    # C4: remove history database (always, not just on purge)
    history_removed = False
    try:
        history_removed = history.remove_database(resolved)
    except Exception:
        pass
    home_removed = False
    if purge and resolved.exists():
        shutil.rmtree(resolved)
        home_removed = True
    return {"schedule_removed": removed["ok"], "home_removed": home_removed,
            "history_removed": history_removed}
