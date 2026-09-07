---
description: "裁決帳本 — /ledger [init|add|check|migrate] [--scan 路徑...] [--dry-run]"
---

解析 `$ARGUMENTS` 並套用 ruling-ledger skill 規則。

## 參數解析

1. 第一個詞 → 子指令（init / add / check / migrate；沒給就印用法並停）
2. `--scan <路徑>...` → check 額外掃描的檔或目錄（預設只掃白名單＋CLAUDE.md）
3. `--dry-run` → init 只印不寫
4. 剩餘文字 → add 的命題草稿，或 migrate 的來源檔路徑

## 執行

套用 `ruling-ledger` skill 的對應流程。腳本路徑：
`${CLAUDE_PLUGIN_ROOT}/scripts/ledger_check.py`、`${CLAUDE_PLUGIN_ROOT}/scripts/ledger_init.py`。
沒有 `CLAUDE_PLUGIN_ROOT` 時，從本 skill 的 SKILL.md 位置回推 `<plugin-root>/scripts/`。

Arguments: $ARGUMENTS
