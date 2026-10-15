"""User state directory, generation management, commit/publish protocol (§1)."""

from __future__ import annotations

import copy
import hashlib
import json
import os
import shutil
import tempfile
from contextlib import contextmanager
from datetime import datetime
from pathlib import Path
from typing import Any, Iterator

from daily_agenda.fsutil import atomic_write_json, atomic_write_text, fsync_dir, read_json


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------

class ConsentRequired(Exception):
    code: str = "CONSENT_REQUIRED"


class Locked(Exception):
    code: str = "LOCKED"


class PublishFailed(Exception):
    code: str = "PUBLISH_FAILED"


# ---------------------------------------------------------------------------
# Fail-point injection (testing only)
# ---------------------------------------------------------------------------

def _fail_point(name: str) -> None:  # noqa: ARG001
    """No-op in production; tests monkeypatch to raise at named points."""


# ---------------------------------------------------------------------------
# Home & config
# ---------------------------------------------------------------------------

_DEFAULT_CONFIG: dict[str, Any] = {
    "schema_version": 1,
    "tz": "Asia/Taipei",
    "consent": None,
    "line": {"enabled": False},
    "mail": {"enabled": False},
    "output": {"dir": None, "formats": ["html"]},
    "refine": {"every_runs": 10, "every_days": 14},
    "ctx": {"dir": None},
    "schedule": {"enabled": False, "time": None},
    "analysis": {"batch_bytes": 300_000, "model": None, "timeout_s": 600},
}


def home() -> Path:
    env = os.environ.get("DAILY_AGENDA_HOME")
    if env:
        return Path(env)
    return Path.home() / ".daily-agenda"


def load_config(h: Path) -> dict:
    """Load config.json; missing file returns defaults (consent=None)."""
    cfg = read_json(h / "config.json")
    if cfg is None:
        return copy.deepcopy(_DEFAULT_CONFIG)
    return cfg


def save_config(h: Path, cfg: dict) -> None:
    atomic_write_json(h / "config.json", cfg)


def require_consent(cfg: dict) -> None:
    """Raise :class:`ConsentRequired` if consent is missing or null."""
    consent = cfg.get("consent")
    if not consent or not consent.get("accepted_at"):
        raise ConsentRequired("consent not given")


def accept_consent(h: Path, now: datetime) -> None:
    """Record user consent in config."""
    cfg = load_config(h)
    cfg["consent"] = {"accepted_at": now.isoformat(), "text_version": 1}
    save_config(h, cfg)


def revoke_consent(h: Path) -> None:
    """Remove consent from config."""
    cfg = load_config(h)
    cfg["consent"] = None
    save_config(h, cfg)


# ---------------------------------------------------------------------------
# Generation directory management
# ---------------------------------------------------------------------------

def current_generation(h: Path) -> int | None:
    """Read the CURRENT file; return None if absent."""
    current_path = h / "CURRENT"
    try:
        text = current_path.read_text(encoding="utf-8").strip()
        return int(text)
    except (FileNotFoundError, ValueError):
        return None


def _write_current(h: Path, gen: int) -> None:
    atomic_write_text(h / "CURRENT", str(gen) + "\n")


def _gen_dir(h: Path, gen: int) -> Path:
    return h / "gen" / str(gen)


def _staging_dir(h: Path, gen: int) -> Path:
    return h / "gen" / f"{gen}.staging"


def load_state(h: Path) -> dict:
    """Load state.json from CURRENT generation; no generation → initial state."""
    gen = current_generation(h)
    if gen is None:
        return _initial_state()
    state_path = _gen_dir(h, gen) / "state.json"
    data = read_json(state_path)
    if data is None:
        return _initial_state()
    return data


def _initial_state() -> dict:
    return {
        "schema_version": 1,
        "generation": 0,
        "created_at": None,
        "checkpoints": {"line": None, "mail": None},
        "known_chats": [],
        "mail_index": {},
        "runs_since_refine": 0,
        "last_refine_at": None,
        "last_refine_applied_at": None,
        "criteria_version": 0,
        "report_code": None,
        "report_index": {},
        "last_run": None,
    }


def load_ledger(h: Path) -> list[dict]:
    """Load ledger.jsonl from CURRENT generation; no generation → empty list."""
    gen = current_generation(h)
    if gen is None:
        return []
    path = _gen_dir(h, gen) / "ledger.jsonl"
    try:
        lines = path.read_text(encoding="utf-8").strip().splitlines()
        return [json.loads(line) for line in lines if line.strip()]
    except FileNotFoundError:
        return []


# ---------------------------------------------------------------------------
# Stage / commit / publish
# ---------------------------------------------------------------------------

def stage_generation(
    h: Path,
    *,
    state: dict,
    ledger: list[dict],
    outputs: dict[str, str],
    publish_targets: list[dict],
) -> Path:
    """Write ``gen/<n+1>.staging/`` with all files; return staging path.

    *outputs*: ``{fmt: content_str}`` e.g. ``{"html": "<html>…"}``
    *publish_targets*: ``[{"fmt": "html", "path": "/abs/path/agenda-2026-10-08.html"}]``
    """
    # Clean up any leftover staging dirs first.
    cleanup_uncommitted(h)

    cur = current_generation(h) or 0
    next_gen = cur + 1
    staging = _staging_dir(h, next_gen)
    staging.mkdir(parents=True, exist_ok=True)

    # state.json
    gen_state = dict(state)
    gen_state["generation"] = next_gen
    atomic_write_json(staging / "state.json", gen_state)

    # ledger.jsonl
    ledger_text = "\n".join(json.dumps(t, ensure_ascii=False) for t in ledger)
    if ledger_text:
        ledger_text += "\n"
    atomic_write_text(staging / "ledger.jsonl", ledger_text)

    # Output files + publish.json with sha256.
    targets: list[dict] = []
    for pt in publish_targets:
        fmt = pt["fmt"]
        dest_path = pt["path"]
        content = outputs[fmt]
        content_bytes = content.encode("utf-8")
        sha = hashlib.sha256(content_bytes).hexdigest()

        out_name = f"agenda.{fmt}"
        atomic_write_text(staging / out_name, content)

        targets.append({"fmt": fmt, "path": dest_path, "sha256": sha})

    atomic_write_json(staging / "publish.json", {"targets": targets})

    _fail_point("staged")

    fsync_dir(staging)
    return staging


def commit_generation(h: Path, staging: Path) -> int:
    """Rename staging → gen/<n>, atomically replace CURRENT. Return gen number."""
    gen = int(staging.name.replace(".staging", ""))
    final = _gen_dir(h, gen)

    os.replace(staging, final)
    _fail_point("before_current_replace")
    _write_current(h, gen)
    _fail_point("after_current_replace")

    return gen


def publish(h: Path, gen: int) -> dict:
    """Publish outputs to the output directory per publish.json. Write receipt."""
    gen_dir = _gen_dir(h, gen)
    pub_json = read_json(gen_dir / "publish.json")
    if pub_json is None:
        raise PublishFailed("publish.json missing")

    targets = pub_json["targets"]
    published_targets: list[dict] = []

    for target in targets:
        fmt = target["fmt"]
        dest = Path(target["path"])
        sha_expected = target["sha256"]
        src = gen_dir / f"agenda.{fmt}"

        # If target already exists with correct hash, skip (idempotent).
        if dest.exists():
            existing_sha = hashlib.sha256(dest.read_bytes()).hexdigest()
            if existing_sha == sha_expected:
                published_targets.append({"path": str(dest), "sha256": sha_expected})
                continue

        content = src.read_bytes()
        actual_sha = hashlib.sha256(content).hexdigest()
        if actual_sha != sha_expected:
            raise PublishFailed(f"source hash mismatch for {fmt}")

        _fail_point("before_publish_rename")

        # Write to output dir via tmp + rename.
        dest.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp = tempfile.mkstemp(
            prefix=f".{dest.name}.", suffix=".tmp", dir=dest.parent,
        )
        try:
            with os.fdopen(fd, "wb") as fh:
                fh.write(content)
                fh.flush()
                os.fsync(fh.fileno())
            os.replace(tmp, dest)
        except BaseException:
            Path(tmp).unlink(missing_ok=True)
            raise

        _fail_point("after_publish_rename")

        published_targets.append({"path": str(dest), "sha256": sha_expected})

    # Write receipt.
    receipt = {
        "at": datetime.now().astimezone().isoformat(),
        "targets": published_targets,
    }
    _fail_point("before_receipt")
    atomic_write_json(gen_dir / "published", receipt)

    return receipt


def ensure_published(h: Path) -> dict:
    """Check CURRENT gen is published; if not, try to publish. Never raises."""
    gen = current_generation(h)
    if gen is None:
        return {"status": "ok"}

    gen_dir = _gen_dir(h, gen)
    if not gen_dir.exists():
        return {"status": "failed", "code": "PUBLISH_FAILED"}

    receipt_path = gen_dir / "published"
    if receipt_path.exists():
        return {"status": "ok"}

    # Try to republish.
    try:
        publish(h, gen)
        return {"status": "republished"}
    except Exception:
        return {"status": "failed", "code": "PUBLISH_FAILED"}


def cleanup_uncommitted(h: Path) -> None:
    """Delete ``.staging`` dirs and gen dirs with number > CURRENT."""
    gen_parent = h / "gen"
    if not gen_parent.exists():
        return
    cur = current_generation(h) or 0
    for entry in gen_parent.iterdir():
        if entry.name.endswith(".staging"):
            shutil.rmtree(entry, ignore_errors=True)
        elif entry.is_dir():
            try:
                n = int(entry.name)
                if n > cur:
                    shutil.rmtree(entry, ignore_errors=True)
            except ValueError:
                pass


# ---------------------------------------------------------------------------
# Output filename with same-day dedup
# ---------------------------------------------------------------------------

def output_filename(out_dir: Path, date: str, fmt: str) -> Path:
    """Return ``agenda-YYYY-MM-DD.fmt``; same-day reruns get ``-2``, ``-3``, etc."""
    base = out_dir / f"agenda-{date}.{fmt}"
    if not base.exists():
        return base
    suffix = 2
    while True:
        candidate = out_dir / f"agenda-{date}-{suffix}.{fmt}"
        if not candidate.exists():
            return candidate
        suffix += 1


# ---------------------------------------------------------------------------
# Execution lock
# ---------------------------------------------------------------------------

@contextmanager
def acquire_lock(h: Path, *, stale_after_s: int = 1200) -> Iterator[Path]:
    """Context manager: create ``runs/lock``; release on exit.

    The lock file contains pid and timestamp. If it already exists and is not
    stale, raises :class:`Locked`. Stale locks are taken over.
    """
    lock_dir = h / "runs"
    lock_dir.mkdir(parents=True, exist_ok=True)
    lock_path = lock_dir / "lock"

    lock_data = json.dumps({
        "pid": os.getpid(),
        "at": datetime.now().astimezone().isoformat(),
    })

    # Try atomic create.
    try:
        fd = os.open(lock_path, os.O_CREAT | os.O_EXCL | os.O_WRONLY)
        try:
            os.write(fd, lock_data.encode("utf-8"))
            os.fsync(fd)
        finally:
            os.close(fd)
    except FileExistsError:
        # Lock exists — check staleness.
        try:
            existing = json.loads(lock_path.read_text(encoding="utf-8"))
            locked_at = datetime.fromisoformat(existing["at"])
            elapsed = (datetime.now().astimezone() - locked_at).total_seconds()
            if elapsed < stale_after_s:
                raise Locked(f"lock held by pid {existing.get('pid')}")
        except (json.JSONDecodeError, KeyError, ValueError, TypeError):
            pass  # corrupt lock — take over

        # Stale or corrupt: take over.
        os.replace(
            _write_tmp_in(lock_path.parent, lock_data),
            lock_path,
        )

    try:
        yield lock_path
    finally:
        lock_path.unlink(missing_ok=True)


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------

def _write_tmp_in(directory: Path, text: str) -> str:
    """Write *text* to a temp file in *directory*; return the temp path."""
    fd, tmp = tempfile.mkstemp(dir=directory)
    try:
        os.write(fd, text.encode("utf-8"))
        os.fsync(fd)
    finally:
        os.close(fd)
    return tmp
