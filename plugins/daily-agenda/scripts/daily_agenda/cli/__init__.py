"""CLI dispatcher for daily-agenda.

Thin command layer; implementations live in submodules.
"""

from __future__ import annotations

import os
import sys
from pathlib import Path
from typing import Any

from ._common import Deps, emit, parse_root_args, set_current_progress
from daily_agenda.progress import Progress


def main(argv: list[str] | None = None, *, deps: Deps | None = None) -> int:
    """Entry point: parse *argv*, dispatch to the right handler, return exit code."""
    if argv is None:
        argv = sys.argv[1:]

    ns, remaining = parse_root_args(argv)
    command = ns.command

    if not command:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "no command given"}, exit_code=2)

    # Resolve progress BEFORE Deps.production() so startup failures still
    # write a terminal event and ``progress watch`` does not hang (#1).
    progress_path_str = ns.progress_file or os.environ.get("DAILY_AGENDA_PROGRESS_FILE")
    progress: Progress | None = None
    if progress_path_str:
        progress = Progress(Path(progress_path_str))
        set_current_progress(progress)

    try:
        try:
            d = deps or Deps.production()
        except Exception as exc:
            # Startup failures (e.g. missing tzdata on Windows) must still
            # honour the one-JSON contract.
            return emit({"ok": False, "code": "INTERNAL_ERROR", "detail": type(exc).__name__})

        # Attach resolved progress to deps
        if progress is not None:
            d.progress = progress
        elif d.progress._path is not None:
            # deps was injected with a progress already set
            set_current_progress(d.progress)

        # Emit start event for progress-tracked commands
        if d.progress._path is not None:
            d.progress.event("start")

        # Import handlers lazily to keep startup fast.
        from . import cmd_consent, cmd_config, cmd_env, cmd_runtime, cmd_trust
        from . import cmd_line, cmd_collect, cmd_criteria, cmd_ledger_ops
        from . import cmd_refine, cmd_schedule, cmd_uninstall, cmd_run
        from . import cmd_progress

        dispatch: dict[str, Any] = {
            "consent": cmd_consent.handle,
            "envcheck": cmd_env.handle,
            "runtime": cmd_runtime.handle,
            "trust": cmd_trust.handle,
            "line-detect": cmd_line.handle_detect,
            "line-select": cmd_line.handle_select,
            "probe": cmd_collect.handle_probe,
            "outlook-preview": cmd_collect.handle_outlook_preview,
            "chats": cmd_collect.handle_chats,
            "exclude": cmd_collect.handle_exclude,
            "ctx": cmd_collect.handle_ctx,
            "config": cmd_config.handle,
            "criteria": cmd_criteria.handle,
            "run": cmd_run.handle,
            "show": cmd_ledger_ops.handle_show,
            "mark-done": cmd_ledger_ops.handle_mark_done,
            "reject-done": cmd_ledger_ops.handle_reject_done,
            "dismiss": cmd_ledger_ops.handle_dismiss,
            "restore": cmd_ledger_ops.handle_restore,
            "refine": cmd_refine.handle,
            "schedule": cmd_schedule.handle,
            "uninstall": cmd_uninstall.handle,
            # progress: stream command (watch) is exempt from one-JSON contract
            "progress": cmd_progress.handle,
        }

        handler = dispatch.get(command)
        if handler is None:
            return emit({"ok": False, "code": "ARG_ERROR", "detail": f"unknown command: {command}"}, exit_code=2)

        try:
            return handler(remaining, d)
        except SystemExit:
            raise  # re-raise exits from argparse
        except KeyboardInterrupt:
            # Write failed event before re-raising so progress watch exits
            if d.progress._path is not None:
                d.progress.failed(code="INTERNAL_ERROR")
            raise
        except Exception as exc:
            # Contract §7: every command prints exactly ONE JSON object.
            # Detail is type name only — exception messages may contain untrusted content.
            return emit({"ok": False, "code": "INTERNAL_ERROR", "detail": type(exc).__name__})
    finally:
        set_current_progress(None)
