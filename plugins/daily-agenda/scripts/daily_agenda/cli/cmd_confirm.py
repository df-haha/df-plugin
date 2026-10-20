"""confirm command — apply a batch of todo actions from digest-page text (C5)."""

from __future__ import annotations

import sys
from pathlib import Path

from daily_agenda import ledger, state
from daily_agenda.confirm import (
    INPUT_BYTE_CAP,
    ParsedConfirm,
    parse_confirm_text,
    validate_confirm,
)
from daily_agenda.fence import fence_untrusted
from ._common import Deps, emit
from .cmd_ledger_ops import _commit_ledger_change


def handle(argv: list[str], deps: Deps) -> int:
    """Handle ``DA confirm --from-stdin``."""
    # Reject any flag other than --from-stdin.
    if argv != ["--from-stdin"]:
        return emit(
            {"ok": False, "code": "ARG_ERROR", "detail": "usage: confirm --from-stdin"},
            exit_code=2,
        )

    # Read from stdin with byte cap.
    try:
        raw: bytes
        if hasattr(sys.stdin, "buffer"):
            raw = sys.stdin.buffer.read(INPUT_BYTE_CAP + 1)
        else:
            # StringIO in tests — encode to check byte limit.
            raw = sys.stdin.read(INPUT_BYTE_CAP + 1).encode("utf-8")
    except Exception:
        return emit(
            {"ok": False, "code": "ARG_ERROR", "detail": "無法讀取 stdin"},
            exit_code=2,
        )

    if len(raw) > INPUT_BYTE_CAP:
        return emit(
            {"ok": False, "code": "ARG_ERROR", "detail": f"輸入超過 {INPUT_BYTE_CAP} bytes"},
            exit_code=2,
        )

    # Decode, stripping BOM if present.
    try:
        text = raw.decode("utf-8-sig")
    except UnicodeDecodeError:
        return emit(
            {"ok": False, "code": "ARG_ERROR", "detail": "輸入不是有效的 UTF-8"},
            exit_code=2,
        )

    # Parse.
    try:
        parsed = parse_confirm_text(text)
    except ValueError as exc:
        return emit(
            {"ok": False, "code": "ARG_ERROR", "detail": str(exc)},
            exit_code=2,
        )

    # Load state and ledger.
    st = state.load_state(deps.home)
    ldg = state.load_ledger(deps.home)

    # Stale report check (reuses ledger.check_report_code).
    try:
        ledger.check_report_code(st, parsed.report_code)
    except ledger.StaleReport:
        return emit({"ok": False, "code": "STALE_REPORT"})

    # Validate all items before applying any.
    errors = validate_confirm(parsed, state=st, ledger=ldg)
    if errors:
        detail = [{"no": e.no, "reason": e.reason} for e in errors]
        return emit({"ok": False, "code": "CONFIRM_INVALID", "detail": detail})

    # All validated — nothing to apply?
    report_index = st.get("report_index", {})
    current_code = st.get("report_code")
    total_actions = (
        len(parsed.done_numbers)
        + len(parsed.drop_numbers)
        + len(parsed.priority_changes)
    )

    # Count page-visible items (open/maybe_done) for unchanged_count.
    _VISIBLE = {"open", "maybe_done"}
    ldg_by_id = {t["id"]: t for t in ldg}
    visible_count = sum(
        1 for tid in report_index.values()
        if ldg_by_id.get(tid, {}).get("status") in _VISIBLE
    )

    if total_actions == 0:
        # 全部保留 — no changes, don't write a new generation.
        return emit({
            "ok": True,
            "code": None,
            "report_code": current_code,
            "results": [],
            "unchanged_count": visible_count,
        })

    # Apply atomically.
    now = deps.now_factory()
    new_ldg = ldg  # will be replaced by each operation
    results: list[dict] = []

    try:
        # 完成 (mark-done)
        for n in sorted(parsed.done_numbers):
            tid = report_index[str(n)]
            new_ldg, _ = ledger.mark_done(
                new_ldg, st, report_code=None, todo_ids=[tid], now=now,
            )
            title = _find_title(new_ldg, tid)
            results.append({
                "no": n, "todo_id": tid, "action": "done",
                "title": fence_untrusted(title),
            })

        # 刪除 (dismiss)
        for n in sorted(parsed.drop_numbers):
            tid = report_index[str(n)]
            new_ldg = ledger.dismiss(new_ldg, tid, now=now)
            title = _find_title(new_ldg, tid)
            results.append({
                "no": n, "todo_id": tid, "action": "dismissed",
                "title": fence_untrusted(title),
            })

        # 改優先 (set_priority)
        for n, pri in sorted(parsed.priority_changes, key=lambda x: x[0]):
            tid = report_index[str(n)]
            new_ldg = ledger.set_priority(new_ldg, tid, pri, now=now)
            title = _find_title(new_ldg, tid)
            results.append({
                "no": n, "todo_id": tid, "action": "priority",
                "title": fence_untrusted(title), "priority": pri,
            })
    except (ledger.IllegalTransition, KeyError, ValueError) as exc:
        return emit({
            "ok": False,
            "code": "CONFIRM_INVALID",
            "detail": [{"no": None, "reason": type(exc).__name__}],
        })

    # Commit.
    rc = _commit_ledger_change(deps, new_ldg)
    if rc != -1:
        return rc

    touched = {r["no"] for r in results}
    unchanged = visible_count - len(touched)

    return emit({
        "ok": True,
        "code": None,
        "report_code": current_code,
        "results": results,
        "unchanged_count": max(unchanged, 0),
    })


def _find_title(ldg: list[dict], todo_id: str) -> str:
    """Find a todo's title in the ledger."""
    for t in ldg:
        if t["id"] == todo_id:
            return t.get("title", "")
    return ""
