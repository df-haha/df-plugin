---
description: 每日啟動駕駛艙（Daily Cockpit）。整合郵件分流 + 團隊日報追蹤 + 教練 directive loop +（可選）情報/標案/社群雷達。config 驅動、零 hard-code。用法 /hi [--quick] [--config <path>]。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Skill, ToolSearch, TaskCreate, TaskUpdate, TaskList, mcp__outlook-local__list_recent_emails_tool, mcp__outlook-local__load_emails_by_folder_tool, mcp__outlook-local__get_email_by_number_tool, mcp__outlook-local__search_email_by_subject_tool
---

# /hi — Daily Cockpit 入口

主管每日啟動指令。**參數解析 + 載入 config，然後委派 `cockpit` skill 執行 orchestrator。**

## 參數

| 參數 | 說明 |
|------|------|
| `--quick` | 輕量模式：只跑核心（郵件分流 + 團隊日報 + directive loop），跳過情報/標案/社群與深度分析 |
| `--config <path>` | oc-config.md 路徑；省略時用環境變數 `OM_DAILY_COCKPIT_CONFIG` |
| （無參數）| 完整模式：核心 + 所有 `config.modules.*.enabled=true` 的可選模組 |

## 執行

1. **解析參數**：判定 `mode = quick | full`，解析 config 路徑。
2. **驗證 config**：
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/oc_core/config.py --validate <config>
   ```
   不過則停止並回報錯誤（缺 config → 提示先跑 onboarding）。
3. **委派 orchestrator**：呼叫 `cockpit` skill，傳入 `mode` 與 config 路徑。

> MVP 預設啟用：email-triage、team-daily-fetcher、directive/coaching loop。
> intel/tender/fb 預設停用（`config.modules.*.enabled=false`）——啟用需先在 config 設定來源/關鍵字
> 與 storage 後端（見 plugin README 的 Phase 4.5 storage 說明）。
