"""scan raw command — collect raw messages for profile inference.

Mirrors ``criteria draft``'s LINE fail-fast semantics: if LINE is enabled,
factory errors and probe failures are hard errors (no degradation path).

Also saves the full collected records + source status to
``<home>/runs/<id>/records.jsonl`` and ``<home>/runs/<id>/source-status.json``
for reuse by ``criteria draft --from-run``.
"""

from __future__ import annotations

import secrets
from typing import Any

from daily_agenda import collect, state
from daily_agenda.line_source import LineSourceError
from daily_agenda.scan_raw import write_raw_sample, save_records, save_source_status
from ._common import Deps, emit, require_consent


def handle(argv: list[str], deps: Deps) -> int:
    if not argv or argv[0] != "raw":
        return emit({"ok": False, "code": "ARG_ERROR",
                      "detail": "usage: scan raw --days N"},
                    exit_code=2)

    cfg = state.load_config(deps.home)
    rc = require_consent(cfg)
    if rc is not None:
        return rc

    # Parse --days
    days = 14
    for i, arg in enumerate(argv):
        if arg == "--days" and i + 1 < len(argv):
            try:
                days = int(argv[i + 1])
            except ValueError:
                return emit({"ok": False, "code": "ARG_ERROR",
                              "detail": "--days must be integer"}, exit_code=2)

    now = deps.now_factory()
    run_id = now.strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(2)
    runs_dir = deps.home / "runs" / run_id
    raw_dir = runs_dir / "raw"
    progress = deps.progress

    # Build LINE source — map factory errors to their code (mirror cmd_criteria)
    line_src: Any = None
    line_enabled = cfg.get("line", {}).get("enabled", False)
    if deps.line_source_factory and line_enabled:
        try:
            line_src = deps.line_source_factory(
                main_database=cfg["line"].get("main_database", ""),
                trusted_builds_path=str(deps.home / "trusted_builds.json"),
            )
        except LineSourceError as exc:
            return emit({"ok": False, "code": exc.code})
        except Exception:
            return emit({"ok": False, "code": "LINE_RUNTIME_MISSING"})

    # --- Fail-fast LINE probe (mirror cmd_criteria) ---
    if line_src is not None:
        progress.event("line_check")
        try:
            line_src.recent_chats(days=14, offset=0, limit=1)
        except LineSourceError as exc:
            return emit({"ok": False, "code": exc.code})
        except Exception:
            return emit({"ok": False, "code": "LOCAL_READER_FAILED"})

    graph_src: Any = None
    if deps.graph_factory and cfg.get("mail", {}).get("enabled", True):
        try:
            graph_src = deps.graph_factory()
        except Exception:
            pass

    # Collect with initial state (no checkpoints advance)
    from daily_agenda.state import _initial_state

    try:
        result = collect.collect(
            cfg, _initial_state(), now=now, line=line_src, graph=graph_src,
            run_id=run_id, initial_days=days, progress=progress,
        )
    except collect.CollectError as exc:
        return emit({"ok": False, "code": exc.code})

    # --- Check LINE collection result (no degradation path) ---
    if line_enabled and line_src is not None:
        line_status = result.source_status.get("line", {})
        if line_status.get("status") == "failed":
            code = line_status.get("code") or "LOCAL_READER_FAILED"
            return emit({"ok": False, "code": code})

    # Save full records + source status for reuse by criteria draft --from-run
    runs_dir.mkdir(parents=True, exist_ok=True)
    save_records(result.records, runs_dir)
    save_source_status(result.source_status, runs_dir)

    # Also save the days used for the scan (for criteria draft reporting)
    from daily_agenda.fsutil import atomic_write_json
    atomic_write_json(runs_dir / "scan-meta.json", {"days": days})

    # Write raw sample
    sample_result = write_raw_sample(result.records, raw_dir)

    # Include source status (status/code/counts only, no names) and run_id
    sample_result["source_status"] = result.source_status
    sample_result["run_id"] = run_id
    sample_result["days"] = days
    return emit(sample_result)
