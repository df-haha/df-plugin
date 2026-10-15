"""mail folders | mail select --set/--add/--remove REF..."""

from __future__ import annotations

from typing import Any

from daily_agenda import state
from daily_agenda.folder_walk import walk_folders, WalkResult, SKIP_WELL_KNOWN, _WELL_KNOWN_NAMES
from ._common import Deps, emit, require_consent


# All canonical well-known names that Graph accepts as folder references
_ALL_WELL_KNOWN_REFS: frozenset[str] = frozenset(set(_WELL_KNOWN_NAMES.values()))


# ---------------------------------------------------------------------------
# mail folders (★ command — reads mailbox structure)
# ---------------------------------------------------------------------------

def handle_mail(argv: list[str], deps: Deps) -> int:
    if not argv:
        return emit(
            {"ok": False, "code": "ARG_ERROR", "detail": "usage: mail folders|select"},
            exit_code=2,
        )

    sub = argv[0]
    if sub == "folders":
        return _handle_folders(argv[1:], deps)
    elif sub == "select":
        return _handle_select(argv[1:], deps)
    else:
        return emit(
            {"ok": False, "code": "ARG_ERROR", "detail": "usage: mail folders|select"},
            exit_code=2,
        )


def _handle_folders(argv: list[str], deps: Deps) -> int:
    """List mailbox folders for user selection."""
    cfg = state.load_config(deps.home)
    rc = require_consent(cfg)
    if rc is not None:
        return rc

    if not deps.graph_factory:
        return emit({"ok": False, "code": "DF_GRAPH_NOT_REGISTERED"})

    try:
        graph = deps.graph_factory()
    except Exception as exc:
        code = getattr(exc, "code", "DF_GRAPH_NOT_REGISTERED")
        return emit({"ok": False, "code": code})

    try:
        walk = walk_folders(graph.folder_list)
    except Exception as exc:
        code = getattr(exc, "code", "GRAPH_FAILED")
        return emit({"ok": False, "code": code})

    # Mark currently selected folders
    selected_refs = set(cfg.get("mail", {}).get("folders", ["inbox"]))

    result_folders: list[dict[str, Any]] = []
    for f in walk.folders:
        ref = _folder_ref(f)
        result_folders.append({
            "ref": ref,
            "path": f["path"],
            "total": f.get("total", 0),
            "unread": f.get("unread", 0),
            "well_known": f.get("well_known"),
            "skip_recommended": f.get("well_known") in SKIP_WELL_KNOWN,
            "selected": ref in selected_refs,
        })

    result: dict[str, Any] = {
        "ok": True, "code": None,
        "folders": result_folders,
        "complete": walk.complete,
    }
    if walk.skipped:
        result["skipped"] = walk.skipped
    if walk.truncated:
        result["truncated"] = walk.truncated
    return emit(result)


def _resolve_folder_map(deps: Deps) -> tuple[dict[str, str] | None, bool]:
    """List folders from Graph and return ``({ref: display_path}, complete)``.

    Returns ``(None, False)`` if graph is not available.
    """
    if not deps.graph_factory:
        return None, False
    try:
        graph = deps.graph_factory()
        walk = walk_folders(graph.folder_list)
    except Exception:
        return None, False
    return {_folder_ref(f): f["path"] for f in walk.folders}, walk.complete


def _handle_select(argv: list[str], deps: Deps) -> int:
    """Set/add/remove selected mail folders."""
    cfg = state.load_config(deps.home)
    rc = require_consent(cfg)
    if rc is not None:
        return rc

    mode: str = ""
    refs_to_validate: list[str] = []

    if "--set" in argv:
        mode = "set"
        idx = argv.index("--set")
        refs = argv[idx + 1:]
        if not refs:
            return emit(
                {"ok": False, "code": "ARG_ERROR",
                 "detail": "至少要選一個信箱資料夾；若要停用信件，請用 config set mail.enabled=false"},
                exit_code=2,
            )
        refs_to_validate = refs
    elif "--add" in argv:
        mode = "add"
        idx = argv.index("--add")
        if idx + 1 >= len(argv):
            return emit({"ok": False, "code": "ARG_ERROR", "detail": "--add requires a ref"}, exit_code=2)
        refs_to_validate = [argv[idx + 1]]
    elif "--remove" in argv:
        mode = "remove"
        idx = argv.index("--remove")
        if idx + 1 >= len(argv):
            return emit({"ok": False, "code": "ARG_ERROR", "detail": "--remove requires a ref"}, exit_code=2)
        # --remove does NOT validate: removing a stale ref is the recovery
        # path for MAIL_FOLDER_MISSING — the folder won't be in the listing.
    else:
        return emit(
            {"ok": False, "code": "ARG_ERROR",
             "detail": "usage: mail select --set REF...|--add REF|--remove REF"},
            exit_code=2,
        )

    # Validate refs for --set and --add (not --remove)
    folder_map: dict[str, str] | None = None
    inventory_complete: bool = False
    if refs_to_validate:
        # Well-known names always valid (Graph accepts them regardless of locale)
        unknown = [r for r in refs_to_validate if r not in _ALL_WELL_KNOWN_REFS]
        if unknown:
            folder_map, inventory_complete = _resolve_folder_map(deps)
            if folder_map is not None:
                bad = [r for r in unknown if r not in folder_map]
                if bad:
                    if not inventory_complete and "--allow-incomplete" not in argv:
                        return emit(
                            {"ok": False, "code": "MAIL_FOLDERS_INCOMPLETE",
                             "message": "信箱資料夾清單不完整（部分子資料夾讀取失敗或已截斷），"
                                        "無法確認這些 ref 是否存在。"
                                        "加上 --allow-incomplete 可強制接受。",
                             "detail": f"unverifiable refs: {', '.join(bad)}"},
                        )
                    return emit(
                        {"ok": False, "code": "ARG_ERROR",
                         "detail": f"找不到這些信箱資料夾 ref：{', '.join(bad)}"},
                        exit_code=2,
                    )
            else:
                # Graph unavailable and we have non-well-known refs — refuse
                # because we can't validate them; storing a bad ref silently
                # would cause MAIL_FOLDER_MISSING on every run.
                return emit(
                    {"ok": False, "code": "DF_GRAPH_NOT_REGISTERED",
                     "detail": "無法驗證信箱資料夾（Outlook 連線失敗），請確認 df-graph 登入後再試"},
                )

    # Apply the change
    if mode == "set":
        cfg.setdefault("mail", {})["folders"] = list(refs_to_validate)
    elif mode == "add":
        ref = refs_to_validate[0]
        existing = list(cfg.get("mail", {}).get("folders", ["inbox"]))
        if ref not in existing:
            existing.append(ref)
        cfg.setdefault("mail", {})["folders"] = existing
    elif mode == "remove":
        ref = argv[argv.index("--remove") + 1]
        existing = list(cfg.get("mail", {}).get("folders", ["inbox"]))
        new_list = [f for f in existing if f != ref]
        if not new_list:
            return emit(
                {"ok": False, "code": "ARG_ERROR",
                 "detail": "至少要保留一個信箱資料夾；若要停用信件，請用 config set mail.enabled=false"},
                exit_code=2,
            )
        cfg.setdefault("mail", {})["folders"] = new_list

    # Store folder labels automatically for status display
    selected = cfg.get("mail", {}).get("folders", [])
    if folder_map is None and refs_to_validate:
        folder_map, _ = _resolve_folder_map(deps)
    if folder_map:
        labels = {ref: folder_map[ref] for ref in selected if ref in folder_map}
        # Well-known names not in the listing get their canonical name as label
        for ref in selected:
            if ref not in labels and ref in _ALL_WELL_KNOWN_REFS:
                labels[ref] = ref
        cfg.setdefault("mail", {})["folder_labels"] = labels

    state.save_config(deps.home, cfg)
    return emit({"ok": True, "code": None, "selected_count": len(selected),
                 "folders": selected})


def _folder_ref(folder: dict[str, Any]) -> str:
    """Return a stable ref for a folder.

    Well-known folders use their well-known name (e.g. ``"inbox"``)
    so the ref survives mailbox migrations and matches the default
    config value.  Other folders use their Graph API id.
    """
    wk = folder.get("well_known")
    if wk:
        return wk
    return folder.get("id", "")
