"""draft new --kind criteria|profile|coverage / draft show --file F

Creates draft files in ``<home>/runs/drafts/<timestamp>/`` so Claude can
Write to a path that satisfies the lock-core hook's allowlist
(``runs/…/criteria-draft.md``, ``profile-draft.md``, ``coverage-draft.json``).

``draft show`` returns fenced text so Claude never needs to Read runs/ files
directly (which would lock the session).
"""

from __future__ import annotations

import secrets
from pathlib import Path

from daily_agenda import criteria, state
from daily_agenda.fence import fence_untrusted
from daily_agenda.fsutil import atomic_write_text
from ._common import Deps, emit, require_consent, validate_file_flag

# Allowed draft filenames — must match lock-core hook's checkTaintedWrite allowlist.
_KIND_FILENAME: dict[str, str] = {
    "criteria": "criteria-draft.md",
    "profile": "profile-draft.md",
    "coverage": "coverage-draft.json",
}


def handle(argv: list[str], deps: Deps) -> int:
    if not argv:
        return emit(
            {"ok": False, "code": "ARG_ERROR", "detail": "usage: draft new|show"},
            exit_code=2,
        )

    sub = argv[0]

    if sub == "new":
        return _handle_new(argv[1:], deps)
    elif sub == "show":
        return _handle_show(argv[1:], deps)
    else:
        return emit(
            {"ok": False, "code": "ARG_ERROR", "detail": "usage: draft new|show"},
            exit_code=2,
        )


def _handle_new(argv: list[str], deps: Deps) -> int:
    """Create a new draft file under ``<home>/runs/drafts/<id>/``."""
    cfg = state.load_config(deps.home)
    rc = require_consent(cfg)
    if rc is not None:
        return rc

    kind: str | None = None
    for i, arg in enumerate(argv):
        if arg == "--kind" and i + 1 < len(argv):
            kind = argv[i + 1]

    if kind is None or kind not in _KIND_FILENAME:
        return emit(
            {"ok": False, "code": "ARG_ERROR",
             "detail": f"--kind must be one of: {', '.join(sorted(_KIND_FILENAME))}"},
            exit_code=2,
        )

    filename = _KIND_FILENAME[kind]

    # Create drafts directory
    now = deps.now_factory()
    draft_id = now.strftime("%Y%m%d-%H%M") + "-" + secrets.token_hex(2)
    draft_dir = deps.home / "runs" / "drafts" / draft_id
    draft_dir.mkdir(parents=True, exist_ok=True)

    draft_path = draft_dir / filename

    # Seed with current version if available
    seed = ""
    if kind == "criteria":
        seed = criteria.read_latest(deps.home) or ""
    elif kind == "profile":
        from daily_agenda import profile as _profile
        seed = _profile.read_latest(deps.home) or ""
    elif kind == "coverage":
        seed = "{}"

    atomic_write_text(draft_path, seed)

    return emit({
        "ok": True, "code": None,
        "draft_path": str(draft_path),
        "kind": kind,
        "seeded": bool(seed and seed != "{}"),
    })


def _handle_show(argv: list[str], deps: Deps) -> int:
    """Return fenced text of a draft file so Claude never Reads runs/ directly."""
    cfg = state.load_config(deps.home)
    rc = require_consent(cfg)
    if rc is not None:
        return rc

    file_path, err = validate_file_flag(argv, deps.home, policy_key="draft show")
    if err is not None:
        return err
    if not file_path:
        return emit(
            {"ok": False, "code": "ARG_ERROR", "detail": "usage: draft show --file F"},
            exit_code=2,
        )

    # Additional guard: only known draft filenames
    p = Path(file_path)
    allowed_names = frozenset(_KIND_FILENAME.values())
    if p.name not in allowed_names:
        return emit(
            {"ok": False, "code": "ARG_ERROR",
             "detail": f"--file must be a known draft filename: {', '.join(sorted(allowed_names))}"},
            exit_code=2,
        )

    try:
        text = p.read_text(encoding="utf-8")
    except FileNotFoundError:
        return emit(
            {"ok": False, "code": "ARG_ERROR", "detail": f"file not found: {file_path}"},
            exit_code=2,
        )

    return emit({
        "ok": True, "code": None,
        "draft_path": str(file_path),
        "text": fence_untrusted(text),
    })
