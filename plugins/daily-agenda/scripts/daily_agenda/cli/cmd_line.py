"""line-detect / line-select commands."""

from __future__ import annotations

import os
from pathlib import Path
from typing import Any

from daily_agenda import line_detect, state
from ._common import Deps, emit, require_consent


def _default_db_dir() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "LINE" / "Data" / "db"


def handle_detect(argv: list[str], deps: Deps) -> int:
    cfg = state.load_config(deps.home)
    rc = require_consent(cfg)
    if rc is not None:
        return rc

    db_dir = deps.db_dir or _default_db_dir()

    # Use the formal trust file if it exists; otherwise probe will get
    # LINE_BUILD_UNVERIFIED from the vendored reader (which is the expected
    # result when trust probe hasn't run yet).
    trust_path = deps.home / "trusted_builds.json"

    def _probe(db_filename: str) -> dict[str, Any]:
        if deps.line_source_factory:
            try:
                src = deps.line_source_factory(
                    main_database=db_filename,
                    trusted_builds_path=str(trust_path),
                )
                return src.probe()
            except Exception as exc:
                return {"ok": False, "code": getattr(exc, "code", "LOCAL_READER_FAILED")}
        return {"ok": False, "code": "LINE_RUNTIME_MISSING"}

    result = line_detect.detect_main_database(db_dir=db_dir, probe=_probe)

    if result["status"] == "ok":
        cfg.setdefault("line", {})["main_database"] = result["database"]
        cfg["line"]["enabled"] = True
        state.save_config(deps.home, cfg)
        return emit({"ok": True, "status": "ok", "database": result["database"]})
    elif result["status"] == "multiple":
        candidates = [
            {"name": c["filename"], "mtime": c.get("mtime", "")}
            for c in result.get("candidates", [])
        ]
        return emit({"ok": False, "status": "multiple", "code": "MULTIPLE_DATABASES_OK",
                      "candidates": candidates})
    else:
        candidates = [
            {"name": c["filename"], "mtime": c.get("mtime", ""), "code": c.get("code")}
            for c in result.get("candidates", [])
        ]
        return emit({"ok": False, "status": "failed", "code": result.get("code", "MAIN_DATABASE_NOT_FOUND"),
                      "candidates": candidates})


def handle_select(argv: list[str], deps: Deps) -> int:
    # Parse --database NAME
    db_name: str | None = None
    for i, arg in enumerate(argv):
        if arg == "--database" and i + 1 < len(argv):
            db_name = argv[i + 1]

    if not db_name:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: line-select --database NAME"}, exit_code=2)

    # Validate: must end in .edb, no path separators
    if not db_name.endswith(".edb"):
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "database name must end in .edb"}, exit_code=2)
    if "/" in db_name or "\\" in db_name:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "database name must not contain path separators"}, exit_code=2)

    cfg = state.load_config(deps.home)
    cfg.setdefault("line", {})["main_database"] = db_name
    cfg["line"]["enabled"] = True
    state.save_config(deps.home, cfg)
    return emit({"ok": True, "database": db_name})
