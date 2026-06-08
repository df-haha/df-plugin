#!/usr/bin/env python3
"""PreToolUse(Bash) — coaching 寄送 gate（關卡 C-寄送）。

只攔含 `send_coaching_cards.py` 的指令。對「會用未驗證 email 實寄」的呼叫 deny：
  - --auto-send（reply/compose 皆直接 Send）
  - --mode compose（即使草稿也把未驗證 email 填進 To:）
只建 reply 草稿（預設、不帶上述旗標）→ 放行（人工把關，且 reply 靠 message-id 不靠 email）。
--dry-run / 非 send 指令 / 找不到 config / 任何內部錯誤 → 放行（fail-open）。
"""

from __future__ import annotations

import json
import shlex
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))
from _coaching_hooklib import (  # noqa: E402
    find_config_path,
    load_config_safe,
    parse_cards,
)

# 寄送端 main() 會跳過的卡狀態（已寄/已回/已併）——gate 同樣忽略，只看真正會送的卡。
_TERMINAL_STATUS = {"sent", "replied", "parsed", "closed", "superseded"}

_VALUE_FLAGS = {
    "--mode", "--target-date", "--subject-prefix", "--report-folder",
    "--report-subject", "--report-account", "--report-inbox",
}


# shell operator token（shlex 不解釋這些，會原樣留成 token）→ 用來把複合指令切成多個簡單命令。
_OPERATOR_TOKENS = {"&&", "||", ";", "|", "&", "\n"}
_SHELL_WRAPPERS = {"bash", "sh", "zsh", "dash"}


def _parse_single_send(seg_tokens: list[str]) -> dict | None:
    """單一簡單命令的 token 串 → send 呼叫 dict；非 send → None。"""
    idx = next((i for i, t in enumerate(seg_tokens) if t.endswith("send_coaching_cards.py")), None)
    if idx is None:
        return None
    args = seg_tokens[idx + 1:]
    md_file: str | None = None
    auto_send = False
    mode = "reply"
    dry_run = False
    i = 0
    while i < len(args):
        t = args[i]
        if t == "--auto-send":
            auto_send = True
        elif t == "--dry-run":
            dry_run = True
        elif t.startswith("--mode="):
            mode = t.split("=", 1)[1]
        elif t == "--mode":
            i += 1
            if i < len(args):
                mode = args[i]
        elif t in _VALUE_FLAGS:
            i += 1  # 吃掉值、忽略
        elif t.startswith("-"):
            pass  # 其他旗標忽略
        elif md_file is None:
            md_file = t
        i += 1
    return {"md_file": md_file, "auto_send": auto_send, "mode": mode.lower(), "dry_run": dry_run}


def parse_send_commands(command: str, _depth: int = 0) -> list[dict]:
    """回傳指令裡**所有** send_coaching_cards.py 呼叫（處理 `&&`/`;`/`|` 鏈接與 `bash -c "..."` 包裹）。

    防 bypass：鏈式「--dry-run && … --auto-send」不會讓第二段真寄漏網；bash -c 包裹也會解包。
    """
    if _depth > 3 or "send_coaching_cards.py" not in command:
        return []
    try:
        tokens = shlex.split(command)
    except ValueError:
        return []

    # 依 operator token 切成多個簡單命令
    segments: list[list[str]] = [[]]
    for t in tokens:
        if t in _OPERATOR_TOKENS:
            segments.append([])
        else:
            segments[-1].append(t)

    out: list[dict] = []
    for seg in segments:
        if not seg:
            continue
        # bash -c "<inner>"：inner 是被引號包住的整段命令 → 遞迴解析
        if Path(seg[0]).name in _SHELL_WRAPPERS and "-c" in seg:
            ci = seg.index("-c")
            if ci + 1 < len(seg):
                out.extend(parse_send_commands(seg[ci + 1], _depth + 1))
            continue
        parsed = _parse_single_send(seg)
        if parsed is not None:
            out.append(parsed)
    return out


def gate_decision(sendable_emails: list[str], auto_send: bool, mode: str, config) -> str | None:
    """回 deny reason 或 None。risky（auto_send / compose）且有未驗證 email → deny。"""
    risky = auto_send or mode.lower() == "compose"
    if not risky:
        return None  # reply + 草稿：低風險，放行
    unverified = []
    for email in sendable_emails:
        m = config.member_by_email(email)
        if m is None or not m.is_verified(email):
            unverified.append(email)
    if unverified:
        how = "--auto-send 實寄" if auto_send else "compose 開新信（To: 帶未驗證位址）"
        uniq = ", ".join(dict.fromkeys(unverified))
        return (
            f"以下 email 尚未 verified，不可用於 {how}：{uniq}。"
            f"請先在 config 標 verified: true，或改用 reply 草稿模式"
            f"（不帶 --auto-send / --mode compose，由人工把關）。"
        )
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


def main() -> None:
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}
    tool_input = data.get("tool_input", {}) or {}
    command = tool_input.get("command", "") or ""
    cwd = data.get("cwd") or "."

    # 複合指令裡可能有多個 send 呼叫（鏈接 / bash -c 包裹）——逐一檢查，任一 risky+未驗證即 deny。
    for inv in parse_send_commands(command):
        if inv["dry_run"]:
            continue  # 該段只是預覽，不寄
        if not (inv["auto_send"] or inv["mode"] == "compose"):
            continue  # reply 草稿，低風險，放行該段
        if not inv["md_file"]:
            # risky 但拿不到卡片檔（如只用 --target-date）→ 無法檢查，fail-open 放行但留痕
            print("[coaching_send_gate] ⚠️ risky 寄送指令缺卡片檔路徑，無法核對 email，放行",
                  file=sys.stderr)
            continue

        md_path = Path(inv["md_file"])
        if not md_path.is_absolute():
            md_path = Path(cwd) / md_path
        if not md_path.is_file():
            continue  # 檔不在，放行（寄送端自會報錯）

        try:
            cards = parse_cards(md_path.read_text(encoding="utf-8"))
        except Exception:  # noqa: BLE001 — 讀檔/parse 失敗 → fail-open
            continue

        cfg_path = find_config_path(md_path.parent)
        config = load_config_safe(cfg_path) if cfg_path else None
        if config is None:
            continue  # 非此 tenant / config 壞 → 放行該段

        sendable_emails = []
        for card in cards:
            y = card["yaml"]
            if str(y.get("review_status", "")).strip() in _TERMINAL_STATUS:
                continue
            email = str((y.get("employee") or {}).get("email", "")).strip()
            if email:
                sendable_emails.append(email)

        reason = gate_decision(sendable_emails, inv["auto_send"], inv["mode"], config)
        if reason:
            _deny(reason + "（hook coaching_send_gate 攔截）")
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — fail-open
        print(f"[coaching_send_gate] 內部錯誤，放行：{e}", file=sys.stderr)
        sys.exit(0)
