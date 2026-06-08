#!/usr/bin/env python3
"""PreToolUse(Write|Edit|MultiEdit) — coaching card lint（關卡 B + C-寫檔）。

寫 team_coaching_cards_*.md 時：
  B-語氣：提問段落禁用詞（質疑類 / 把 AI 消耗當提問）→ deny
  B-欄位：每張卡必填欄位缺漏 → deny
  C-寫檔：employee.{member_id,name,email} 與 config member 錯配 → deny；
          一致但 email 未 verified → warn 放行（同 session 同成員只提醒一次）
  malformed yaml block → deny

非卡片檔、找不到 config（C 部分）、或任何內部錯誤 → 放行（fail-open）。
"""

from __future__ import annotations

import json
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _coaching_hooklib import (  # noqa: E402
    LintResult,
    MalformedCardError,
    find_config_path,
    is_coaching_card_file,
    lint_cards,
    load_config_safe,
)

# warn 去重狀態（同 session 同成員只提醒一次）。temp 目錄即可（跨 Stop/PreToolUse 同 OS session）。
# env 可覆寫，供測試隔離。
import os  # noqa: E402
import tempfile  # noqa: E402

_WARN_STATE = Path(
    os.environ.get("OM_COCKPIT_CARD_LINT_STATE")
    or str(Path(tempfile.gettempdir()) / "om_cockpit_card_lint_warned.json")
)


def resolve_post_content(tool_name: str, tool_input: dict) -> str | None:
    """算出「這次寫入後」的完整檔案內容（Edit/MultiEdit 套用後全檔）。算不出 → None。"""
    if tool_name == "Write":
        return tool_input.get("content", "")
    fp = tool_input.get("file_path")
    if not fp:
        return None
    path = Path(fp)
    if not path.is_file():
        return None  # Edit/MultiEdit 目標不存在 → 工具本身會錯，放行
    current = path.read_text(encoding="utf-8")
    if tool_name == "Edit":
        old = tool_input.get("old_string", "")
        new = tool_input.get("new_string", "")
        if old == "":
            return None
        if tool_input.get("replace_all"):
            return current.replace(old, new)
        return current.replace(old, new, 1)
    if tool_name == "MultiEdit":
        text = current
        for e in tool_input.get("edits", []) or []:
            old = e.get("old_string", "")
            new = e.get("new_string", "")
            if old == "":
                continue
            if e.get("replace_all"):
                text = text.replace(old, new)
            else:
                text = text.replace(old, new, 1)
        return text
    return None


def _deny(reason: str) -> None:
    print(json.dumps({
        "hookSpecificOutput": {
            "hookEventName": "PreToolUse",
            "permissionDecision": "deny",
            "permissionDecisionReason": reason,
        }
    }, ensure_ascii=False))
    sys.exit(0)


def _load_warn_state() -> set[str]:
    try:
        return set(json.loads(_WARN_STATE.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return set()


def _save_warn_state(keys: set[str]) -> None:
    try:
        _WARN_STATE.write_text(json.dumps(sorted(keys)), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def main() -> None:
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    tool_name = data.get("tool_name", "")
    tool_input = data.get("tool_input", {}) or {}
    session_id = str(data.get("session_id", ""))

    file_path = tool_input.get("file_path")
    if not is_coaching_card_file(file_path):
        sys.exit(0)  # 非卡片檔，放行

    content = resolve_post_content(tool_name, tool_input)
    if content is None:
        sys.exit(0)  # 算不出寫入後內容，放行

    # config 供 C-email 一致性用；找不到 → C 跳過（lint_cards 收 None）
    cfg_path = find_config_path(Path(file_path).parent)
    config = load_config_safe(cfg_path) if cfg_path else None

    try:
        result: LintResult = lint_cards(content, config)
    except MalformedCardError as e:
        _deny(f"教練卡格式錯誤（malformed yaml block）：{e}。請修正後再寫入。")
        return

    if result.deny_reason:
        _deny(result.deny_reason + "（hook coaching_card_lint 攔截）")
        return

    # warn：同 session 同成員只提醒一次
    if result.warnings:
        warned = _load_warn_state()
        fresh = []
        for w in result.warnings:
            key = f"{session_id}:{w['member_id']}"
            if key not in warned:
                warned.add(key)
                fresh.append(w["msg"])
        if fresh:
            _save_warn_state(warned)
            for msg in fresh:
                print(f"[coaching_card_lint] ⚠️ {msg}", file=sys.stderr)
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — fail-open：守衛壞掉不可 brick 使用者
        print(f"[coaching_card_lint] 內部錯誤，放行：{e}", file=sys.stderr)
        sys.exit(0)
