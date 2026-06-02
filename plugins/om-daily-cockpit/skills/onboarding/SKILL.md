---
name: cockpit-onboarding
description: 引導新部門主管把 om-daily-cockpit 裝起來——填 config、設 secrets env、接 Outlook MCP、驗證連線、跑 /hi --quick。預設只開核心模組（郵件分流 + 團隊日報 + directive loop）。觸發詞：「駕駛艙 onboarding」「設定 daily cockpit」「cockpit setup」。
allowed-tools: Bash, Read, Write, Edit, AskUserQuestion, ToolSearch, TaskCreate, TaskUpdate, TaskList
---

# om-daily-cockpit Onboarding Wizard

每位新主管跑一次。互動式，產出該主管可運作的 instance。**全程零 tenant hard-code**——所有部門特定值都問主管、寫進 config，secret 一律放本機 env。

## Phase 0：前置確認（先問清楚再動工）

用 AskUserQuestion 確認：
1. **config 放哪**：建議放主管自己的 private repo / 目錄（如 `<repo>/.om-cockpit/config.md`）。
2. **要開哪些模組**：預設只開「核心」（郵件分流 + 團隊日報 + directive coaching loop）。
   情報/標案/社群（intel/tender/fb）**預設停用**——要開需先選 storage 後端與來源/關鍵字，建議 onboarding 後再啟用。
3. **Outlook 環境**：確認是 Windows + Outlook Desktop（MVP 的 `outlook_local` adapter 走 PowerShell COM）。
   非中文版 Outlook 要記得把 `email.inbox_name` 從預設 `Inbox` 改成實際 inbox 名（中文版填「收件匣」）。

## Phase 1：填 config.md

1. 複製 `${CLAUDE_PLUGIN_ROOT}/templates/config.md` → `<repo>/.om-cockpit/config.md`。
2. 引導主管填：
   - `tenant_id`、`timezone`、`identity`（部門/公司/AI persona）
   - `team.members[]`：每位屬下 `member_id`（穩定 slug）→ `name` → `email` → `alias_allowlist`
     （**email 是 directive 嚴格比對的 key，務必正確**，避免多屬下同主旨串錯人）
   - `email`：`account`、`daily_report_folder`、`processed_category`、（非英文 Outlook）`inbox_name`
   - `paths`：`archive_dir`、`daily_proposal_dir`、（可選）`tracking_files`（任務/進度追蹤檔，供對齊度分析）
   - `directive`：`subject_prefix`（催辦信主旨前綴）、`marker`（保留預設即可）
3. **驗證直到綠**：
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/oc_core/config.py --validate <repo>/.om-cockpit/config.md
   ```

## Phase 2：設 secrets env（不進 config / repo）

config 的 `services.*_env` 只是「環境變數名稱」。引導主管把真值設進本機 env（`.bashrc` / Routine env）：
- `OM_DAILY_COCKPIT_CONFIG`：指向上面的 config.md（之後 script 免帶 `--config`）
- 只有啟用對應功能才需要：`OM_COCKPIT_DATABASE_URL`（postgres storage）、`OM_COCKPIT_GEMINI_API_KEY`、
  `OM_COCKPIT_N8N_*`、`OM_COCKPIT_TELEGRAM_*`
- **絕不把這些值寫進 config.md**（loader 會掃描並拒絕看起來像密鑰的值）。

## Phase 3：接 Outlook MCP + 相依 plugin

1. 確認 `outlook-local` MCP 可用（`ToolSearch("select:mcp__outlook-local__list_recent_emails_tool")` → 試讀近 1 日信）。
2. 確認相依 plugin 已安裝：`om-daily-work-log`（≥1.1.0）+ `daily-work-log`（≥1.7.3）。
3. 安裝 Python 相依：核心只需 `pip install PyYAML`；啟用 postgres storage / 情報模組再裝 `psycopg2-binary requests feedparser`。

## Phase 4：驗收（跑核心）

1. 確認 Outlook Desktop 已開（COM 抓附件需要）。
2. 乾跑團隊日報抓取：
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_daily_reports.py --dry-run
   ```
   確認印出的 PowerShell 指令帶的是**你 config 的成員/帳號**，不是別人的。
3. 跑 `/hi --quick`，確認核心三段（郵件分流 / 團隊日報 / directive loop）無 error 跑完。

## Phase 5：（可選）啟用產業特化模組

待核心穩定後，要開 intel/tender/fb：
1. 在 config `modules.<key>` 設 `enabled: true` + 填 `sources`/`keywords`/`org_ids`。
2. 選 `storage` 後端：`quick_only`（不落 DB，最簡）/ `sqlite`（本機檔）/ `postgres`（需 `OM_COCKPIT_DATABASE_URL`）。
3. 注意：產業特定 crawler runtime 需自備（MVP 只打包分析框架 intel-scan skill + storage adapter）。
