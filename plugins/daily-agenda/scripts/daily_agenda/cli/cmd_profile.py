"""profile show|list|save|restore|coverage|cadence|next-question commands."""

from __future__ import annotations

from pathlib import Path

from daily_agenda import profile, state
from daily_agenda.fence import fence_untrusted
from ._common import Deps, emit, require_consent, validate_file_flag


def handle(argv: list[str], deps: Deps) -> int:
    if not argv:
        return emit(
            {"ok": False, "code": "ARG_ERROR",
             "detail": "usage: profile show|list|save|restore|coverage|cadence|next-question"},
            exit_code=2,
        )

    sub = argv[0]

    if sub == "list":
        versions = profile.list_versions(deps.home)
        return emit({"ok": True, "code": None, "versions": versions,
                      "latest": versions[-1] if versions else None})

    elif sub == "show":
        cfg = state.load_config(deps.home)
        rc = require_consent(cfg)
        if rc is not None:
            return rc

        version: int | None = None
        for i, arg in enumerate(argv):
            if arg == "--version" and i + 1 < len(argv):
                try:
                    version = int(argv[i + 1])
                except ValueError:
                    return emit({"ok": False, "code": "ARG_ERROR",
                                  "detail": "--version must be integer"}, exit_code=2)

        if version is None:
            version = profile.latest_version(deps.home)
        if version == 0:
            return emit({"ok": False, "code": "PROFILE_MISSING",
                          "detail": "no profile versions found"})

        text = profile.read_version(deps.home, version)
        if text is None:
            return emit({"ok": False, "code": "CONFIG_MISSING",
                          "detail": f"version {version} not found"})

        # Fence: profile may contain inferred third-party names
        return emit({"ok": True, "code": None, "version": version,
                      "text": fence_untrusted(text)})

    elif sub == "save":
        file_path, err = validate_file_flag(argv, deps.home, policy_key="profile save")
        if err is not None:
            return err
        if not file_path:
            return emit({"ok": False, "code": "ARG_ERROR",
                          "detail": "usage: profile save --file F"}, exit_code=2)

        try:
            text = Path(file_path).read_text(encoding="utf-8")
        except FileNotFoundError:
            return emit({"ok": False, "code": "ARG_ERROR",
                          "detail": f"file not found: {file_path}"}, exit_code=2)

        try:
            v = profile.save(deps.home, text)
        except ValueError as exc:
            return emit({"ok": False, "code": "ARG_ERROR",
                          "detail": str(exc)}, exit_code=2)
        return emit({"ok": True, "code": None, "version": v})

    elif sub == "restore":
        version = None
        for i, arg in enumerate(argv):
            if arg == "--version" and i + 1 < len(argv):
                try:
                    version = int(argv[i + 1])
                except ValueError:
                    return emit({"ok": False, "code": "ARG_ERROR",
                                  "detail": "--version must be integer"}, exit_code=2)

        if version is None:
            return emit({"ok": False, "code": "ARG_ERROR",
                          "detail": "usage: profile restore --version N"}, exit_code=2)

        try:
            new_v = profile.restore(deps.home, version)
        except FileNotFoundError:
            return emit({"ok": False, "code": "CONFIG_MISSING",
                          "detail": f"version {version} not found"})

        return emit({"ok": True, "code": None, "version": new_v,
                      "restored_from": version})

    elif sub == "coverage":
        return _handle_coverage(argv[1:], deps)

    elif sub == "cadence":
        return _handle_cadence(argv[1:], deps)

    elif sub == "next-question":
        gen = state.current_generation(deps.home)
        # Parse optional --context
        context: str | None = None
        for i, arg in enumerate(argv):
            if arg == "--context" and i + 1 < len(argv):
                context = argv[i + 1]
        result = profile.next_question(deps.home, current_generation=gen, context=context)
        # Record the ask so the rate limit is enforced
        if result.get("question") is not None:
            profile.record_asked(deps.home, gen)
        return emit({"ok": True, "code": None, **result})

    else:
        return emit(
            {"ok": False, "code": "ARG_ERROR",
             "detail": "usage: profile show|list|save|restore|coverage|cadence|next-question"},
            exit_code=2,
        )


# ---------------------------------------------------------------------------
# Subcommand helpers
# ---------------------------------------------------------------------------


def _fence_coverage(cov: dict) -> dict:
    """Fence model-authored strings in coverage data."""
    import copy
    result = copy.deepcopy(cov)
    dims = result.get("dimensions", {})
    for dim_id, entry in dims.items():
        notes = entry.get("notes")
        if notes:
            entry["notes"] = fence_untrusted(notes)
    top_notes = result.get("notes")
    if top_notes:
        result["notes"] = fence_untrusted(top_notes)
    return result


def _handle_coverage(argv: list[str], deps: Deps) -> int:
    if not argv:
        # Show current coverage — fence the notes (model-authored strings)
        cov = profile.load_coverage(deps.home)
        if cov is None:
            return emit({"ok": False, "code": "CONFIG_MISSING",
                          "detail": "no coverage data found"})
        # Fence notes fields that may contain model-inferred content
        fenced_cov = _fence_coverage(cov)
        return emit({"ok": True, "code": None, "coverage": fenced_cov})

    sub = argv[0]

    if sub == "save":
        file_path, err = validate_file_flag(argv, deps.home, policy_key="profile coverage save")
        if err is not None:
            return err
        if not file_path:
            return emit({"ok": False, "code": "ARG_ERROR",
                          "detail": "usage: profile coverage save --file F"}, exit_code=2)

        import json
        try:
            text = Path(file_path).read_text(encoding="utf-8")
            data = json.loads(text)
        except FileNotFoundError:
            return emit({"ok": False, "code": "ARG_ERROR",
                          "detail": f"file not found: {file_path}"}, exit_code=2)
        except json.JSONDecodeError as exc:
            return emit({"ok": False, "code": "ARG_ERROR",
                          "detail": f"invalid JSON: {exc}"}, exit_code=2)

        try:
            profile.save_coverage(deps.home, data)
        except ValueError as exc:
            return emit({"ok": False, "code": "ARG_ERROR",
                          "detail": str(exc)}, exit_code=2)

        return emit({"ok": True, "code": None})

    return emit({"ok": False, "code": "ARG_ERROR",
                  "detail": "usage: profile coverage [save --file F]"}, exit_code=2)


def _handle_cadence(argv: list[str], deps: Deps) -> int:
    if not argv:
        c = profile.load_cadence(deps.home)
        return emit({"ok": True, "code": None, "cadence": c})

    set_val: str | None = None
    for i, arg in enumerate(argv):
        if arg == "--set" and i + 1 < len(argv):
            set_val = argv[i + 1]

    if set_val is None:
        return emit({"ok": False, "code": "ARG_ERROR",
                      "detail": "usage: profile cadence --set now|agenda|refine|later"},
                    exit_code=2)

    try:
        profile.save_cadence(deps.home, set_val)
    except ValueError as exc:
        return emit({"ok": False, "code": "ARG_ERROR",
                      "detail": str(exc)}, exit_code=2)

    return emit({"ok": True, "code": None, "cadence": set_val})
