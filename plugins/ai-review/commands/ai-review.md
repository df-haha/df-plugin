---
description: "AI 二次審查 — /ai-review [code|plan|debate] [--codex|--gemini|--both] [--model name] [path|\"問題\"]"
---

解析 `$ARGUMENTS` 並套用 ai-review skill 規則。

## 參數解析

1. 第一個詞 → mode（code/plan/debate，預設 code）
2. `--codex` / `--gemini` / `--both` → reviewer（預設 codex）
3. `--model <name>` → 提取為模型名稱
4. 剩餘文字 → plan path 或 debate question

## 指令範例

| 指令 | 功能 |
|------|------|
| `/ai-review` | 預設 code 模式 + codex |
| `/ai-review code` | 審查未提交程式碼（codex） |
| `/ai-review code --gemini` | 用 Gemini 審程式碼 |
| `/ai-review plan` | 自動找最新 plan（codex） |
| `/ai-review plan path/to/file` | 審查指定 plan |
| `/ai-review debate` | 互動詢問問題 |
| `/ai-review debate "問題"` | 直接辯論 |
| `/ai-review code --both` | Codex + Gemini 雙審 |
| `--model o3` | 指定 reviewer 使用的模型 |

## 執行

套用 `ai-review` skill 規則，根據解析出的 mode、reviewer、model 執行。

Arguments: $ARGUMENTS
