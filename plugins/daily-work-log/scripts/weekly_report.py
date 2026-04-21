#!/usr/bin/env python3
"""
Weekly Report Generator（屬下端）

掃描一週的 Claude Code session + 各 repo 的 spec.md/plan.md/tasks.md，
偵測 spec drift、抽 signal（老手 vs AI-coder junior），輸出結構化 JSON。

Usage:
    python3 weekly_report.py                      # 本週（週一~週日 GMT+8）
    python3 weekly_report.py --week 2026-04-14    # 指定週一日期
    python3 weekly_report.py --last-week          # 上週
    python3 weekly_report.py --config /path.json  # 自訂 config 路徑

輸出：JSON 到 stdout，供 skill 組裝 markdown 週報。
"""

from __future__ import annotations

import argparse
import json
import re
import subprocess
import sys
from collections import Counter, defaultdict
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ_GMT8 = timezone(timedelta(hours=8))
PROJECTS_DIR = Path.home() / ".claude" / "projects"
DEFAULT_CONFIG_PATH = Path.home() / ".claude" / "daily-work-log" / "config.json"

SKIP_DIRS = {
    "-home",
    "-mnt-c-Users-haha-huang",
    "-home-haha--claude-mem-observer-sessions",
    "-home-hahahuang--claude-mem-observer-sessions",
}


def parse_timestamp(ts: Any) -> datetime | None:
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)):
            if ts > 1e12:
                ts = ts / 1000
            return datetime.fromtimestamp(ts, tz=TZ_GMT8)
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(TZ_GMT8)
    except (ValueError, OSError):
        return None


def week_range(anchor: datetime, week_start: str = "monday") -> tuple[datetime, datetime]:
    """回傳 [週一 00:00, 下週一 00:00) 的範圍（或 sunday 起算）。"""
    anchor = anchor.astimezone(TZ_GMT8).replace(hour=0, minute=0, second=0, microsecond=0)
    offset_base = 0 if week_start == "monday" else 6  # weekday: Mon=0, Sun=6
    offset = (anchor.weekday() - offset_base) % 7
    start = anchor - timedelta(days=offset)
    return start, start + timedelta(days=7)


def load_config(path: Path) -> dict:
    if not path.exists():
        return {}
    try:
        return json.loads(path.read_text(encoding="utf-8"))
    except json.JSONDecodeError:
        return {}


# ────────────────────────────────────────────────────────────────
# Spec drift 偵測
# ────────────────────────────────────────────────────────────────

def _run_git(cwd: Path, args: list[str]) -> str:
    try:
        result = subprocess.run(
            ["git", *args],
            cwd=str(cwd),
            capture_output=True,
            text=True,
            timeout=10,
        )
        return result.stdout.strip() if result.returncode == 0 else ""
    except (subprocess.TimeoutExpired, FileNotFoundError):
        return ""


def analyze_spec(repo_path: Path, week_start: datetime, week_end: datetime) -> dict:
    """分析單一 repo 的 spec 狀態。"""
    spec_files = {
        "spec": repo_path / "spec.md",
        "plan": repo_path / "plan.md",
        "tasks": repo_path / "tasks.md",
    }

    result: dict[str, Any] = {
        "repo": repo_path.name,
        "repo_path": str(repo_path),
        "exists": repo_path.exists(),
        "spec_files": {},
        "tasks": [],
        "warnings": [],
    }

    if not repo_path.exists():
        result["warnings"].append(f"repo 路徑不存在：{repo_path}")
        return result

    for key, fp in spec_files.items():
        info: dict[str, Any] = {"path": str(fp), "exists": fp.exists()}
        if fp.exists():
            info["size"] = fp.stat().st_size
            info["last_modified"] = datetime.fromtimestamp(fp.stat().st_mtime, tz=TZ_GMT8).isoformat()

            git_log = _run_git(
                repo_path,
                ["log", "-1", "--format=%cI|%s", "--", fp.name],
            )
            if git_log and "|" in git_log:
                last_ci, last_subj = git_log.split("|", 1)
                last_commit_dt = parse_timestamp(last_ci)
                info["last_git_commit"] = last_ci
                info["last_git_subject"] = last_subj
                if last_commit_dt:
                    days_since = (datetime.now(TZ_GMT8) - last_commit_dt).days
                    info["days_since_spec_update"] = days_since
                    if days_since > 14:
                        result["warnings"].append(
                            f"{fp.name} 已 {days_since} 天未更新（僵屍化風險）"
                        )
        result["spec_files"][key] = info

    # 解析 tasks.md checkbox
    tasks_fp = spec_files["tasks"]
    if tasks_fp.exists():
        result["tasks"] = _parse_tasks_checkboxes(tasks_fp)

    # 抽 spec scope（簡單版：抓 file path 樣式 + 模組名關鍵字）
    spec_fp = spec_files["spec"]
    if spec_fp.exists():
        result["spec_scope"] = _extract_scope_hints(spec_fp)

    # 該週 commit 數
    commits = _run_git(
        repo_path,
        [
            "log",
            f"--since={week_start.strftime('%Y-%m-%d')} 00:00",
            f"--until={week_end.strftime('%Y-%m-%d')} 00:00",
            "--format=%h|%s|%cI",
        ],
    )
    result["commits_this_week"] = []
    for line in commits.splitlines():
        if "|" in line:
            parts = line.split("|", 2)
            if len(parts) == 3:
                result["commits_this_week"].append({
                    "hash": parts[0],
                    "subject": parts[1],
                    "date": parts[2],
                })

    # 未 push / 未 commit
    result["uncommitted"] = bool(_run_git(repo_path, ["status", "--porcelain"]))
    unpushed = _run_git(repo_path, ["log", "@{u}..", "--oneline"])
    result["unpushed_count"] = len([l for l in unpushed.splitlines() if l.strip()])

    return result


def _parse_tasks_checkboxes(tasks_fp: Path) -> list[dict]:
    """解析 tasks.md 的 checkbox：- [ ] / - [x] 格式。"""
    tasks = []
    try:
        content = tasks_fp.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return tasks
    for line in content.splitlines():
        m = re.match(r"^\s*[-*]\s+\[( |x|X)\]\s+(.+?)\s*$", line)
        if m:
            tasks.append({
                "done": m.group(1).lower() == "x",
                "text": m.group(2)[:200],
            })
    return tasks


def _extract_scope_hints(spec_fp: Path) -> dict:
    """從 spec.md 抽可能的 scope（檔案路徑、模組、關鍵名詞）。"""
    try:
        content = spec_fp.read_text(encoding="utf-8")
    except (OSError, UnicodeDecodeError):
        return {"raw_length": 0}

    # 抽 backtick 包住的路徑 / 模組名
    code_refs = re.findall(r"`([^`]{2,100})`", content)
    # 抽看起來像檔案路徑的字串
    file_refs = [r for r in code_refs if "/" in r or r.endswith((".py", ".ts", ".tsx", ".js", ".md"))]
    # 抽標題
    headings = re.findall(r"^#{1,3}\s+(.+?)$", content, re.MULTILINE)

    return {
        "raw_length": len(content),
        "file_refs": sorted(set(file_refs))[:30],
        "code_refs": sorted(set(code_refs))[:40],
        "headings": headings[:20],
    }


# ────────────────────────────────────────────────────────────────
# Session 分析 + signal
# ────────────────────────────────────────────────────────────────

def _repo_to_project_dir(repo_path: Path) -> str:
    """將 repo 實體路徑轉成 Claude projects 目錄名。

    Claude Code 的規則：所有 non-alphanumeric 字元（/ _ . 等）都變成 '-'，
    並以 '-' 開頭。例：/home/haha/CC_project → -home-haha-CC-project
    """
    s = str(repo_path.resolve())
    sanitized = re.sub(r"[^A-Za-z0-9]+", "-", s).strip("-")
    return "-" + sanitized


def scan_repo_sessions(
    repo_path: Path,
    week_start: datetime,
    week_end: datetime,
) -> list[dict]:
    """掃某 repo 在該週的 Claude Code session。"""
    project_dir_name = _repo_to_project_dir(repo_path)
    project_dir = PROJECTS_DIR / project_dir_name

    sessions: list[dict] = []
    if not project_dir.exists():
        return sessions

    loose_start = week_start - timedelta(days=1)
    loose_end = week_end + timedelta(hours=6)

    for jsonl_file in sorted(project_dir.glob("*.jsonl")):
        mtime = datetime.fromtimestamp(jsonl_file.stat().st_mtime, tz=TZ_GMT8)
        if mtime < loose_start or mtime > loose_end:
            continue
        session_info = _analyze_session_file(jsonl_file, week_start, week_end)
        if session_info:
            sessions.append(session_info)

    sessions.sort(key=lambda s: s.get("start_iso") or "")
    return sessions


def _analyze_session_file(
    filepath: Path,
    week_start: datetime,
    week_end: datetime,
) -> dict | None:
    """抽 session 內的事件時序，為後續 signal 偵測服務。"""
    events: list[dict] = []  # [{ts, kind, detail}]
    user_msgs: list[str] = []
    week_timestamps: list[datetime] = []
    files_touched: set[str] = set()
    bash_commands: list[str] = []

    try:
        with open(filepath, encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = obj.get("type", "")
                ts_raw = obj.get("timestamp")
                ts = parse_timestamp(ts_raw)

                if ts and week_start <= ts < week_end:
                    week_timestamps.append(ts)

                # User message
                if msg_type == "user":
                    text = _extract_text(obj.get("message", {}).get("content", ""))
                    if text and not text.startswith("<system-reminder>") and len(text) > 3:
                        user_msgs.append(text[:500])
                        events.append({
                            "ts": ts.isoformat() if ts else None,
                            "kind": "user_msg",
                            "text": text[:300],
                        })

                # Assistant tool calls
                elif msg_type == "assistant":
                    blocks = obj.get("message", {}).get("content", [])
                    if isinstance(blocks, list):
                        assistant_text = ""
                        for block in blocks:
                            if not isinstance(block, dict):
                                continue
                            btype = block.get("type", "")
                            if btype == "text":
                                assistant_text += block.get("text", "")
                            elif btype == "tool_use":
                                name = block.get("name", "")
                                params = block.get("input", {}) or {}
                                detail: dict[str, Any] = {"tool": name}
                                if name in ("Read", "Write", "Edit"):
                                    fp = params.get("file_path", "")
                                    if fp:
                                        files_touched.add(fp)
                                        detail["file"] = fp
                                        detail["action"] = name.lower()
                                elif name == "Bash":
                                    cmd = params.get("command", "")[:300]
                                    if cmd:
                                        bash_commands.append(cmd)
                                        detail["cmd"] = cmd
                                events.append({
                                    "ts": ts.isoformat() if ts else None,
                                    "kind": "tool",
                                    **detail,
                                })
                        if assistant_text:
                            events.append({
                                "ts": ts.isoformat() if ts else None,
                                "kind": "assistant_text",
                                "text": assistant_text[:600],
                            })

    except OSError:
        return None

    if not week_timestamps:
        return None

    start_ts = min(week_timestamps)
    end_ts = max(week_timestamps)

    return {
        "file": filepath.name,
        "file_id": filepath.stem,
        "start_iso": start_ts.isoformat(),
        "end_iso": end_ts.isoformat(),
        "duration_min": round((end_ts - start_ts).total_seconds() / 60, 1),
        "user_msg_count": len(user_msgs),
        "user_msgs_sample": user_msgs[:10],
        "files_touched": sorted(files_touched),
        "bash_count": len(bash_commands),
        "events": events,
    }


def _extract_text(content: Any) -> str:
    if isinstance(content, str):
        return content
    if isinstance(content, list):
        parts = []
        for item in content:
            if isinstance(item, dict) and item.get("type") == "text":
                parts.append(item.get("text", ""))
            elif isinstance(item, str):
                parts.append(item)
        return " ".join(parts)
    return ""


# ────────────────────────────────────────────────────────────────
# Signal 偵測（雙檔）
# ────────────────────────────────────────────────────────────────

AI_WARNING_PATTERNS = [
    r"不安全", r"security", r"vulnerab",
    r"不建議", r"不推薦",
    r"injection", r"xss", r"csrf",
    r"warning", r"⚠",
    r"不應該", r"should not",
]
AI_WARNING_RE = re.compile("|".join(AI_WARNING_PATTERNS), re.IGNORECASE)


def detect_signals_for_ai_junior(sessions: list[dict]) -> dict:
    """AI-coder junior 專屬 signal。"""
    signals = {
        "role": "ai-coder-junior",
        "ignored_ai_warnings": [],  # AI 警告後仍 commit
        "fast_accept_ratio": 0.0,   # AI 給 code → 立刻 commit / 寫檔
        "copy_paste_sessions": [],  # Read → Ask AI → Write 比例高
        "no_verification_commits": [],  # 寫完沒跑就 commit
        "repeated_errors": [],      # 重複類錯誤
    }

    fast_accept_count = 0
    assistant_to_write_count = 0

    for s in sessions:
        events = s.get("events") or []
        if not events:
            continue

        # 偵測 AI 警告後是否立即接受
        last_warning_idx = -1
        for i, ev in enumerate(events):
            if ev.get("kind") == "assistant_text" and AI_WARNING_RE.search(ev.get("text", "")):
                last_warning_idx = i
                # 檢查警告後 3 個事件內，user 是不是直接叫繼續
                window = events[i + 1 : i + 4]
                user_acks = [e for e in window if e.get("kind") == "user_msg"]
                if user_acks and any(
                    re.match(r"^\s*(好|ok|okay|繼續|continue|yes|是|對)\b", u.get("text", ""), re.IGNORECASE)
                    for u in user_acks
                ):
                    signals["ignored_ai_warnings"].append({
                        "session": s["file_id"],
                        "warning": ev.get("text", "")[:200],
                        "user_response": user_acks[0].get("text", "")[:100],
                    })

        # Read → assistant → Write pattern
        for i in range(len(events) - 2):
            a, b, c = events[i], events[i + 1], events[i + 2]
            if (
                a.get("tool") == "Read"
                and b.get("kind") == "assistant_text"
                and c.get("tool") in ("Write", "Edit")
            ):
                fast_accept_count += 1

        # 估計 assistant_text → Write/Edit 比例（quick accept）
        for i in range(len(events) - 1):
            if events[i].get("kind") == "assistant_text" and events[i + 1].get("tool") in ("Write", "Edit"):
                assistant_to_write_count += 1

        # 沒驗證就 commit：偵測 Write/Edit → Bash(git commit) 中間無 test/run
        write_idx = -1
        for i, ev in enumerate(events):
            if ev.get("tool") in ("Write", "Edit"):
                write_idx = i
            if ev.get("tool") == "Bash" and "git commit" in ev.get("cmd", "") and write_idx >= 0:
                between = events[write_idx + 1 : i]
                has_test = any(
                    "test" in e.get("cmd", "").lower() or "pytest" in e.get("cmd", "").lower()
                    or "npm run" in e.get("cmd", "").lower() or "python" in e.get("cmd", "").lower()
                    for e in between
                )
                if not has_test:
                    signals["no_verification_commits"].append({
                        "session": s["file_id"],
                        "commit_cmd": ev.get("cmd", "")[:150],
                    })
                write_idx = -1

    total_tool_events = sum(
        1 for s in sessions for ev in (s.get("events") or []) if ev.get("kind") == "tool"
    )
    if total_tool_events > 0:
        signals["fast_accept_ratio"] = round(assistant_to_write_count / total_tool_events, 2)

    # 重複錯誤：抽 user_msg 的高頻詞組（簡單啟發式）
    error_keywords = Counter()
    for s in sessions:
        for msg in s.get("user_msgs_sample", []):
            for kw in re.findall(r"(error|fail|bug|錯誤|失敗|不對|沒用|跑不起來|報錯)", msg, re.IGNORECASE):
                error_keywords[kw.lower()] += 1
    signals["repeated_errors"] = [
        {"keyword": k, "count": v} for k, v in error_keywords.most_common(5) if v >= 3
    ]

    return signals


def detect_signals_for_senior(sessions: list[dict]) -> dict:
    """老手 signal：繞路、重複 debug、方向偏離。"""
    signals = {
        "role": "senior",
        "repeated_debugging": [],      # 多 session 碰同檔案且多次報錯
        "detour_sessions": [],         # 嘗試 A → 失敗 → B → 失敗 → C
        "abandoned_tasks": [],         # session 有大量編輯但無 commit
    }

    file_touch_count: Counter = Counter()
    for s in sessions:
        for f in s.get("files_touched", []):
            file_touch_count[f] += 1

    # 高頻檔案 + session 出現錯誤關鍵字 = 重複 debug
    for file, count in file_touch_count.most_common(10):
        if count < 3:
            continue
        err_sessions = []
        for s in sessions:
            if file in s.get("files_touched", []):
                if any(
                    re.search(r"error|fail|錯誤|失敗|bug|報錯|不對", m, re.IGNORECASE)
                    for m in s.get("user_msgs_sample", [])
                ):
                    err_sessions.append(s["file_id"])
        if len(err_sessions) >= 2:
            signals["repeated_debugging"].append({
                "file": file,
                "session_count": count,
                "error_sessions": err_sessions,
            })

    # 繞路：一個 session 裡多個方向切換（粗估：high tool 多樣性 + 高 user msg）
    for s in sessions:
        events = s.get("events") or []
        tool_names = [e.get("tool") for e in events if e.get("kind") == "tool"]
        distinct_tools = len(set(tool_names))
        if distinct_tools >= 6 and s.get("user_msg_count", 0) >= 8 and s.get("duration_min", 0) > 60:
            signals["detour_sessions"].append({
                "session": s["file_id"],
                "distinct_tools": distinct_tools,
                "duration_min": s.get("duration_min"),
            })

    return signals


# ────────────────────────────────────────────────────────────────
# Spec drift cross-check
# ────────────────────────────────────────────────────────────────

def cross_check_drift(
    repo_spec: dict,
    sessions: list[dict],
) -> dict:
    """交叉比對 session 實際動作 vs spec 聲明 scope。"""
    drift: dict[str, Any] = {
        "repo": repo_spec.get("repo"),
        "spec_file_refs": repo_spec.get("spec_scope", {}).get("file_refs", []),
        "session_touched_files": [],
        "out_of_scope_files": [],   # 做了但 spec 沒列
        "untouched_scope_files": [], # spec 列了但沒碰
        "tasks_done_but_unchecked": [],  # 已 commit 但 tasks.md 沒勾
    }

    touched: set[str] = set()
    for s in sessions:
        for f in s.get("files_touched", []):
            touched.add(f)
    drift["session_touched_files"] = sorted(touched)

    spec_files = set(repo_spec.get("spec_scope", {}).get("file_refs", []))
    if spec_files:
        touched_names = {Path(f).name for f in touched}
        spec_names = {Path(f).name if "/" in f else f for f in spec_files}

        drift["out_of_scope_files"] = sorted(touched_names - spec_names)[:20]
        drift["untouched_scope_files"] = sorted(spec_names - touched_names)[:20]

    # tasks.md checkbox 未勾 但該週有 commit 提到：簡單用 subject 比對
    tasks = repo_spec.get("tasks", [])
    commit_subjects = " ".join(c.get("subject", "") for c in repo_spec.get("commits_this_week", []))
    for t in tasks:
        if not t["done"]:
            text = t["text"].lower()
            # 抓 task 中的 3+ 字關鍵詞是否出現在 commit subject
            kws = [w for w in re.findall(r"\w{4,}", text) if w not in ("this", "that", "task", "add")][:3]
            if kws and all(kw.lower() in commit_subjects.lower() for kw in kws):
                drift["tasks_done_but_unchecked"].append(t["text"][:120])

    return drift


# ────────────────────────────────────────────────────────────────
# Main
# ────────────────────────────────────────────────────────────────

def build_report(config: dict, anchor: datetime) -> dict:
    week_start, week_end = week_range(anchor, config.get("week_start", "monday"))

    report: dict[str, Any] = {
        "meta": {
            "user_name": config.get("user_name", ""),
            "user_role": config.get("user_role", "senior"),
            "manager_email": config.get("manager_email", config.get("outlook_email", "")),
            "week_start": week_start.strftime("%Y-%m-%d"),
            "week_end": (week_end - timedelta(seconds=1)).strftime("%Y-%m-%d"),
            "iso_week": f"{week_start.isocalendar().year}-W{week_start.isocalendar().week:02d}",
            "generated_at": datetime.now(TZ_GMT8).isoformat(),
        },
        "repos": [],
        "summary": {
            "total_sessions": 0,
            "total_user_msgs": 0,
            "total_commits": 0,
            "repos_with_drift_warning": 0,
        },
    }

    role = report["meta"]["user_role"]
    repos_cfg = config.get("repos", [])

    for repo_str in repos_cfg:
        repo_path = Path(repo_str).expanduser().resolve()
        spec_info = analyze_spec(repo_path, week_start, week_end)
        sessions = scan_repo_sessions(repo_path, week_start, week_end)
        drift = cross_check_drift(spec_info, sessions)

        if role == "ai-coder-junior":
            sigs = detect_signals_for_ai_junior(sessions)
        else:
            sigs = detect_signals_for_senior(sessions)

        repo_entry = {
            "name": repo_path.name,
            "path": str(repo_path),
            "spec": spec_info,
            "drift": drift,
            "signals": sigs,
            "session_count": len(sessions),
            "session_user_msgs": sum(s.get("user_msg_count", 0) for s in sessions),
            "sessions": [
                {
                    k: v for k, v in s.items()
                    if k != "events"  # events 太大，不回傳
                }
                for s in sessions
            ],
        }
        report["repos"].append(repo_entry)

        report["summary"]["total_sessions"] += len(sessions)
        report["summary"]["total_user_msgs"] += repo_entry["session_user_msgs"]
        report["summary"]["total_commits"] += len(spec_info.get("commits_this_week", []))
        has_drift = bool(
            drift.get("out_of_scope_files")
            or drift.get("untouched_scope_files")
            or any("僵屍" in w for w in spec_info.get("warnings", []))
        )
        if has_drift:
            report["summary"]["repos_with_drift_warning"] += 1

    return report


def main() -> int:
    parser = argparse.ArgumentParser(description="Weekly report generator (屬下端)")
    parser.add_argument("--week", type=str, help="週一日期 YYYY-MM-DD（預設本週）")
    parser.add_argument("--last-week", action="store_true", help="上週")
    parser.add_argument("--config", type=str, default=str(DEFAULT_CONFIG_PATH))
    args = parser.parse_args()

    anchor = datetime.now(TZ_GMT8)
    if args.last_week:
        anchor -= timedelta(days=7)
    elif args.week:
        anchor = datetime.strptime(args.week, "%Y-%m-%d").replace(tzinfo=TZ_GMT8)

    config = load_config(Path(args.config))
    if not config:
        print(json.dumps({"error": f"config 未找到或為空：{args.config}"}, ensure_ascii=False))
        return 1

    report = build_report(config, anchor)
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))
    return 0


if __name__ == "__main__":
    sys.exit(main())
