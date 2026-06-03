#!/usr/bin/env python3
"""
Daily Work Log Generator
掃描指定日期的 Claude Code session JSONL 檔案，跨專案彙整工作進度。

Usage:
    python3 scripts/daily_work_log.py [DATE]
    python3 scripts/daily_work_log.py 2026-03-09
    python3 scripts/daily_work_log.py today

輸出：JSON 格式的 session 摘要（stdout），供 skill 後續格式化使用。
"""

import json
import os
import subprocess
import sys
from datetime import datetime, timezone, timedelta
from pathlib import Path

TZ_GMT8 = timezone(timedelta(hours=8))
PROJECTS_DIR = Path.home() / ".claude" / "projects"

# 目錄名 → 可讀專案名（可選覆寫；預設空 = 一律走 dir_to_project_name() 演算法推導）。
# tenant 要自訂友善名可設 env OM_PROJECT_NAME_MAP（JSON 物件），零 hard-code。
def _load_project_name_map() -> dict:
    raw = os.environ.get("OM_PROJECT_NAME_MAP", "").strip()
    if not raw:
        return {}
    try:
        m = json.loads(raw)
        return m if isinstance(m, dict) else {}
    except (ValueError, TypeError):
        return {}


PROJECT_NAME_MAP = _load_project_name_map()


def dir_to_project_name(dirname: str) -> str:
    """將 Claude projects 目錄名轉換為可讀專案名。"""
    if dirname in PROJECT_NAME_MAP:
        return PROJECT_NAME_MAP[dirname]
    # Fallback: 去掉常見前綴
    for prefix in ["-home-haha-CC-project-", "-home-haha-", "-home-"]:
        if dirname.startswith(prefix):
            name = dirname[len(prefix):]
            if name:
                return name
    return dirname


def parse_date(date_str: str) -> datetime:
    """解析日期字串，回傳 GMT+8 的當日起始時間。"""
    if date_str.lower() == "today":
        now = datetime.now(TZ_GMT8)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    if date_str.lower() == "yesterday":
        now = datetime.now(TZ_GMT8) - timedelta(days=1)
        return now.replace(hour=0, minute=0, second=0, microsecond=0)
    return datetime.strptime(date_str, "%Y-%m-%d").replace(tzinfo=TZ_GMT8)


def parse_timestamp(ts) -> datetime | None:
    """解析各種格式的時間戳記。"""
    if ts is None:
        return None
    try:
        if isinstance(ts, (int, float)):
            if ts > 1e12:
                ts = ts / 1000
            return datetime.fromtimestamp(ts, tz=TZ_GMT8)
        # ISO format string
        return datetime.fromisoformat(str(ts).replace("Z", "+00:00")).astimezone(TZ_GMT8)
    except (ValueError, OSError):
        return None


def extract_user_text(msg: dict) -> str:
    """從 JSONL message 提取 user 的文字內容。

    Claude Code JSONL 格式：
    - type: "user" 的訊息，文字在 message.content 裡
    - message.content 可能是 string 或 list of {type, text}
    """
    message = msg.get("message", {})
    if not isinstance(message, dict):
        return ""
    content = message.get("content", "")
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


def parse_session_file(filepath: Path, day_start: datetime, day_end: datetime) -> dict | None:
    """解析單一 JSONL session 檔案。

    只計入在目標日期有 timestamp 的 session。
    """
    try:
        day_timestamps = []
        user_messages = []
        total_lines = 0
        user_count = 0

        with open(filepath, "r", encoding="utf-8") as f:
            for line in f:
                line = line.strip()
                if not line:
                    continue
                total_lines += 1
                try:
                    msg = json.loads(line)
                except json.JSONDecodeError:
                    continue

                msg_type = msg.get("type", "")

                # 提取 timestamp
                ts_raw = msg.get("timestamp")
                if ts_raw is None and msg_type == "file-history-snapshot":
                    snapshot = msg.get("snapshot", {})
                    if isinstance(snapshot, dict):
                        ts_raw = snapshot.get("timestamp")

                dt = parse_timestamp(ts_raw)
                if dt and day_start <= dt < day_end:
                    day_timestamps.append(dt)

                # 提取 user messages
                if msg_type == "user":
                    user_count += 1
                    text = extract_user_text(msg)
                    if text and len(text) > 5:
                        # 去掉 system-reminder 等雜訊
                        if not text.startswith("<system-reminder>"):
                            user_messages.append(text[:300])

        # 必須有屬於目標日期的 timestamp 才算
        if not day_timestamps:
            return None

        start_time = min(day_timestamps)
        end_time = max(day_timestamps)

        return {
            "file": filepath.name,
            "file_id": filepath.stem,
            "start_time": start_time.strftime("%H:%M"),
            "end_time": end_time.strftime("%H:%M"),
            "start_time_raw": start_time,
            "end_time_raw": end_time,
            "total_lines": total_lines,
            "user_msg_count": user_count,
            "first_user_msg": user_messages[0] if user_messages else "",
            "topic_hints": user_messages[:3],
            "size_kb": round(filepath.stat().st_size / 1024, 1),
        }
    except Exception as e:
        print(f"Error parsing {filepath.name}: {e}", file=sys.stderr)
        return None


def scan_sessions(target_date: datetime) -> dict:
    """掃描所有專案的 session JSONL 檔案，找出指定日期的 sessions。"""
    day_start = target_date
    day_end = day_start + timedelta(days=1)

    result = {
        "date": target_date.strftime("%Y-%m-%d"),
        "projects": {},
        "stats": {
            "total_sessions": 0,
            "total_projects": 0,
            "earliest": None,
            "latest": None,
        }
    }

    if not PROJECTS_DIR.exists():
        print(f"Projects directory not found: {PROJECTS_DIR}", file=sys.stderr)
        return result

    all_times = []
    skipped_dirs = []

    # 跳過非專案目錄（home root / observer sessions 等噪音）。
    # tenant 特定目錄用 env OM_SKIP_PROJECT_DIRS（逗號分隔）擴充，預設零 hard-code。
    skip_dirs = {"-home"} | {
        s.strip() for s in os.environ.get("OM_SKIP_PROJECT_DIRS", "").split(",") if s.strip()
    }

    for proj_dir in sorted(PROJECTS_DIR.iterdir()):
        if not proj_dir.is_dir():
            continue

        dirname = proj_dir.name
        if dirname in skip_dirs or dirname.endswith("-observer-sessions"):
            skipped_dirs.append(dirname)
            continue

        project_name = dir_to_project_name(dirname)
        sessions = []

        jsonl_files = list(proj_dir.glob("*.jsonl"))
        if not jsonl_files:
            continue

        # 先用 mtime 做粗篩（擴大範圍避免漏掉跨日 session）
        day_start_loose = day_start - timedelta(days=1)
        day_end_loose = day_end + timedelta(hours=6)

        for jsonl_file in sorted(jsonl_files):
            mtime = datetime.fromtimestamp(jsonl_file.stat().st_mtime, tz=TZ_GMT8)
            if mtime < day_start_loose or mtime > day_end_loose:
                continue

            session_info = parse_session_file(jsonl_file, day_start, day_end)
            if session_info and session_info["user_msg_count"] > 0:
                sessions.append(session_info)
                all_times.append(session_info["start_time_raw"])
                all_times.append(session_info["end_time_raw"])

        if sessions:
            # 按開始時間排序
            sessions.sort(key=lambda s: s["start_time_raw"])
            result["projects"][project_name] = {
                "sessions": sessions,
                "session_count": len(sessions)
            }
            result["stats"]["total_sessions"] += len(sessions)

    result["stats"]["total_projects"] = len(result["projects"])

    if all_times:
        result["stats"]["earliest"] = min(all_times).strftime("%H:%M")
        result["stats"]["latest"] = max(all_times).strftime("%H:%M")

    if skipped_dirs:
        print(f"Skipped dirs: {skipped_dirs}", file=sys.stderr)

    return result


def _detect_windows_user() -> str:
    """偵測當前 Windows 使用者名（WSL）。失敗回空字串 → 跳過 Windows codex（不掃任何 profile）。"""
    try:
        out = subprocess.run(
            ["cmd.exe", "/c", "echo %USERNAME%"],
            capture_output=True, text=True, timeout=10,
        )
        name = out.stdout.strip()
        # cmd 未展開時會回字面 "%USERNAME%"；含 % 視為失敗
        return name if name and "%" not in name else ""
    except Exception:
        return ""


def scan_codex_sessions(target_date: datetime) -> list:
    """掃描 Codex CLI (WSL + Windows Desktop) 的 session JSONL 檔案。"""
    day_start = target_date
    day_end = day_start + timedelta(days=1)
    date_parts = target_date.strftime("%Y/%m/%d").split("/")

    # WSL（Linux 端）codex sessions
    search_dirs = [
        Path.home() / ".codex" / "sessions" / date_parts[0] / date_parts[1] / date_parts[2],
    ]
    # Windows Desktop codex sessions（WSL 掛載）：env OM_WINDOWS_USER 指定優先，
    # 否則自動偵測 /mnt/c/Users/*/.codex —— 零 hard-code、零設定即可運作。
    win_user = os.environ.get("OM_WINDOWS_USER", "").strip()
    users_root = Path("/mnt/c/Users")
    # 只取**當前**使用者的 profile（env 指定 > 偵測），**絕不** glob 全部 profile——
    # 否則多使用者機台會把別人的 Codex session 算進你的日報（隱私/準確性 regression）。
    if not win_user:
        win_user = _detect_windows_user()
    candidate = users_root / win_user if win_user else None
    win_user_dirs = [candidate] if candidate and (candidate / ".codex").is_dir() else []
    for ud in win_user_dirs:
        search_dirs.append(ud / ".codex" / "sessions" / date_parts[0] / date_parts[1] / date_parts[2])

    sessions = []
    seen_ids: set[str] = set()

    for sessions_dir in search_dirs:
        if not sessions_dir.exists():
            continue
        source_label = "codex_desktop" if "/mnt/c/" in str(sessions_dir) else "cli"

        for jsonl_file in sorted(sessions_dir.glob("*.jsonl")):
            try:
                session_id = ""
                cwd = ""
                model = ""
                originator = ""
                source = source_label
                user_messages: list[str] = []
                timestamps: list[float] = []

                with open(jsonl_file, encoding="utf-8") as f:
                    for line in f:
                        line = line.strip()
                        if not line:
                            continue
                        try:
                            entry = json.loads(line)
                        except json.JSONDecodeError:
                            continue

                        entry_type = entry.get("type", "")
                        payload = entry.get("payload", {})

                        ts_raw = entry.get("timestamp")
                        if ts_raw:
                            dt = parse_timestamp(ts_raw)
                            if dt and day_start <= dt < day_end:
                                timestamps.append(dt.timestamp())

                        if entry_type == "session_meta":
                            session_id = payload.get("id", "")
                            cwd = payload.get("cwd", "")
                            originator = payload.get("originator", "")
                            if payload.get("source"):
                                source = payload["source"]

                        elif entry_type == "turn_context":
                            if payload.get("model"):
                                model = payload["model"]

                        elif entry_type == "response_item":
                            if payload.get("role") == "user":
                                content = payload.get("content", [])
                                if isinstance(content, list):
                                    for item in content:
                                        if isinstance(item, dict) and item.get("type") == "input_text":
                                            text = item.get("text", "")
                                            if text and len(text) > 5 and len(user_messages) < 3:
                                                user_messages.append(text[:300])

                        elif entry_type == "event_msg":
                            if payload.get("type") == "user_message":
                                text = payload.get("message", "")
                                if text and len(text) > 5 and len(user_messages) < 3:
                                    user_messages.append(text[:300])

                if not timestamps or not session_id:
                    continue
                if session_id in seen_ids:
                    continue
                seen_ids.add(session_id)

                project = Path(cwd).name if cwd else jsonl_file.stem
                start_ts = min(timestamps)
                end_ts = max(timestamps)

                sessions.append({
                    "session_id": session_id,
                    "provider": "codex",
                    "source": source,
                    "project": project,
                    "cwd": cwd,
                    "start": start_ts,
                    "end": end_ts,
                    "start_time": datetime.fromtimestamp(start_ts, tz=TZ_GMT8).strftime("%H:%M"),
                    "end_time": datetime.fromtimestamp(end_ts, tz=TZ_GMT8).strftime("%H:%M"),
                    "model": model,
                    "message_count": len(user_messages),
                    "topic_hints": user_messages,
                })
            except Exception as e:
                print(f"Error parsing codex session {jsonl_file.name}: {e}", file=sys.stderr)

    return sessions


def scan_gemini_sessions(target_date: datetime) -> list:
    """掃描 Gemini CLI 的 session JSON 檔案。"""
    day_start = target_date
    day_end = day_start + timedelta(days=1)
    gemini_tmp = Path.home() / ".gemini" / "tmp"

    if not gemini_tmp.exists():
        return []

    sessions = []

    for proj_dir in sorted(gemini_tmp.iterdir()):
        if not proj_dir.is_dir():
            continue
        chats_dir = proj_dir / "chats"
        if not chats_dir.exists():
            continue

        for session_file in sorted(chats_dir.glob("session-*.json")):
            try:
                data = json.loads(session_file.read_text(encoding="utf-8"))
                start_time_str = data.get("startTime", "")
                if not start_time_str:
                    continue

                start_dt = parse_timestamp(start_time_str)
                if not start_dt or not (day_start <= start_dt < day_end):
                    continue

                session_id = data.get("sessionId", session_file.stem)
                messages = data.get("messages", [])

                # Extract model and user messages
                model = ""
                user_messages: list[str] = []
                last_ts = start_dt

                for msg in messages:
                    msg_type = msg.get("type", "")
                    if msg.get("model"):
                        model = msg["model"]
                    if msg_type == "user":
                        content = msg.get("content", "")
                        if isinstance(content, list):
                            text = " ".join(
                                item.get("text", "") if isinstance(item, dict) else str(item)
                                for item in content
                            )
                        elif isinstance(content, str):
                            text = content
                        else:
                            text = str(content)
                        if text and len(text) > 5 and len(user_messages) < 3:
                            user_messages.append(text[:300])

                    msg_ts = parse_timestamp(msg.get("timestamp"))
                    if msg_ts and msg_ts > last_ts:
                        last_ts = msg_ts

                project = proj_dir.name
                sessions.append({
                    "session_id": session_id,
                    "provider": "gemini",
                    "source": "gemini_cli",
                    "project": project,
                    "cwd": str(proj_dir),
                    "start": start_dt.timestamp(),
                    "end": last_ts.timestamp(),
                    "start_time": start_dt.strftime("%H:%M"),
                    "end_time": last_ts.strftime("%H:%M"),
                    "model": model,
                    "message_count": len(user_messages),
                    "topic_hints": user_messages,
                })
            except Exception as e:
                print(f"Error parsing gemini session {session_file.name}: {e}", file=sys.stderr)

    return sessions


def fetch_ccusage(target_date: datetime) -> dict | None:
    """呼叫 ccusage CLI 取得當日 token 用量與成本。

    回傳格式：
    {
        "daily": { "totalCost": float, "totalTokens": int, "modelBreakdowns": [...] },
        "projects": { "專案名": { "cost": float, "tokens": int, "models": [...] }, ... },
        "subagents": { "total_cost": float, "count": int }
    }
    """
    date_str = target_date.strftime("%Y%m%d")

    # 取 daily 總覽（含模型分解）
    daily_data = None
    try:
        r = subprocess.run(
            ["ccusage", "daily", "--since", date_str, "--until", date_str, "--json", "--breakdown"],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0 and r.stdout.strip():
            raw = json.loads(r.stdout)
            days = raw.get("daily", [])
            if days:
                daily_data = days[0]
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"ccusage daily failed: {e}", file=sys.stderr)

    # 取 session（含 --instances 分專案）
    # 注意：ccusage session 用 lastActivity 過濾，跨日 session 的 lastActivity 可能是隔天
    # 因此 --until 需要 +1 天，確保捕捉到目標日有活動但隔天才結束的 session
    next_date_str = (target_date + timedelta(days=1)).strftime("%Y%m%d")
    projects_cost = {}
    subagent_total = 0.0
    subagent_count = 0
    try:
        r = subprocess.run(
            ["ccusage", "session", "--since", date_str, "--until", next_date_str, "--json", "--instances"],
            capture_output=True, text=True, timeout=30
        )
        if r.returncode == 0 and r.stdout.strip():
            raw = json.loads(r.stdout)
            for s in raw.get("sessions", []):
                sid = s.get("sessionId", "")
                cost = s.get("totalCost", 0)
                tokens = s.get("totalTokens", 0)
                models = s.get("modelsUsed", [])

                if "subagents" in sid:
                    subagent_total += cost
                    subagent_count += 1
                    continue

                # 轉換為可讀專案名
                proj_name = dir_to_project_name(sid)
                if proj_name in projects_cost:
                    projects_cost[proj_name]["cost"] += cost
                    projects_cost[proj_name]["tokens"] += tokens
                    for m in models:
                        if m not in projects_cost[proj_name]["models"]:
                            projects_cost[proj_name]["models"].append(m)
                else:
                    projects_cost[proj_name] = {
                        "cost": cost,
                        "tokens": tokens,
                        "models": list(models),
                    }
    except (subprocess.TimeoutExpired, FileNotFoundError, json.JSONDecodeError) as e:
        print(f"ccusage session failed: {e}", file=sys.stderr)

    if daily_data is None and not projects_cost:
        return None

    return {
        "daily": {
            "totalCost": daily_data.get("totalCost", 0) if daily_data else sum(p["cost"] for p in projects_cost.values()) + subagent_total,
            "totalTokens": daily_data.get("totalTokens", 0) if daily_data else sum(p["tokens"] for p in projects_cost.values()),
            "modelBreakdowns": daily_data.get("modelBreakdowns", []) if daily_data else [],
        },
        "projects": projects_cost,
        "subagents": {
            "total_cost": round(subagent_total, 2),
            "count": subagent_count,
        },
    }


def main():
    date_str = sys.argv[1] if len(sys.argv) > 1 else "today"
    with_cost = "--with-cost" in sys.argv

    try:
        target_date = parse_date(date_str)
    except ValueError:
        print(json.dumps({"error": f"Invalid date format: {date_str}. Use YYYY-MM-DD or 'today'"}))
        sys.exit(1)

    print(f"Scanning sessions for {target_date.strftime('%Y-%m-%d')} (GMT+8)...", file=sys.stderr)

    result = scan_sessions(target_date)

    # 新增：掃描 Codex + Gemini sessions
    codex_sessions = scan_codex_sessions(target_date)
    gemini_sessions = scan_gemini_sessions(target_date)

    if codex_sessions:
        result["codex_sessions"] = codex_sessions
        result["stats"]["total_sessions"] += len(codex_sessions)
    if gemini_sessions:
        result["gemini_sessions"] = gemini_sessions
        result["stats"]["total_sessions"] += len(gemini_sessions)

    result["stats"]["providers"] = {
        "claude": sum(p["session_count"] for p in result["projects"].values()),
        "codex": len(codex_sessions),
        "gemini": len(gemini_sessions),
    }

    # 整合 ccusage 成本數據
    if with_cost:
        print("Fetching ccusage cost data...", file=sys.stderr)
        cost_data = fetch_ccusage(target_date)
        if cost_data:
            result["cost"] = cost_data
            # 將成本合併到各專案
            for proj_name, proj_data in result["projects"].items():
                if proj_name in cost_data["projects"]:
                    proj_data["cost"] = round(cost_data["projects"][proj_name]["cost"], 2)
                    proj_data["tokens"] = cost_data["projects"][proj_name]["tokens"]
                    proj_data["models"] = cost_data["projects"][proj_name]["models"]
            print(f"Total cost: ${cost_data['daily']['totalCost']:.2f}", file=sys.stderr)
        else:
            print("ccusage data not available", file=sys.stderr)

    # 移除 raw datetime（不能 JSON 序列化）
    for proj_data in result["projects"].values():
        for session in proj_data["sessions"]:
            session.pop("start_time_raw", None)
            session.pop("end_time_raw", None)

    print(json.dumps(result, ensure_ascii=False, indent=2))

    stats = result["stats"]
    print(f"\nFound {stats['total_sessions']} sessions across {stats['total_projects']} projects", file=sys.stderr)
    if stats["earliest"] and stats["latest"]:
        print(f"Time range: {stats['earliest']} - {stats['latest']}", file=sys.stderr)


if __name__ == "__main__":
    main()
