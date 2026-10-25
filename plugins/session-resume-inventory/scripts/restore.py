#!/usr/bin/env python3
"""Reopen sessions from an inventory snapshot as Orca terminal tabs.

Default is a dry run that only prints the plan. `--apply` creates the Orca tabs; `--open-orca`
only launches Orca. Sessions whose ID is already live are never opened a second time, and sessions
the user already wrapped up (ran /session-close, or only did session inventory / snapshot / restore)
are listed but not reopened.
"""
from __future__ import annotations

import argparse
import json
import re
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from inventory import (CLAUDE_PROJECTS, CODEX_SESSIONS, SNAPSHOT_DIR, argv_resume_sid, collect_claude, collect_codex,  # noqa: E402
                       is_claude_main, is_codex_main, live_pids, normalize_path, orca_exe, proc_args,
                       run_full)

TITLE_LEN = 40
REASON_CLOSED = "下過 /session-close"
REASON_INVENTORY_ONLY = "只做 session 盤點／存快照／復原"
# A real slash-command record starts with these tags; the same tags inside pasted text are just text.
COMMAND_RECORD_TAGS = ("<command-message>", "<command-name>")
INVENTORY_SKILL = "session-resume-inventory"
INVENTORY_SCRIPTS = ("inventory.py", "restore.py")
# Injected user records that are not something the user typed. Other `<...>` records (pasted text,
# `!` shell input, slash commands outside IGNORED_COMMANDS) count as real work.
INJECTED_PREFIXES = ("Stop hook feedback", "Another Claude session sent a message",
                     "The ctx-handoff plugin sent a message", "Base directory for this skill:", "[Image")
INJECTED_TAGS = ("<task-notification", "<local-command-", "<system-reminder", "<bash-stdout", "<bash-stderr")
COMMAND_NAME = re.compile(r"<command-name>(/[^<\s]+)</command-name>")
IGNORED_COMMANDS = {"/model", "/clear", "/compact"}
# Codex: user-role records Codex itself injects. Other prompts starting with "<" (pasted XML/HTML) are work.
CODEX_INJECTED_PREFIXES = ("# AGENTS.md instructions", "<environment_context", "<user_instructions", "<user_action",
                           "<turn_aborted", "<permissions", "<no retained transcript")
CODEX_TOOL_CALLS = {"function_call", "custom_tool_call", "local_shell_call"}
# Short prompts made only of session-management words (存session / 復原 session / resume).
SESSION_MGMT_WORDS = re.compile(r"存|記錄|盤點|清單|復原|回復|恢復|還原|開回來|開回|開著|在跑|resume|restore|inventory", re.I)
# The skill's own trigger phrases (關機前記錄 / 重開機前清單 / 開著哪些 session / 有哪些 claude/codex 在跑 /
# resume 指令) carry these as context words.
SESSION_MGMT_FILLER = re.compile(r"session|claude|codex|重開機前|關機前|開機後|重開機|關機|開機|有哪些|哪些|指令|"
                                 r"一下|幫我|請|把|的|吧|[\W_]", re.I)
SESSION_MGMT_MAX_LEN = 20


def resume_command(session: dict) -> str:
    head = ["claude", "--resume", session["sid"]] if session["runtime"] == "claude" \
        else ["codex", "resume", session["sid"]]
    return shlex.join(head + list(session.get("flags") or []))


def is_session_mgmt_prompt(text: str) -> bool:
    compact = re.sub(r"\s+", "", text).lower()
    if len(compact) > SESSION_MGMT_MAX_LEN or not SESSION_MGMT_WORDS.search(compact):
        return False
    return not SESSION_MGMT_FILLER.sub("", SESSION_MGMT_WORDS.sub("", compact))


def record_text(content) -> str:
    if isinstance(content, list):
        return " ".join(c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text")
    return content if isinstance(content, str) else ""


def claude_skip_reason(transcript: Path) -> str | None:
    """Why a Claude session needs no reopening: it ran /session-close, or the user only asked it to
    save / list / restore sessions. A session that also did any other work is reopened."""
    used_inventory = False
    prompts: list[str] = []
    try:
        with transcript.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"type":"user"' not in line and '"type":"assistant"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("isSidechain"):
                    continue
                content = (rec.get("message") or {}).get("content")
                if rec.get("type") == "assistant":
                    for c in content if isinstance(content, list) else []:
                        if not isinstance(c, dict) or c.get("type") != "tool_use":
                            continue
                        args = c.get("input") or {}
                        if c.get("name") == "Skill":
                            skill = str(args.get("skill", ""))
                            if skill.split(":")[-1] == "session-close":
                                return REASON_CLOSED
                            used_inventory |= INVENTORY_SKILL in skill
                        elif c.get("name") == "Bash":
                            used_inventory |= any(s in str(args.get("command", "")) for s in INVENTORY_SCRIPTS)
                    continue
                if rec.get("type") != "user" or rec.get("isMeta"):
                    continue
                text = record_text(content)
                stripped = text.lstrip()
                if not stripped or stripped.startswith(INJECTED_PREFIXES):
                    continue
                command = COMMAND_NAME.search(text) if stripped.startswith(COMMAND_RECORD_TAGS) else None
                if command:
                    name = command.group(1)
                    if name.split(":")[-1] == "/session-close" or name.endswith(":session-close"):
                        return REASON_CLOSED
                    if name.split(":")[0] == f"/{INVENTORY_SKILL}":
                        used_inventory = True
                    elif name not in IGNORED_COMMANDS:
                        prompts.append(text)
                    continue
                if not stripped.startswith(INJECTED_TAGS):
                    prompts.append(text)
    except OSError:
        return None
    if used_inventory and all(is_session_mgmt_prompt(p) for p in prompts):
        return REASON_INVENTORY_ONLY
    return None


def codex_skip_reason(rollout: Path) -> str | None:
    """Why a Codex session needs no reopening: the user invoked $session-close (or /session-close), or
    the session only ran the inventory/restore scripts for session-management prompts. Any other user
    prompt means real work, and the session is reopened."""
    used_inventory = False
    prompts: list[str] = []
    try:
        with rollout.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                try:
                    payload = json.loads(line).get("payload") or {}
                except (json.JSONDecodeError, AttributeError):
                    continue
                if not isinstance(payload, dict):
                    continue
                kind = payload.get("type")
                if kind in CODEX_TOOL_CALLS:
                    call = json.dumps(payload, ensure_ascii=False)
                    used_inventory |= INVENTORY_SKILL in call or any(s in call for s in INVENTORY_SCRIPTS)
                    continue
                if payload.get("role") != "user" or kind not in (None, "message"):
                    continue
                for c in payload.get("content") or []:
                    text = (c.get("text", "") if isinstance(c, dict) else "").strip()
                    if text.startswith(("$session-close", "/session-close")):
                        return REASON_CLOSED
                    if not text or text.startswith(CODEX_INJECTED_PREFIXES):
                        continue
                    if text.startswith((f"${INVENTORY_SKILL}", f"/{INVENTORY_SKILL}")):
                        used_inventory = True
                        continue
                    prompts.append(text)
    except OSError:
        return None
    if used_inventory and all(is_session_mgmt_prompt(p) for p in prompts):
        return REASON_INVENTORY_ONLY
    return None


def not_needed_sessions(sessions: list[dict], projects: Path = CLAUDE_PROJECTS,
                        codex_root: Path = CODEX_SESSIONS) -> dict[str, str]:
    reasons: dict[str, str] = {}
    for s in sessions:
        sid = s.get("sid")
        if not sid or sid in reasons:
            continue
        try:
            if s.get("runtime") == "claude":
                record = next(projects.glob(f"*/{sid}.jsonl"), None)
                reason = claude_skip_reason(record) if record else None
            else:
                record = next(codex_root.rglob(f"*{sid}.jsonl"), None)
                reason = codex_skip_reason(record) if record else None
        except Exception:  # noqa: BLE001 — an odd transcript must not block the restore; reopen it
            reason = None
        if reason:
            reasons[sid] = reason
    return reasons


def build_plan(sessions: list[dict], live_sids: set[str], worktrees: dict[str, str],
               not_needed: dict[str, str] | None = None) -> dict[str, list[dict]]:
    not_needed = not_needed or {}
    plan: dict[str, list[dict]] = {k: [] for k in ("already_live", "not_needed", "orca", "unregistered",
                                                   "terminal", "skipped")}
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
        elif sid in not_needed:
            plan["not_needed"].append({**s, "reason": not_needed[sid]})
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
    if plan["not_needed"]:
        print(f"已收尾、不開回：{len(plan['not_needed'])} 個")
        for s in plan["not_needed"]:
            print(f"- [{s['runtime']}] {title_of(s)}（{s['reason']}）\n  - 要開的話：{resume_command(s)}")
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
    sessions = snap.get("sessions", [])
    plan = build_plan(sessions, live_session_ids(), orca_worktrees(), not_needed_sessions(sessions))
    print_plan(snap_path, snap, plan)
    if opts.apply:
        print()
        sys.exit(1 if apply_plan(plan) else 0)
    elif plan["orca"]:
        print("\n這是預覽。確認後加 --apply 才會開分頁。")


if __name__ == "__main__":
    main()
