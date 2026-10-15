"""Detect the correct LINE main database among local .edb files (setup step 3).

Lists ``*.edb`` in the LINE data directory, excludes known non-main databases
(album, chatStats, keep), then probes each candidate.  Exactly one success →
return that filename; all fail → return per-file error codes; multiple succeed →
``MULTIPLE_DATABASES_OK`` with modification times for user selection.
"""

from __future__ import annotations

import os
from datetime import datetime, timezone
from pathlib import Path
from typing import Any, Callable, Protocol


_EXCLUDED_PREFIXES = ("album", "chatstats", "keep")


class ProbeCallable(Protocol):
    def __call__(self, db_filename: str) -> dict[str, Any]: ...


def _list_edb_files(db_dir: Path) -> list[Path]:
    """List .edb files in *db_dir*, excluding non-main databases."""
    if not db_dir.is_dir():
        return []
    candidates: list[Path] = []
    for p in sorted(db_dir.iterdir()):
        if not p.is_file():
            continue
        if p.suffix.lower() != ".edb":
            continue
        name_lower = p.stem.lower()
        if any(name_lower.startswith(prefix) for prefix in _EXCLUDED_PREFIXES):
            continue
        candidates.append(p)
    return candidates


def detect_main_database(
    *,
    db_dir: Path,
    probe: ProbeCallable,
) -> dict[str, Any]:
    """Probe each candidate .edb and return a detection result.

    Returns a dict with:
    - ``status``: ``"ok"`` | ``"failed"`` | ``"multiple"``
    - ``database``: filename (when status is ``"ok"``)
    - ``code``: error code (when ``"failed"``)
    - ``candidates``: list of ``{filename, mtime, ok, code}`` (when ``"multiple"`` or ``"failed"``)
    """
    db_dir = Path(db_dir)
    candidates = _list_edb_files(db_dir)

    if not candidates:
        return {
            "status": "failed",
            "code": "MAIN_DATABASE_NOT_FOUND",
            "candidates": [],
        }

    results: list[dict[str, Any]] = []
    for p in candidates:
        try:
            mtime_ts = p.stat().st_mtime
            mtime = datetime.fromtimestamp(mtime_ts, tz=timezone.utc).isoformat()
        except OSError:
            mtime = ""

        probe_result = probe(p.name)
        results.append({
            "filename": p.name,
            "mtime": mtime,
            "ok": probe_result.get("ok", False),
            "code": probe_result.get("code"),
        })

    successes = [r for r in results if r["ok"]]

    if len(successes) == 1:
        return {
            "status": "ok",
            "database": successes[0]["filename"],
        }

    if len(successes) == 0:
        # If all candidates share one error code (or there is only one candidate),
        # use that code as top-level instead of the generic MAIN_DATABASE_NOT_FOUND.
        failure_codes = {r["code"] for r in results if r.get("code")}
        if len(failure_codes) == 1:
            top_code = failure_codes.pop()
        else:
            top_code = "MAIN_DATABASE_NOT_FOUND"
        return {
            "status": "failed",
            "code": top_code,
            "candidates": results,
        }

    # Multiple databases resolved — ask user
    return {
        "status": "multiple",
        "code": "MULTIPLE_DATABASES_OK",
        "candidates": successes,
    }
