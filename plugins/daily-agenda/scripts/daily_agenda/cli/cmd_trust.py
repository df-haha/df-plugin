"""trust probe — with integrated main-database detection.

When ``config.line.main_database`` is unset, ``trust probe`` first runs
detection with a temporary trust file, saves the single detected DB to
config, then runs ``bootstrap_trust`` with ``LINE_MCP_MAIN_DATABASE`` set.

If detection returns ``multiple``, return that status + candidates (filename
+ mtime only) WITHOUT writing trust.  If ``failed``, return the code.
"""

from __future__ import annotations

import os
import secrets
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from daily_agenda import envcheck, line_detect, state, trust
from daily_agenda.fsutil import atomic_write_json
from daily_agenda.runtime import env_for_line
from ._common import Deps, emit, require_consent


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

@contextmanager
def _temp_trust_file(h: Path, entry: dict[str, Any]) -> Iterator[Path]:
    """Write a temporary trust file under ``<home>/runs/``; always deleted."""
    runs_dir = h / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    name = f"trust-detect-{secrets.token_hex(8)}.json"
    path = runs_dir / name
    atomic_write_json(path, {"builds": [entry]})
    try:
        yield path
    finally:
        path.unlink(missing_ok=True)


def _candidate_entry(exe_info: dict[str, Any], now: datetime) -> dict[str, Any]:
    """Build a trust entry from exe info (same shape as trust.bootstrap_trust)."""
    return {
        "version": exe_info.get("version"),
        "bytes": exe_info.get("bytes", 0),
        "sha256": (exe_info.get("sha256") or "").lower(),
        "signer": exe_info.get("signer_org", ""),
        "thumbprint": exe_info.get("thumbprint", ""),
        "trusted_at": now.isoformat(),
    }


def _default_db_dir() -> Path:
    """LINE data directory on Windows."""
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "LINE" / "Data" / "db"


# ---------------------------------------------------------------------------
# Handler
# ---------------------------------------------------------------------------

def handle(argv: list[str], deps: Deps) -> int:
    if not argv or argv[0] != "probe":
        return emit({"ok": False, "code": "ARG_ERROR", "detail": "usage: trust probe"}, exit_code=2)

    cfg = state.load_config(deps.home)
    rc = require_consent(cfg)
    if rc is not None:
        return rc

    now = deps.now_factory()

    # Read exe info (once — expensive on real machine)
    reader = deps.exe_info_reader or envcheck.read_line_exe_info
    exe_path = envcheck.default_line_exe()
    try:
        exe_info = reader(exe_path)
    except Exception as exc:
        return emit({"ok": False, "code": "LINE_NOT_RUNNING", "detail": str(exc)})

    # Check signature first (fail-closed, short-circuits everything)
    if exe_info.get("signature_status") != "Valid" or exe_info.get("signer_org") != trust.ACCEPTED_SIGNER_ORG:
        return emit({"ok": False, "status": "rejected", "code": "LINE_SIGNATURE_INVALID",
                      "build": {"version": exe_info.get("version"), "sha256": exe_info.get("sha256")}})

    # Noninteractive: never write trust (D-14 / AS-15)
    if deps.noninteractive:
        return emit({"ok": False, "status": "skipped_noninteractive", "code": "LINE_BUILD_UNVERIFIED",
                      "build": {"version": exe_info.get("version"), "sha256": exe_info.get("sha256")}})

    # Check if main_database is already configured
    main_db = cfg.get("line", {}).get("main_database")

    if not main_db:
        # Run detection with a temporary trust file
        entry = _candidate_entry(exe_info, now)
        db_dir = deps.db_dir or _default_db_dir()

        with _temp_trust_file(deps.home, entry) as temp_path:
            def _detect_probe(db_filename: str) -> dict[str, Any]:
                """Probe a single candidate DB using temp trust."""
                if deps.line_source_factory:
                    try:
                        src = deps.line_source_factory(
                            main_database=db_filename,
                            trusted_builds_path=str(temp_path),
                        )
                        return src.probe()
                    except Exception:
                        return {"ok": False, "code": "LOCAL_READER_FAILED"}
                return {"ok": False, "code": "LINE_RUNTIME_MISSING"}

            with deps.progress.heartbeat():
                detect_result = line_detect.detect_main_database(
                    db_dir=db_dir,
                    probe=_detect_probe,
                )

        if detect_result["status"] == "ok":
            main_db = detect_result["database"]
            cfg.setdefault("line", {})["main_database"] = main_db
            cfg["line"]["enabled"] = True
            state.save_config(deps.home, cfg)
        elif detect_result["status"] == "multiple":
            candidates = [
                {"name": c["filename"], "mtime": c.get("mtime", "")}
                for c in detect_result.get("candidates", [])
            ]
            return emit({"ok": False, "status": "multiple", "code": "MULTIPLE_DATABASES_OK",
                          "candidates": candidates})
        else:
            code = detect_result.get("code", "MAIN_DATABASE_NOT_FOUND")
            return emit({"ok": False, "status": "failed", "code": code})

    # Now run bootstrap_trust with the resolved main_database
    def _probe_with_db(env_overrides: dict[str, str]) -> dict[str, Any]:
        """Probe using the detected main_database."""
        if deps.line_source_factory:
            try:
                src = deps.line_source_factory(
                    main_database=main_db,
                    trusted_builds_path=env_overrides.get("LINE_MCP_TRUSTED_BUILDS", ""),
                )
                return src.probe()
            except Exception as exc:
                code = getattr(exc, "code", "LOCAL_READER_FAILED")
                return {"ok": False, "code": code}
        return {"ok": False, "code": "LINE_RUNTIME_MISSING"}

    with deps.progress.heartbeat():
        result = trust.bootstrap_trust(
            deps.home,
            exe_info_reader=lambda _p: exe_info,  # already read
            probe=_probe_with_db,
            now=now,
            noninteractive=deps.noninteractive,
            exe=exe_path,
        )
    return emit(result)
