---
description: "AI 二次審查 — /ai-review [code|plan|debate] [--codex|--agy|--claude|--both] [--model name] [--effort level] [path|\"問題\"]"
---

解析 `$ARGUMENTS` 並套用 ai-review skill 規則。

## 參數解析

1. 第一個詞 → mode（code/plan/debate，預設 code）
2. `--codex` / `--agy` / `--claude` / `--both` → reviewer（預設 codex；`--gemini` 為舊名，自動映射到 agy；`--claude` 保留給 Codex 宿主反向外審情境）
3. `--model <name>` → 提取為模型名稱；未指定時 Codex 由 `~/.codex/config.toml` 決定，Agy 使用 `3.5-flash`，Claude Code 使用 `claude-opus-4-6[1m] --effort max`
4. `--effort <level>` → 提取為 reasoning effort（僅 codex 生效，low/medium/high/xhigh/max/ultra；未指定則吃 `~/.codex/config.toml`）
5. 剩餘文字 → plan path 或 debate question

## 指令範例

| 指令 | 功能 |
|------|------|
| `/ai-review` | 預設 code 模式 + codex |
| `/ai-review code` | 審查未提交程式碼（codex） |
| `/ai-review code --agy` | 用 Antigravity（agy）審程式碼 |
| `/ai-review plan` | 自動找最新 plan（codex） |
| `/ai-review plan path/to/file` | 審查指定 plan |
| `/ai-review debate` | 互動詢問問題 |
| `/ai-review debate "問題"` | 直接辯論 |
| `/ai-review code --both` | Codex + Antigravity 雙審 |
| `--model gpt-5.4` | 指定 reviewer 使用的模型（agy 例：`--model 3.5-flash`） |
| `--effort high` | 指定 Codex reasoning effort |

`--claude` 時權限預設是 `--permission-mode plan`。若確定要放寬，可在執行 runner 時設定 `AI_REVIEW_CLAUDE_PERMISSION_MODE=bypassPermissions`。

## 執行

套用 `ai-review` skill 規則，根據解析出的 mode、reviewer、model、effort 執行。

Arguments: $ARGUMENTS
