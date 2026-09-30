#!/usr/bin/env python3
"""Read-only inventory of live Claude Code / Codex / tmux / Orca sessions.

Never mutates any session. Emits the grouped plain-text report defined in SKILL.md.
"""
from __future__ import annotations

import json
import os
import shutil
import subprocess
from collections import defaultdict
from datetime import datetime
from pathlib import Path

HOME = Path.home()
CLAUDE_SESSIONS = HOME / ".claude" / "sessions"
CLAUDE_PROJECTS = HOME / ".claude" / "projects"
CODEX_SESSIONS = HOME / ".codex" / "sessions"
CODEX_INDEX = HOME / ".codex" / "session_index.jsonl"
CODEX_SKIP_SUBCMDS = {"app-server", "mcp-server", "exec", "login", "logout"}
HINT_LEN = 110


def run(cmd: list[str], timeout: int = 20) -> str:
    try:
        return subprocess.run(cmd, capture_output=True, text=True, timeout=timeout).stdout
    except (OSError, subprocess.TimeoutExpired):
        return ""


def proc_args(pid: int) -> list[str]:
    try:
        raw = Path(f"/proc/{pid}/cmdline").read_bytes()
    except OSError:
        return []
    return [a for a in raw.decode(errors="replace").split("\0") if a]


def proc_env(pid: int) -> dict[str, str]:
    try:
        raw = Path(f"/proc/{pid}/environ").read_bytes().decode(errors="replace")
    except OSError:
        return {}
    return dict(item.split("=", 1) for item in raw.split("\0") if "=" in item)


def proc_cwd(pid: int) -> str:
    try:
        return os.readlink(f"/proc/{pid}/cwd")
    except OSError:
        return "尚未驗證"


def live_pids() -> list[int]:
    return [int(p.name) for p in Path("/proc").iterdir() if p.name.isdigit()]


def is_claude_main(args: list[str]) -> bool:
    if not args or Path(args[0]).name != "claude":
        return False
    return "--output-format" not in args and "-p" not in args and "--print" not in args


def is_codex_main(args: list[str]) -> bool:
    if not args or Path(args[0]).name != "codex" or "/vendor/" not in args[0]:
        return False
    return not (len(args) > 1 and args[1] in CODEX_SKIP_SUBCMDS)


def truncate(text: str) -> str:
    text = " ".join(text.split())
    return text if len(text) <= HINT_LEN else text[:HINT_LEN] + "…"


def claude_user_msgs(transcript: Path) -> tuple[str, str]:
    """Return (first, last) real user prompts, skipping meta, tool results and injected tags."""
    msgs: list[str] = []
    try:
        with transcript.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"type":"user"' not in line:
                    continue
                try:
                    rec = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if rec.get("isMeta") or rec.get("isSidechain"):
                    continue
                content = rec.get("message", {}).get("content")
                if isinstance(content, list):
                    content = " ".join(
                        c.get("text", "") for c in content if isinstance(c, dict) and c.get("type") == "text"
                    )
                if isinstance(content, str) and content.strip() and not content.lstrip().startswith("<") \
                        and not content.startswith("Another Claude session sent a message"):
                    msgs.append(content)
    except OSError:
        return "", ""
    return (truncate(msgs[0]), truncate(msgs[-1])) if msgs else ("", "")


def git_info(path: str) -> tuple[str, str]:
    if not Path(path).is_dir():
        return "路徑不存在", ""
    if not run(["git", "-C", path, "rev-parse", "--is-inside-work-tree"]).strip():
        return "非 Git repo", ""
    branch = run(["git", "-C", path, "branch", "--show-current"]).strip() or "(detached HEAD)"
    git_dir = run(["git", "-C", path, "rev-parse", "--absolute-git-dir"]).strip()
    common = run(["git", "-C", path, "rev-parse", "--path-format=absolute", "--git-common-dir"]).strip()
    kind = "linked worktree" if git_dir and common and git_dir != common else "main checkout"
    return branch, kind


def collect_claude(pids: list[int]) -> dict[str, dict]:
    sessions: dict[str, dict] = {}
    for pid in pids:
        args = proc_args(pid)
        if not is_claude_main(args):
            continue
        state_file = CLAUDE_SESSIONS / f"{pid}.json"
        env = proc_env(pid)
        try:
            state = json.loads(state_file.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            state = {}
        sid = state.get("sessionId")
        key = sid or f"pid:{pid}"
        entry = sessions.setdefault(key, {
            "sid": sid, "name": state.get("name"), "status": state.get("status", "尚未驗證"),
            "cwd": state.get("cwd") or proc_cwd(pid), "pids": [], "orca": set(),
        })
        entry["pids"].append(pid)
        if "--dangerously-skip-permissions" in args:
            entry["bypass"] = True
        if env.get("ORCA_TERMINAL_HANDLE"):
            entry["orca"].add(env["ORCA_TERMINAL_HANDLE"])
    for entry in sessions.values():
        transcript = next(CLAUDE_PROJECTS.glob(f"*/{entry['sid']}.jsonl"), None) if entry["sid"] else None
        entry["transcript"] = transcript
        entry["first"], entry["hint"] = claude_user_msgs(transcript) if transcript else ("", "")
    return sessions


def codex_names() -> dict[str, str]:
    names: dict[str, str] = {}
    try:
        for line in CODEX_INDEX.read_text(encoding="utf-8").splitlines():
            rec = json.loads(line)
            if rec.get("id") and rec.get("thread_name"):
                names[rec["id"]] = rec["thread_name"]
    except (OSError, json.JSONDecodeError):
        pass
    return names


def codex_rollout_meta(path: Path) -> dict:
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            rec = json.loads(fh.readline())
    except (OSError, json.JSONDecodeError):
        return {}
    return rec.get("payload", {}) if rec.get("type") == "session_meta" else {}


def codex_user_msgs(path: Path) -> tuple[str, str]:
    """Return (first, last) real user prompts, skipping injected AGENTS.md / env blocks."""
    msgs: list[str] = []
    try:
        with path.open(encoding="utf-8", errors="replace") as fh:
            for line in fh:
                if '"role":"user"' not in line:
                    continue
                try:
                    payload = json.loads(line).get("payload", {})
                except json.JSONDecodeError:
                    continue
                for c in payload.get("content") or []:
                    text = c.get("text", "") if isinstance(c, dict) else ""
                    if text.strip() and not text.lstrip().startswith(("<", "# AGENTS.md")):
                        msgs.append(text)
    except OSError:
        return "", ""
    return (truncate(msgs[0]), truncate(msgs[-1])) if msgs else ("", "")


def collect_codex(pids: list[int]) -> dict[str, dict]:
    names = codex_names()
    sessions: dict[str, dict] = {}
    for pid in pids:
        args = proc_args(pid)
        if not is_codex_main(args):
            continue
        env = proc_env(pid)
        rollouts: list[Path] = []
        try:
            for fd in Path(f"/proc/{pid}/fd").iterdir():
                target = os.readlink(fd)
                if target.startswith(str(CODEX_SESSIONS)) and target.endswith(".jsonl"):
                    rollouts.append(Path(target))
        except OSError:
            pass
        top = []
        for rollout in rollouts:
            meta = codex_rollout_meta(rollout)
            if meta.get("thread_source", "user") == "user" and not meta.get("parent_thread_id"):
                top.append((rollout, meta))
        if not top:
            sessions[f"pid:{pid}"] = {"sid": None, "cwd": proc_cwd(pid), "pids": [pid],
                                      "orca": env.get("ORCA_TERMINAL_HANDLE"), "hint": ""}
            continue
        for rollout, meta in top:
            sid = meta.get("id")
            entry = sessions.setdefault(sid, {
                "sid": sid, "name": names.get(sid), "cwd": meta.get("cwd") or proc_cwd(pid),
                "pids": [], "orca": env.get("ORCA_TERMINAL_HANDLE"), "rollout": rollout,
            })
            entry["first"], entry["hint"] = codex_user_msgs(rollout)
            entry["pids"].append(pid)
    return sessions


def collect_tmux() -> dict[str, list[str]]:
    groups: dict[str, list[str]] = defaultdict(list)
    fmt = "#{session_name}\t#{pane_id}\t#{pane_current_command}\t#{pane_current_path}"
    for line in run(["tmux", "list-panes", "-a", "-F", fmt]).splitlines():
        parts = line.split("\t")
        if len(parts) == 4:
            groups[parts[3]].append(f"{parts[0]} {parts[1]} ({parts[2]})")
    return groups


def collect_orca(agent_handles: set[str]) -> tuple[dict[str, list[dict]], str]:
    exe = shutil.which("orca-ide") or shutil.which("orca")
    if not exe:
        return {}, "Orca CLI 不在 PATH（尚未驗證）"
    out = run([exe, "terminal", "list", "--json"], timeout=30)
    try:
        terms = json.loads(out)["result"]["terminals"]
    except (json.JSONDecodeError, KeyError, TypeError):
        return {}, f"Orca CLI 失敗：{truncate(out) or '無輸出'}"
    groups: dict[str, list[dict]] = defaultdict(list)
    for term in terms:
        if term.get("handle") in agent_handles:
            continue
        groups[term.get("worktreePath") or "(無 worktree)"].append(term)
    return groups, f"Orca CLI OK（{len(terms)} 個 terminal，其中非 Claude/Codex 的列在 Orca 區）"


def work_hint(entry: dict) -> str:
    first, last = entry.get("first", ""), entry.get("hint", "")
    if not last:
        return "尚未驗證（無可讀的使用者訊息）"
    return last if first == last else f"起頭：{first}／最新：{last}"


def sort_named(items: list[dict]) -> list[dict]:
    return sorted(items, key=lambda e: (e.get("name") is None, e.get("name") or "", e.get("sid") or ""))


def main() -> None:
    pids = live_pids()
    claude = collect_claude(pids)
    codex = collect_codex(pids)
    tmux = collect_tmux()
    handles = {h for e in claude.values() for h in e["orca"]} | {e["orca"] for e in codex.values() if e.get("orca")}
    orca, orca_note = collect_orca(handles)

    by_path: dict[str, dict[str, list]] = defaultdict(lambda: defaultdict(list))
    for e in claude.values():
        by_path[e["cwd"]]["claude"].append(e)
    for e in codex.values():
        by_path[e["cwd"]]["codex"].append(e)
    for path, panes in tmux.items():
        by_path[path]["tmux"] = panes
    for path, terms in orca.items():
        by_path[path]["orca"] = terms

    # Fresh existence check right before reporting; drop sessions whose processes exited.
    alive = set(live_pids())
    for group in by_path.values():
        for rt in ("claude", "codex"):
            group[rt] = [e for e in group[rt] if any(p in alive for p in e["pids"])]
    counts = defaultdict(int)
    lines: list[str] = []
    for path in sorted(by_path):
        group = by_path[path]
        branch, kind = git_info(path)
        lines += [f"# {path}", "", f"Branch: {branch}" + (f"（{kind}）" if kind == "linked worktree" else ""), ""]
        if group["claude"]:
            lines += ["## Claude Code", ""]
            for e in sort_named(group["claude"]):
                live = [p for p in e["pids"] if p in alive]
                if not live:
                    continue
                counts["claude"] += 1
                counts[f"claude_{e['status']}"] += 1
                lines.append(f"- {e.get('name') or '(未命名)'} — {e['status']}")
                lines.append(f"  - session ID: {e['sid'] or '尚未驗證'}")
                if e["sid"] and e["transcript"] and e["transcript"].exists():
                    lines.append(f"  - resume: claude --resume {e['sid']}")
                else:
                    lines.append("  - resume: 無（找不到 transcript，不提供指令）")
                lines.append(f"  - work: {work_hint(e)}")
                notes = [f"PID {', '.join(map(str, live))}"]
                if len(live) > 1:
                    notes.append("多個 process 共用同一 session")
                if e["orca"]:
                    notes.append(f"Orca {', '.join(sorted(e['orca']))}")
                lines += [f"  - note: {'；'.join(notes)}", ""]
        if group["codex"]:
            lines += ["## Codex", ""]
            for e in sort_named(group["codex"]):
                if not any(p in alive for p in e["pids"]):
                    continue
                counts["codex"] += 1
                if not e["sid"]:
                    orca_bit = f"；Orca {e['orca']}" if e.get("orca") else ""
                    lines += ["- (空 TUI) — empty / not resumable", f"  - note: PID {e['pids'][0]}{orca_bit}", ""]
                    continue
                ok = e.get("rollout") and e["rollout"].exists()
                lines.append(f"- {e.get('name') or '(未命名)'} — {'open' if ok else '尚未驗證'}")
                lines.append(f"  - session ID: {e['sid']}")
                lines.append(f"  - resume: codex resume {e['sid']}" if ok else "  - resume: 無（找不到 rollout）")
                lines.append(f"  - work: {work_hint(e)}")
                note = f"PID {', '.join(map(str, e['pids']))}" + (f"；Orca {e['orca']}" if e.get("orca") else "")
                lines += [f"  - note: {note}", ""]
        if group["tmux"]:
            counts["tmux_panes"] += len(group["tmux"])
            lines += ["## tmux", ""] + [f"- {p}" for p in group["tmux"]] + [""]
        if group["orca"]:
            lines += ["## Orca", ""]
            for t in sorted(group["orca"], key=lambda t: (t.get("title") or "", t["handle"])):
                counts["orca_other"] += 1
                state = "connected" if t.get("connected") else "disconnected"
                lines.append(f"- {t.get('title') or '(無標題)'} — {state}")
                lines.append(f"  - handle: {t['handle']}")
                lines.append(f"  - work: {t.get('agentIdentity') or 'shell'}；{truncate(t.get('preview') or '') or 'empty / not resumable'}")
            lines.append("")

    status_bits = "、".join(f"{k.removeprefix('claude_')} {v}" for k, v in sorted(counts.items()) if k.startswith("claude_"))
    header = [
        f"Snapshot: {datetime.now().astimezone():%Y-%m-%d %H:%M:%S %Z}",
        f"Counts: Claude Code {counts['claude']}（{status_bits}）／Codex {counts['codex']}／"
        f"tmux pane {counts['tmux_panes']}／其他 Orca terminal {counts['orca_other']}",
        "Evidence: /proc 程序快照、~/.claude/sessions/<pid>.json＋transcript、Codex 開啟中的 rollout fd、git metadata、"
        + orca_note,
        "",
    ]
    risky = [f"{e.get('name') or e['sid']}（{e['status']}）" for e in claude.values() if e["status"] in ("busy", "shell")]
    footer = ["重開機警告：" + ("、".join(risky) + " 正在執行中，重開會中斷當前 turn／shell 指令，重開後要檢查結果或重跑。" if risky
                              else "目前沒有 busy／shell 的 Claude session；idle 不代表任務已完成。")]
    print("\n".join(header + lines + footer))


if __name__ == "__main__":
    main()
