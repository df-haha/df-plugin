---
name: meeting-tracker-onboarding
description: 引導新部門主管把 om-meeting-tracker 裝起來——建 tenant private repo、從 5 模板填 config + context base、接 Gmail（read connector + n8n send）、設定每日 Routine。觸發詞：「會議追蹤 onboarding」「設定會議追蹤」「meeting tracker setup」。
allowed-tools: Bash, Read, Write, Edit, AskUserQuestion, ToolSearch, TaskCreate, TaskUpdate, TaskList
---

# om-meeting-tracker Onboarding Wizard

每位新主管跑一次。互動式，產出該主管 private repo 內可運作的 instance。**全程零 tenant hard-code**——所有部門特定值都問主管。

## Phase 0：前置確認（先問清楚再動工）
用 AskUserQuestion 確認：
1. tenant private repo 路徑（既有 repo 子目錄 or 新 repo）。**必須 private**。
2. send adapter：`n8n_webhook`（已有 n8n）或 `gmail_smtp`（用 Gmail app password）。
3. cadence：預設 daily，但**明示確認**（不默默繼承）——可選 business_days / overdue_only。

## Phase 1：建路徑 + 填 config.md
1. 複製 `templates/config.md` → `<repo>/.meeting-tracker/config.md`。
2. 引導主管填：tenant_id、timezone、meeting_day、paths、owners(owner_id→name→email→alias→**tier**)、metrics(metric_id→owner→title→deadline→cadence→meeting_id)。**tier 預設 1（人工回報）；該 owner 自己跑 CC 時可標 2（Tier 2 行為 v1.5 才生效，v1 驗證但不分支）。**
3. 跑 `python3 scripts/mt_core/config.py --validate <repo>/.meeting-tracker/config.md`（M1.6 加 CLI 入口）直到綠。

## Phase 2：填 context base
從 `templates/` 複製「團隊架構」「部門策略」（必填）、「個人檔案」「追蹤檔」到 `<repo>/<context_dir>`、`<tracking_file>`。引導主管填自己部門內容。

## Phase 3：接 Gmail
1. read：`ToolSearch("select:mcp__claude_ai_Gmail__authenticate,mcp__claude_ai_Gmail__complete_authentication")` → 跑 authenticate（browser）→ complete。確認可讀信。
2. send（n8n_webhook 路線）：引導在 n8n 建 `webhook/gmail-send`（Webhook→Gmail node→Respond），Gmail node 新增一次性 Gmail 連線。把 webhook URL 存 **Routine env `MT_N8N_WEBHOOK_URL`**（不進 repo）。
3. send（gmail_smtp 路線）：引導開 Gmail 2FA app password，存 env `MT_GMAIL_USER`/`MT_GMAIL_APP_PASSWORD`。
4. **記錄 Gmail token 過期週期**，排定期重新授權提醒。

## Phase 4：上 GitHub + 建 Routine
1. push tenant repo（private）。
2. 安裝 CI 治理 workflow：複製 plugin `ci/meeting-tracker-governance.yml` → `<repo>/.github/workflows/`。
3. claude.ai/code/routines：設 cron（每日，最小間隔 1 小時）、連 Gmail connector、連 repo、**Custom Allowed domains 加 send endpoint domain**、限制只能 push `claude/*` 分支。

## Phase 5：日常與驗收
- 提醒主管：每日 commit+push（由 `/done` + 鮮度檢查雙保護）。
- 驗收：跑一次 `python3 scripts/track.py --dry-run --config <repo>/.meeting-tracker/config.md`，確認算出今天該催的 owner + draft 格式正確。
