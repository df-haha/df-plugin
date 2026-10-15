"""Shared CLI infrastructure: Deps container, JSON emitter, argument parsing."""

from __future__ import annotations

import argparse
import json
import os
import sys
from dataclasses import dataclass, field
from datetime import datetime
from pathlib import Path
from typing import Any, Callable
from zoneinfo import ZoneInfo

from daily_agenda import state as _state
from daily_agenda.messages import humanize
from daily_agenda.progress import Progress


# ---------------------------------------------------------------------------
# Deps — injectable dependencies so tests run on Linux without Windows/LINE
# ---------------------------------------------------------------------------

@dataclass
class Deps:
    """All external dependencies for CLI commands, fully injectable."""

    home: Path
    now_factory: Callable[[], datetime]
    plugin_root: Path
    noninteractive: bool = False

    # Overridable factories / callables (None = use production default)
    exe_info_reader: Callable[..., dict[str, Any]] | None = None
    line_source_factory: Callable[..., Any] | None = None
    graph_factory: Callable[..., Any] | None = None
    runner: Callable[..., Any] | None = None
    which: Callable[[str], str | None] | None = None
    arch_checker: Callable[[], dict[str, str]] | None = None
    fetch: Callable[..., bytes] | None = None
    db_dir: Path | None = None

    # Progress writer — injected from --progress-file or env; None = no-op.
    progress: Progress = field(default_factory=lambda: Progress(None))

    @classmethod
    def production(cls) -> Deps:
        import shutil
        import subprocess
        h = _state.home()
        h.mkdir(parents=True, exist_ok=True)
        tz = ZoneInfo("Asia/Taipei")

        plugin_root_env = os.environ.get("CLAUDE_PLUGIN_ROOT")
        if plugin_root_env:
            pr = Path(plugin_root_env)
        else:
            # Derive from package location: __file__ is cli/_common.py
            # parents[3] = plugin root (scripts/daily_agenda/cli/_common.py -> plugins/daily-agenda/)
            pr = Path(__file__).resolve().parents[3]

        def _line_factory(*, main_database: str, trusted_builds_path: str) -> Any:
            from daily_agenda.line_source import LineSource, LineSourceError
            from daily_agenda.runtime import env_for_line
            env = env_for_line(h)
            python_path = env.get("LINE_MCP_PYTHON")
            dll_path = env.get("LINE_MCP_SQLITE3MC_DLL")
            if not python_path or not dll_path:
                raise LineSourceError("LINE_RUNTIME_MISSING")
            return LineSource(
                plugin_dir=pr,
                runtime_dir=h / "runtime",
                python_path=python_path,
                sqlite3mc_dll=dll_path,
                main_database=main_database,
                trusted_builds_path=trusted_builds_path,
            )

        _graph_cleanups: list[Any] = []

        def _graph_factory() -> Any:
            from daily_agenda.graph_client import open_graph
            ctx = open_graph()
            source = ctx.__enter__()
            _graph_cleanups.append(ctx)
            return source

        import atexit

        def _cleanup_graph() -> None:
            for ctx in _graph_cleanups:
                try:
                    ctx.__exit__(None, None, None)
                except Exception:
                    pass

        atexit.register(_cleanup_graph)

        return cls(
            home=h,
            now_factory=lambda: datetime.now(tz),
            plugin_root=pr,
            noninteractive=os.environ.get("DAILY_AGENDA_NONINTERACTIVE") == "1",
            runner=subprocess.run,
            which=shutil.which,
            line_source_factory=_line_factory,
            graph_factory=_graph_factory,
        )


# ---------------------------------------------------------------------------
# JSON output
# ---------------------------------------------------------------------------

# Module-level progress reference so emit() can write terminal events.
# Set by main() at entry, cleared in finally.  Tests call main() repeatedly
# in-process, so the reset matters.
_current_progress: Progress | None = None


def set_current_progress(p: Progress | None) -> None:
    """Set the module-level progress reference (called by main)."""
    global _current_progress
    _current_progress = p


def emit(obj: dict[str, Any], *, exit_code: int | None = None) -> int:
    """Print one JSON object to stdout; return exit code.

    Fills ``message`` from ``humanize(code)`` when not already present.
    Also writes a terminal ``done``/``failed`` progress event when a
    progress file is active.
    """
    if "message" not in obj:
        obj["message"] = humanize(obj.get("code"))

    # Terminal progress event
    if _current_progress is not None:
        code = obj.get("code")
        if obj.get("ok", False):
            _current_progress.done(code=code)
        else:
            _current_progress.failed(code=code)

    json.dump(obj, sys.stdout, ensure_ascii=False)
    sys.stdout.write("\n")
    sys.stdout.flush()
    if exit_code is not None:
        return exit_code
    return 0 if obj.get("ok", False) else 1


# ---------------------------------------------------------------------------
# Argument parsing (JSON-safe error output)
# ---------------------------------------------------------------------------

class _ArgParser(argparse.ArgumentParser):
    """ArgumentParser that prints JSON on error and exits with code 2."""

    def error(self, message: str) -> None:  # type: ignore[override]
        emit({"ok": False, "code": "ARG_ERROR", "detail": message}, exit_code=2)
        sys.exit(2)


def parse_root_args(argv: list[str]) -> tuple[argparse.Namespace, list[str]]:
    """Parse top-level command name and ``--progress-file``; return (ns, remaining)."""
    parser = _ArgParser(prog="daily_agenda", add_help=False, allow_abbrev=False)
    parser.add_argument("command", nargs="?", default=None)
    parser.add_argument("--progress-file", default=None, dest="progress_file")
    return parser.parse_known_args(argv)


def require_consent(cfg: dict[str, Any]) -> int | None:
    """Return an exit code if consent is missing, else None."""
    try:
        _state.require_consent(cfg)
        return None
    except _state.ConsentRequired:
        emit({"ok": False, "code": "CONSENT_REQUIRED"})
        return 1


def load_cfg_and_state(deps: Deps) -> tuple[dict[str, Any], dict[str, Any]]:
    """Load config and current state."""
    cfg = _state.load_config(deps.home)
    st = _state.load_state(deps.home)
    return cfg, st
