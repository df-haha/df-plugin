"""hooks.json 載入 smoke test：合法 JSON + 三事件齊全 + 指向的腳本檔存在 + 可被 py_compile。"""

from __future__ import annotations

import json
import py_compile
import re
from pathlib import Path

import pytest

ROOT = Path(__file__).resolve().parent.parent
HOOKS_JSON = ROOT / "hooks" / "hooks.json"

HOOK_SCRIPTS = [
    "stop_coaching_guard.py",
    "coaching_card_lint.py",
    "coaching_send_gate.py",
    "_coaching_hooklib.py",
]


def test_hooks_json_valid_and_complete():
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    hooks = data["hooks"]
    # Stop 一組 + PreToolUse 兩組（Write|Edit|MultiEdit / Bash）
    assert "Stop" in hooks
    assert "PreToolUse" in hooks
    matchers = {g.get("matcher") for g in hooks["PreToolUse"]}
    assert "Write|Edit|MultiEdit" in matchers
    assert "Bash" in matchers


def test_hooks_json_commands_reference_existing_scripts():
    data = json.loads(HOOKS_JSON.read_text(encoding="utf-8"))
    refs = []
    for groups in data["hooks"].values():
        for g in groups:
            for h in g["hooks"]:
                m = re.search(r"hooks/([A-Za-z0-9_]+\.py)", h["command"])
                assert m, f"指令未指向 hooks/*.py：{h['command']}"
                refs.append(m.group(1))
    # 三個 hook 腳本都被宣告
    assert set(refs) == {
        "stop_coaching_guard.py",
        "coaching_card_lint.py",
        "coaching_send_gate.py",
    }
    for name in refs:
        assert (ROOT / "hooks" / name).is_file(), f"缺腳本 {name}"


@pytest.mark.parametrize("name", HOOK_SCRIPTS)
def test_hook_scripts_compile(name):
    py_compile.compile(str(ROOT / "hooks" / name), doraise=True)
