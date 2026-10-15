"""LINE version trust bootstrap (plan D-11, task 2.5).

Before reading LINE data, we verify the running LINE.exe has a valid Authenticode
signature from LY Corporation, then write a temporary trust file for a probe
(trial read). Only on probe success do we atomically write the formal trust list.

Decisions (fail-closed):
- Signature must be ``Valid`` and signer org must be exactly ``LY Corporation``.
- Corrupt ``trusted_builds.json`` is an error, never silently treated as empty.
- Noninteractive mode (D-14 / AS-15) never writes trust — returns ``LINE_BUILD_UNVERIFIED``
  so ``/agenda`` can tell the user to run manually.
- Probe exceptions are caught; the temporary file is *always* deleted (try/finally).
- Dedup key is ``sha256`` (lowercase 64-char hex).
- POSIX: formal file gets mode 0600; Windows relies on user-directory ACL.
- ``exe`` parameter defaults to ``envcheck.default_line_exe()`` (the standard install
  path), but callers can pass the actual running process path if they have it.
"""

from __future__ import annotations

import json
import os
import re
import secrets
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

from .envcheck import default_line_exe
from .fsutil import atomic_write_json
from .messages import humanize

ACCEPTED_SIGNER_ORG = "LY Corporation"
_SHA256_RE = re.compile(r"^[0-9a-f]{64}$")
_REQUIRED_ENTRY_KEYS = {"version", "bytes", "sha256", "signer", "thumbprint", "trusted_at"}


class TrustedBuildsCorrupt(ValueError):
    """Raised when ``trusted_builds.json`` exists but is unparseable or structurally invalid."""


# ---------------------------------------------------------------------------
# Public helpers
# ---------------------------------------------------------------------------


ExeInfoReader = Callable[[Path], dict[str, Any]]
Probe = Callable[[dict[str, str]], dict[str, Any]]


def trusted_builds_path(h: Path) -> Path:
    """Return the canonical path of the formal trust list."""
    return Path(h) / "trusted_builds.json"


def load_trusted(h: Path) -> dict[str, Any]:
    """Load and validate ``trusted_builds.json``.

    Raises:
        FileNotFoundError: file does not exist.
        TrustedBuildsCorrupt: file exists but is invalid (bad JSON, missing keys,
            malformed entries).  Callers must *not* treat this as empty.
    """
    p = trusted_builds_path(h)
    raw = p.read_text(encoding="utf-8")  # raises FileNotFoundError if absent
    try:
        data = json.loads(raw)
    except json.JSONDecodeError as exc:
        raise TrustedBuildsCorrupt(f"invalid JSON in {p}") from exc
    if not isinstance(data, dict) or "builds" not in data:
        raise TrustedBuildsCorrupt(f"missing 'builds' key in {p}")
    if not isinstance(data["builds"], list):
        raise TrustedBuildsCorrupt(f"'builds' is not a list in {p}")
    for i, entry in enumerate(data["builds"]):
        _validate_entry(entry, i, p)
    return data


def _validate_entry(entry: dict, index: int, path: Path) -> None:
    if not isinstance(entry, dict):
        raise TrustedBuildsCorrupt(f"entry {index} is not a dict in {path}")
    missing = _REQUIRED_ENTRY_KEYS - set(entry)
    if missing:
        raise TrustedBuildsCorrupt(f"entry {index} missing keys {missing} in {path}")
    if not isinstance(entry["bytes"], int):
        raise TrustedBuildsCorrupt(f"entry {index} 'bytes' is not int in {path}")
    if not _SHA256_RE.match(str(entry.get("sha256", ""))):
        raise TrustedBuildsCorrupt(f"entry {index} 'sha256' is not valid hex in {path}")


# ---------------------------------------------------------------------------
# Core bootstrap
# ---------------------------------------------------------------------------


def bootstrap_trust(
    h: Path,
    *,
    exe_info_reader: ExeInfoReader,
    probe: Probe,
    now: datetime,
    noninteractive: bool,
    exe: Path | None = None,
) -> dict[str, Any]:
    """Trust bootstrap for D-11.

    Args:
        h: User state directory (``DAILY_AGENDA_HOME``).
        exe_info_reader: Callable that takes an exe ``Path`` and returns identity info.
        probe: Callable that takes ``{"LINE_MCP_TRUSTED_BUILDS": path}`` and trial-reads.
        now: Current time for ``trusted_at``.
        noninteractive: If True (scheduled run), never write trust.
        exe: Path to LINE.exe; defaults to ``envcheck.default_line_exe()``.

    Returns a dict with at least ``ok``, ``status``, ``code``, ``message`` keys.
    """
    h = Path(h)
    exe_path = exe if exe is not None else default_line_exe()

    # 0. Read exe info (caller passes exe path, not home dir)
    exe_info = exe_info_reader(exe_path)

    # 1. Check for corrupt trusted_builds.json *before* anything else
    existing_builds: dict[str, Any] | None = None
    try:
        existing_builds = load_trusted(h)
    except FileNotFoundError:
        pass  # no file yet — that's fine
    except TrustedBuildsCorrupt:
        return _result(False, "rejected", "TRUSTED_BUILDS_CORRUPT", exe_info)

    # 2. Validate signature (fail-closed)
    if exe_info.get("signature_status") != "Valid" or exe_info.get("signer_org") != ACCEPTED_SIGNER_ORG:
        return _result(False, "rejected", "LINE_SIGNATURE_INVALID", exe_info)

    # 3. Already trusted? (dedup by sha256)
    candidate_sha = exe_info["sha256"].lower()
    if existing_builds is not None:
        known_hashes = {b["sha256"] for b in existing_builds["builds"]}
        if candidate_sha in known_hashes:
            return _result(True, "already_trusted", None, exe_info)

    # 4. Noninteractive: do not write trust (AS-15)
    if noninteractive:
        return _result(False, "skipped_noninteractive", "LINE_BUILD_UNVERIFIED", exe_info)

    # 5. Write temp trust file, probe, then clean up
    runs_dir = h / "runs"
    runs_dir.mkdir(parents=True, exist_ok=True)
    temp_name = f"trust-probe-{secrets.token_hex(8)}.json"
    temp_path = runs_dir / temp_name

    candidate_entry = {
        "version": exe_info["version"],
        "bytes": exe_info["bytes"],
        "sha256": candidate_sha,
        "signer": exe_info["signer_org"],
        "thumbprint": exe_info["thumbprint"],
        "trusted_at": now.isoformat(),
    }
    temp_data = {"builds": [candidate_entry]}
    # Use atomic_write_json for the temp file too — mkstemp creates at 0600
    atomic_write_json(temp_path, temp_data)

    try:
        probe_result = probe({"LINE_MCP_TRUSTED_BUILDS": str(temp_path)})
    except Exception as exc:
        return _result(False, "rejected", "LINE_PROBE_FAILED", exe_info,
                       detail=f"{type(exc).__name__}: {exc}")
    finally:
        temp_path.unlink(missing_ok=True)

    # 6. Evaluate probe result
    if not probe_result.get("ok"):
        return _result(False, "rejected", probe_result.get("code"), exe_info)

    # 7. Probe succeeded — write formal list (merge + dedup)
    if existing_builds is not None:
        merged = list(existing_builds["builds"])
    else:
        merged = []

    # Dedup: only add if sha256 not already present
    if candidate_sha not in {b["sha256"] for b in merged}:
        merged.append(candidate_entry)

    formal_data = {"builds": merged}
    formal_path = trusted_builds_path(h)
    atomic_write_json(formal_path, formal_data)
    if os.name != "nt":
        os.chmod(formal_path, 0o600)

    return _result(True, "trusted", None, exe_info)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _result(
    ok: bool,
    status: str,
    code: str | None,
    exe_info: dict[str, Any],
    **extra: Any,
) -> dict[str, Any]:
    return {
        "ok": ok,
        "status": status,
        "code": code,
        "message": humanize(code),
        "build": {
            "version": exe_info.get("version"),
            "sha256": exe_info.get("sha256"),
        },
        **extra,
    }
