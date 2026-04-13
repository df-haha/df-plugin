#!/usr/bin/env python3
"""
從 Claude Code session JSONL 檔案中提取結構化工作細節。
輸出精簡的 JSON：使用者訊息、工具使用摘要、檔案清單、指令清單。
設計目標：讓 Phase 3 不依賴 claude-mem 也能深入了解 session 內容。
"""

import json
import sys
import os


def extract_session(filepath, max_user_msgs=20, max_commands=30):
    """從單一 session JSONL 提取關鍵資訊。"""
    user_messages = []
    tool_calls = []
    files_touched = set()
    commands_run = []
    mcp_tools_used = set()

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

                msg_type = obj.get("type")

                # --- 提取使用者訊息 ---
                if msg_type == "human":
                    content = obj.get("message", {}).get("content", "")
                    if isinstance(content, list):
                        texts = []
                        for c in content:
                            if isinstance(c, dict) and c.get("type") == "text":
                                texts.append(c.get("text", ""))
                        content = "\n".join(texts)

                    if not content:
                        continue

                    # 跳過系統 / hook / observer 訊息
                    skip_prefixes = (
                        "<local-command",
                        "<command-name>",
                        "<command-message>",
                        "You are a Claude-Mem",
                        "Hello memory agent",
                        "--- MODE SWITCH",
                    )
                    if any(content.startswith(p) for p in skip_prefixes):
                        continue

                    if len(user_messages) < max_user_msgs:
                        user_messages.append(content[:500])

                # --- 提取工具呼叫 ---
                if msg_type == "assistant":
                    blocks = obj.get("message", {}).get("content", [])
                    if not isinstance(blocks, list):
                        continue

                    for block in blocks:
                        if not isinstance(block, dict):
                            continue
                        if block.get("type") != "tool_use":
                            continue

                        name = block.get("name", "")
                        params = block.get("input", {})
                        if not isinstance(params, dict):
                            params = {}

                        tool_info = {"name": name}

                        # 依工具類型提取關鍵參數
                        if name in ("Read", "Write"):
                            fp = params.get("file_path", "")
                            if fp:
                                files_touched.add(fp)
                                tool_info["file"] = fp

                        elif name == "Edit":
                            fp = params.get("file_path", "")
                            if fp:
                                files_touched.add(fp)
                                tool_info["file"] = fp
                                tool_info["action"] = "edit"

                        elif name == "Bash":
                            cmd = params.get("command", "")
                            desc = params.get("description", "")
                            if cmd and len(commands_run) < max_commands:
                                commands_run.append(
                                    {"command": cmd[:300], "description": desc[:200]}
                                )
                                tool_info["command"] = cmd[:300]

                        elif name == "Grep":
                            tool_info["pattern"] = params.get("pattern", "")[:150]
                            path = params.get("path", "")
                            if path:
                                tool_info["path"] = path

                        elif name == "Glob":
                            tool_info["pattern"] = params.get("pattern", "")[:150]

                        elif name == "Agent":
                            tool_info["description"] = params.get("description", "")[:200]
                            tool_info["subagent_type"] = params.get("subagent_type", "")

                        elif name.startswith("mcp__"):
                            mcp_tools_used.add(name)
                            tool_info["mcp"] = True

                        tool_calls.append(tool_info)

    except Exception as e:
        return {
            "file": os.path.basename(filepath),
            "error": str(e),
        }

    return {
        "file": os.path.basename(filepath),
        "user_messages": user_messages,
        "tool_usage": _summarize_tools(tool_calls),
        "mcp_tools": sorted(mcp_tools_used),
        "files_touched": sorted(files_touched),
        "commands_run": commands_run,
        "stats": {
            "user_msgs": len(user_messages),
            "tool_calls": len(tool_calls),
            "files": len(files_touched),
            "commands": len(commands_run),
            "mcp_calls": sum(1 for t in tool_calls if t.get("mcp")),
        },
    }


def _summarize_tools(tool_calls):
    """依工具名稱分組計數。"""
    counts = {}
    for tc in tool_calls:
        name = tc["name"]
        counts[name] = counts.get(name, 0) + 1
    # 按使用次數排序
    return dict(sorted(counts.items(), key=lambda x: -x[1]))


def main():
    if len(sys.argv) < 2:
        print(
            "Usage: extract_session_details.py <session.jsonl> [session2.jsonl ...]",
            file=sys.stderr,
        )
        sys.exit(1)

    results = []
    for filepath in sys.argv[1:]:
        filepath = os.path.expanduser(filepath)
        if os.path.exists(filepath):
            results.append(extract_session(filepath))
        else:
            print(f"Warning: {filepath} not found", file=sys.stderr)

    print(json.dumps(results, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
