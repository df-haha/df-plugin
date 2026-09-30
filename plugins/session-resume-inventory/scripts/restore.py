#!/usr/bin/env python3
"""Reopen sessions from an inventory snapshot as Orca terminal tabs.

Default is a dry run that only prints the plan. `--apply` creates the Orca tabs; `--open-orca`
only launches Orca. Sessions whose ID is already live are never opened a second time.
"""
from __future__ import annotations

import argparse
import json
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inventory import (SNAPSHOT_DIR, argv_resume_sid, collect_claude, collect_codex,  # noqa: E402
                       is_claude_main, is_codex_main, live_pids, normalize_path, orca_exe, proc_args,
                       run_full)

TITLE_LEN = 40


def resume_command(session: dict) -> str:
    head = ["claude", "--resume", session["sid"]] if session["runtime"] == "claude" \
        else ["codex", "resume", session["sid"]]
    return shlex.join(head + list(session.get("flags") or []))


def build_plan(sessions: list[dict], live_sids: set[str], worktrees: dict[str, str]) -> dict[str, list[dict]]:
    plan: dict[str, list[dict]] = {k: [] for k in ("already_live", "orca", "unregistered", "terminal", "skipped")}
    seen: set[str] = set()
    for s in sessions:
        sid = s.get("sid")
        if not sid or not s.get("resumable"):
            plan["skipped"].append(s)
            continue
        if sid in seen:
            continue
        seen.add(sid)
        if sid in live_sids:
            plan["already_live"].append(s)
        elif not s.get("in_orca"):
            plan["terminal"].append(s)
        elif normalize_path(s["cwd"]) in worktrees:
            plan["orca"].append({**s, "selector": worktrees[normalize_path(s["cwd"])]})
        else:
            plan["unregistered"].append(s)
    return plan


def orca_json(args: list[str], timeout: int = 30) -> dict:
    exe = orca_exe()
    if not exe:
        return {"ok": False, "error": {"message": "Orca CLI（orca-ide）不在 PATH"}}
    out, err = run_full([exe, *args, "--json"], timeout)
    try:
        return json.loads(out)
    except json.JSONDecodeError:
        return {"ok": False, "error": {"message": (f"{out} {err}".strip() or "無輸出")[:500]}}


def orca_reachable() -> bool:
    res = orca_json(["status"])
    return bool(res.get("ok") and res["result"].get("runtime", {}).get("reachable"))


def orca_worktrees() -> dict[str, str]:
    res = orca_json(["worktree", "list"])
    if not res.get("ok"):
        return {}
    return {normalize_path(w["path"]): f"id:{w['id']}" for w in res["result"].get("worktrees", []) if w.get("path")}


def live_session_ids() -> set[str]:
    """Live session IDs from session state files plus `--resume <id>` argv, so a tab still sitting
    on a startup prompt (before its state file names the session) is not opened twice."""
    pids = live_pids()
    alive = set(pids)
    sids: set[str] = set()
    for sessions in (collect_claude(pids), collect_codex(pids)):
        sids |= {e["sid"] for e in sessions.values() if e.get("sid") and any(p in alive for p in e["pids"])}
    for pid in pids:
        args = proc_args(pid)
        if is_claude_main(args) or is_codex_main(args):
            sid = argv_resume_sid(args)
            if sid:
                sids.add(sid)
    return sids


def load_snapshot(path: Path | None) -> tuple[Path, dict]:
    if path is None:
        candidates = sorted(SNAPSHOT_DIR.glob("*.json"))
        if not candidates:
            sys.exit(f"找不到快照：{SNAPSHOT_DIR} 是空的。關機前先跑 inventory.py --save。")
        path = candidates[-1]
    return path, json.loads(path.read_text(encoding="utf-8"))


def title_of(s: dict) -> str:
    title = s.get("name") or s.get("hint") or f"{s['runtime']} {s['sid'][:8]}"
    return title if len(title) <= TITLE_LEN else title[:TITLE_LEN] + "…"


def describe(s: dict) -> str:
    extra = f"｜未保留參數：{' '.join(s['dropped'])}" if s.get("dropped") else ""
    busy = "｜存檔時 busy，當時那一輪會中斷，開回來要檢查" if s.get("status") in ("busy", "shell") else ""
    return f"- [{s['runtime']}] {title_of(s)}\n  - cwd: {s['cwd']}\n  - 指令: {resume_command(s)}{extra}{busy}"


def print_plan(snap_path: Path, snap: dict, plan: dict[str, list[dict]]) -> None:
    print(f"快照：{snap_path}（存於 {snap.get('saved_at', '?')}）\n")
    sections = [
        ("orca", "要在 Orca 開回的 session"),
        ("unregistered", "原本在 Orca、但路徑沒登記在 Orca（不自動處理，請手動開）"),
        ("terminal", "原本不在 Orca（terminal 復原是第二版，請手動開）"),
    ]
    for key, label in sections:
        print(f"## {label}：{len(plan[key])} 個")
        for s in plan[key]:
            print(describe(s))
        print()
    print(f"已在跑、略過：{len(plan['already_live'])} 個"
          + (f"（{'、'.join(title_of(s) for s in plan['already_live'])}）" if plan["already_live"] else ""))
    if plan["skipped"]:
        print(f"無法復原（沒有 session ID 或找不到對話紀錄）：{len(plan['skipped'])} 個")


def apply_plan(plan: dict[str, list[dict]]) -> int:
    failures = 0
    for s in plan["orca"]:
        res = orca_json(["terminal", "create", "--worktree", s["selector"], "--title", title_of(s),
                         "--command", resume_command(s)], timeout=60)
        if res.get("ok"):
            print(f"✅ 已開：{title_of(s)} → {res['result']['terminal']['handle']}")
        else:
            failures += 1
            print(f"❌ 失敗：{title_of(s)} → {res.get('error', {}).get('message', '未知錯誤')}")
    return failures


def main() -> None:
    parser = argparse.ArgumentParser(description="從快照把沒在跑的 session 開回 Orca（預設只列計畫）")
    parser.add_argument("--snapshot", type=Path, help="快照路徑（預設取最新一份）")
    parser.add_argument("--apply", action="store_true", help="實際在 Orca 開分頁")
    parser.add_argument("--open-orca", action="store_true", help="只啟動 Orca 並等它就緒")
    opts = parser.parse_args()

    if opts.open_orca:
        res = orca_json(["open"], timeout=180)
        ok = orca_reachable()
        print("Orca 已就緒" if ok else f"Orca 啟動失敗：{res.get('error', {}).get('message', res)}")
        sys.exit(0 if ok else 1)

    snap_path, snap = load_snapshot(opts.snapshot)
    if not orca_reachable():
        print("Orca 沒在跑或連不上。先跑 restore.py --open-orca（或手動打開 Orca）再重跑。")
        sys.exit(2)
    plan = build_plan(snap.get("sessions", []), live_session_ids(), orca_worktrees())
    print_plan(snap_path, snap, plan)
    if opts.apply:
        print()
        sys.exit(1 if apply_plan(plan) else 0)
    elif plan["orca"]:
        print("\n這是預覽。確認後加 --apply 才會開分頁。")


if __name__ == "__main__":
    main()
