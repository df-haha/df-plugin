"""progress new / show / watch — progress file management and live tail.

``watch`` is the ONE exception to the one-JSON stdout contract: it streams
human-readable lines (for Claude Code's Monitor tool) and exits on terminal
events or timeout.  ``new`` and ``show`` honour the one-JSON contract.
"""

from __future__ import annotations

import json
import secrets
import sys
import time
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

from daily_agenda.messages import humanize, known_codes
from ._common import Deps, emit


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

_STAGE_LABELS: dict[str, str] = {
    "start": "開始執行",
    "line_list": "列出 LINE 聊天室",
    "line_chat": "讀取 LINE 聊天室",
    "line_check": "檢查 LINE 連線",
    "mail": "讀取郵件",
    "calendar": "讀取行事曆",
    "analyze_batch": "AI 分析",
    "analyze_batch_done": "AI 分析完成",
    "analyze_reduce": "AI 整合分析",
    "analyze_reduce_done": "AI 整合分析完成",
    "heartbeat": "仍在執行中…",
    "done": "完成",
    "failed": "失敗",
}


def _format_event(ev: dict[str, Any]) -> str:
    """One human-readable 繁中 line for a progress event."""
    stage = ev.get("stage", "")
    label = _STAGE_LABELS.get(stage, stage)
    i = ev.get("i")
    n = ev.get("n")
    code = ev.get("code")

    parts = [label]
    if i is not None and n is not None:
        parts.append(f"{i}/{n}")
    elif n is not None:
        parts.append(f"共 {n} 項")

    if code and code in known_codes():
        msg = humanize(code)
        if msg:
            parts.append(f"({msg})")

    return " ".join(parts)


def _read_last_event(path: Path) -> dict[str, Any] | None:
    """Read the last complete JSONL line from *path*."""
    try:
        data = path.read_bytes()
    except (FileNotFoundError, OSError):
        return None
    lines = data.split(b"\n")
    for raw in reversed(lines):
        raw = raw.strip()
        if not raw:
            continue
        try:
            return json.loads(raw.decode("utf-8"))
        except (json.JSONDecodeError, UnicodeDecodeError):
            continue
    return None


# ---------------------------------------------------------------------------
# Handlers
# ---------------------------------------------------------------------------

def handle(argv: list[str], deps: Deps) -> int:
    if not argv:
        return emit({"ok": False, "code": "ARG_ERROR",
                      "detail": "usage: progress new|show|watch"}, exit_code=2)

    sub = argv[0]

    if sub == "new":
        return _handle_new(deps)
    elif sub == "show":
        return _handle_show(argv[1:], deps)
    elif sub == "watch":
        # NOTE: stream command, exempt from one-JSON contract.
        return _handle_watch(argv[1:], deps)
    else:
        return emit({"ok": False, "code": "ARG_ERROR",
                      "detail": f"unknown progress subcommand: {sub}"}, exit_code=2)


def _handle_new(deps: Deps) -> int:
    """Create a new progress file and return its path."""
    ts = deps.now_factory().strftime("%Y%m%d-%H%M%S")
    name = f"{ts}-{secrets.token_hex(4)}.jsonl"
    progress_dir = deps.home / "runs" / "progress"
    progress_dir.mkdir(parents=True, exist_ok=True)
    path = progress_dir / name
    # Touch the file so it exists
    path.write_bytes(b"")
    return emit({"ok": True, "code": None, "progress_file": path.as_posix()})


def _handle_show(argv: list[str], deps: Deps) -> int:
    """Return the last event as JSON (one-JSON contract)."""
    file_path = _parse_file_arg(argv)
    if file_path is None:
        return emit({"ok": False, "code": "ARG_ERROR",
                      "detail": "usage: progress show --file <path>"}, exit_code=2)

    last = _read_last_event(Path(file_path))
    now_utc = datetime.now(timezone.utc)

    stale_s: float | None = None
    if last and last.get("at"):
        try:
            ev_time = datetime.fromisoformat(last["at"])
            stale_s = round((now_utc - ev_time).total_seconds(), 1)
        except (ValueError, TypeError):
            pass

    return emit({"ok": True, "code": None, "last": last, "stale_s": stale_s})


def _handle_watch(argv: list[str], deps: Deps) -> int:
    """Tail the progress file, printing human-readable lines to stdout.

    Exits 0 on ``done``, 1 on ``failed``, 3 on inactivity timeout or
    wall-time expiry (``--max-s``).
    This command does NOT call ``emit()`` — it prints plain text lines.
    """
    file_path = _parse_file_arg(argv)
    if file_path is None:
        # Still needs to be parseable — print usage to stderr, exit 2
        sys.stderr.write("usage: progress watch --file <path>\n")
        return 2

    timeout_s = 900.0  # 15 min inactivity default
    poll_s = 1.0
    max_s: float | None = None  # total wall-clock cap (None = unlimited)
    for i, arg in enumerate(argv):
        if arg == "--timeout-s" and i + 1 < len(argv):
            try:
                timeout_s = float(argv[i + 1])
            except ValueError:
                pass
        if arg == "--poll-s" and i + 1 < len(argv):
            try:
                poll_s = float(argv[i + 1])
            except ValueError:
                pass
        if arg == "--max-s" and i + 1 < len(argv):
            try:
                max_s = float(argv[i + 1])
            except ValueError:
                pass

    path = Path(file_path)
    offset = 0
    partial = b""
    last_progress_time = time.monotonic()
    start_time = time.monotonic()
    seen_stages: set[str] = set()

    while True:
        # Wall-time expiry (#3)
        if max_s is not None and (time.monotonic() - start_time) >= max_s:
            sys.stdout.write("執行超時，請檢查背景指令狀態\n")
            sys.stdout.flush()
            return 3

        try:
            data = path.read_bytes()
        except (FileNotFoundError, OSError):
            data = b""

        if len(data) > offset:
            last_progress_time = time.monotonic()
            chunk = partial + data[offset:]
            offset = len(data)
            lines = chunk.split(b"\n")
            # Last element may be incomplete
            partial = lines[-1]
            for raw_line in lines[:-1]:
                raw_line = raw_line.strip()
                if not raw_line:
                    continue
                try:
                    ev = json.loads(raw_line.decode("utf-8"))
                except (json.JSONDecodeError, UnicodeDecodeError):
                    continue

                stage = ev.get("stage", "")
                # Suppress repeated heartbeats
                if stage == "heartbeat" and "heartbeat" in seen_stages:
                    continue
                seen_stages.add(stage)
                if stage != "heartbeat":
                    # Reset heartbeat dedup so next heartbeat shows
                    seen_stages.discard("heartbeat")

                text = _format_event(ev)
                sys.stdout.write(text + "\n")
                sys.stdout.flush()

                if stage == "done":
                    return 0
                if stage == "failed":
                    return 1
        else:
            # No new data — check inactivity timeout
            elapsed = time.monotonic() - last_progress_time
            if elapsed >= timeout_s:
                sys.stdout.write("沒有進度，可能已中斷\n")
                sys.stdout.flush()
                return 3

        time.sleep(poll_s)


def _parse_file_arg(argv: list[str]) -> str | None:
    for i, arg in enumerate(argv):
        if arg == "--file" and i + 1 < len(argv):
            return argv[i + 1]
    return None
