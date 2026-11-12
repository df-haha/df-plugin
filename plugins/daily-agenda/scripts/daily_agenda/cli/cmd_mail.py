"""mail folders | mail select --set/--add/--remove REF|N..."""

from __future__ import annotations

import json
from datetime import datetime, timedelta
from typing import Any

from daily_agenda import state
from daily_agenda.folder_walk import walk_folders, WalkResult, SKIP_WELL_KNOWN, _WELL_KNOWN_NAMES
from daily_agenda.fsutil import atomic_write_json
from ._common import Deps, emit, require_consent


# All canonical well-known names that Graph accepts as folder references
_ALL_WELL_KNOWN_REFS: frozenset[str] = frozenset(set(_WELL_KNOWN_NAMES.values()))

# Numbered listing written by `mail folders`; `mail select` resolves plain
# numbers against it.  Graph folder ids are ~150 characters, and selecting
# dozens of them on one command line broke the Bash tool on Windows.
_LISTING_FILE = "mail-folder-listing.json"
_LISTING_MAX_AGE = timedelta(hours=12)


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
    for n, f in enumerate(walk.folders, start=1):
        ref = _folder_ref(f)
        result_folders.append({
            "n": n,
            "ref": ref,
            "path": f["path"],
            "total": f.get("total", 0),
            "unread": f.get("unread", 0),
            "well_known": f.get("well_known"),
            "skip_recommended": f.get("well_known") in SKIP_WELL_KNOWN,
            "selected": ref in selected_refs,
        })

    atomic_write_json(deps.home / _LISTING_FILE, {
        "listed_at": deps.now_factory().isoformat(),
        "items": [{"n": f["n"], "ref": f["ref"]} for f in result_folders],
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

    allow_incomplete: bool = "--allow-incomplete" in argv
    # Strip the flag so it doesn't leak into refs
    argv = [a for a in argv if a != "--allow-incomplete"]

    flag = next((f for f in ("--set", "--add", "--remove") if f in argv), None)
    if flag is None:
        return emit(
            {"ok": False, "code": "ARG_ERROR",
             "detail": "usage: mail select --set REF|N...|--add REF|N...|--remove REF|N..."},
            exit_code=2,
        )
    tokens = argv[argv.index(flag) + 1:]
    if not tokens:
        detail = ("至少要選一個信箱資料夾；若要停用信件，請用 config set mail.enabled=false"
                  if flag == "--set" else f"{flag} requires a ref or listing number")
        return emit({"ok": False, "code": "ARG_ERROR", "detail": detail}, exit_code=2)
    refs, err = _resolve_numbers(tokens, deps)
    if err:
        return emit({"ok": False, "code": "ARG_ERROR", "detail": err}, exit_code=2)
    mode = flag[2:]
    # --remove does NOT validate: removing a stale ref is the recovery
    # path for MAIL_FOLDER_MISSING — the folder won't be in the listing.
    refs_to_validate: list[str] = [] if mode == "remove" else refs

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
                    if not inventory_complete:
                        if not allow_incomplete:
                            return emit(
                                {"ok": False, "code": "MAIL_FOLDERS_INCOMPLETE",
                                 "message": "信箱資料夾清單不完整（部分子資料夾讀取失敗或已截斷），"
                                            "無法確認這些 ref 是否存在。"
                                            "加上 --allow-incomplete 可強制接受。",
                                 "detail": f"unverifiable refs: {', '.join(bad)}"},
                            )
                        # --allow-incomplete: user confirmed the refs exist;
                        # store them as-is (they may be in a subtree we
                        # couldn't list).
                    else:
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
    existing = list(cfg.get("mail", {}).get("folders", ["inbox"]))
    if mode == "set":
        new_list = list(refs)
    elif mode == "add":
        new_list = existing + [r for r in refs if r not in existing]
    else:
        new_list = [f for f in existing if f not in refs]
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


def _resolve_numbers(tokens: list[str], deps: Deps) -> tuple[list[str], str | None]:
    """Map plain ASCII numbers to refs from the latest `mail folders` listing.

    Anything else is taken as a raw ref (well-known name or Graph id), so stale
    refs can still be removed.  Graph ids are never all-numeric.
    """
    def is_number(tok: str) -> bool:
        return tok.isascii() and tok.isdigit()

    if not any(is_number(t) for t in tokens):
        return list(dict.fromkeys(tokens)), None
    by_n = _load_listing(deps)
    if by_n is None:
        return [], "numbers need a fresh listing: run `mail folders` first"
    refs: list[str] = []
    for tok in tokens:
        if not is_number(tok):
            refs.append(tok)
            continue
        ref = by_n.get(int(tok))
        if ref is None:
            return [], f"number {tok} is not in the latest listing (1..{len(by_n)})"
        refs.append(ref)
    return list(dict.fromkeys(refs)), None


def _load_listing(deps: Deps) -> dict[int, str] | None:
    """n → ref from the latest `mail folders`, or None if missing/corrupt/stale."""
    try:
        data = json.loads((deps.home / _LISTING_FILE).read_text(encoding="utf-8"))
        listed_at = datetime.fromisoformat(str(data["listed_at"]))
        if deps.now_factory() - listed_at > _LISTING_MAX_AGE:
            return None
        return {int(i["n"]): str(i["ref"]) for i in data["items"]}
    except (OSError, ValueError, TypeError, KeyError):
        return None


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
