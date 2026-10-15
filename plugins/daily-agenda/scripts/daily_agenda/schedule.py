"""Windows Task Scheduler integration (plan D-14, task 6.3).

The scheduled task runs the scripted pipeline (``python -m daily_agenda run
--noninteractive``), never a tool-enabled Claude session; the pipeline itself only
calls the tool-less analysis process. Non-interactive runs skip anything that needs
the user (LINE trust bootstrap, criteria refinement) — see C-10.
"""

from __future__ import annotations

import re
import subprocess
from pathlib import Path
from typing import Any, Callable

from .fsutil import atomic_write_text

TASK_NAME = "daily-agenda"
_TIME = re.compile(r"^(?:[01]\d|2[0-3]):[0-5]\d$")

Runner = Callable[..., subprocess.CompletedProcess]


def validate_time(value: str) -> str:
    if not isinstance(value, str) or not _TIME.match(value):
        raise ValueError("time must be HH:MM (24h)")
    return value


def launcher_path(h: Path) -> Path:
    return Path(h) / "schedule" / "run.cmd"


def write_launcher(h: Path, *, plugin_root: Path, uv: str) -> Path:
    """Write ``run.cmd``; rewritten whenever the plugin root changes (plugin updates)."""
    scripts = Path(plugin_root) / "scripts"
    log = Path(h) / "schedule" / "last-run.log"
    text = (
        "@echo off\r\n"
        "setlocal\r\n"
        "set DAILY_AGENDA_NONINTERACTIVE=1\r\n"
        "set PYTHONIOENCODING=utf-8\r\n"
        f'"{uv}" run --quiet --project "{scripts}" python -P -m daily_agenda run --noninteractive'
        f' > "{log}" 2>&1\r\n'
        "exit /b %ERRORLEVEL%\r\n"
    )
    path = launcher_path(h)
    atomic_write_text(path, text)
    return path


def _run(runner: Runner, cmd: list[str]) -> subprocess.CompletedProcess:
    return runner(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=60)


def install(h: Path, *, time: str, plugin_root: Path, uv: str, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Register a daily task for the current user (runs only while logged on, limited rights)."""
    validate_time(time)
    launcher = write_launcher(h, plugin_root=plugin_root, uv=uv)
    proc = _run(runner, ["schtasks", "/Create", "/TN", TASK_NAME, "/SC", "DAILY", "/ST", time,
                         "/TR", f'"{launcher}"', "/F"])
    return {"ok": proc.returncode == 0, "time": time, "launcher": str(launcher),
            "code": None if proc.returncode == 0 else "SCHEDULE_FAILED"}


def remove(h: Path, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    proc = _run(runner, ["schtasks", "/Delete", "/TN", TASK_NAME, "/F"])
    launcher_path(h).unlink(missing_ok=True)
    return {"ok": proc.returncode == 0}


def status(*, runner: Runner = subprocess.run) -> dict[str, Any]:
    proc = _run(runner, ["schtasks", "/Query", "/TN", TASK_NAME, "/FO", "LIST"])
    if proc.returncode != 0:
        return {"registered": False}
    next_run = next((line.split(":", 1)[1].strip() for line in proc.stdout.splitlines()
                     if line.lower().startswith("next run time")), None)
    return {"registered": True, "next_run": next_run}
