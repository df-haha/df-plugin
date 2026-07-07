# Daily Cockpit Config（主管自編）

> 程式只讀下方 `oc-config` 區塊（schema validation 強制）。敘事說明放區塊外。
> ⚠️ **不要把任何密碼 / API key / token / 連線字串寫進此檔**——secret 一律放本機 env，
> 此處 `services.*_env` 只填「環境變數的名稱」（loader 會掃描並拒絕看起來像密鑰的值）。
>
> 填法：把所有 `<...>` 佔位符換成你部門的真實值；用不到的可選模組保持 `enabled: false`。
> 驗證：`python3 scripts/oc_core/config.py --validate <你的>/config.md` 直到印出 OK。

```oc-config
schema_version: 1
tenant_id: <你的部門 slug，如 acme-ops>
timezone: Asia/Taipei

identity:
  department: <部門名，如 營運部>
  company: <公司名>
  persona: <AI 助手稱呼/語氣，如 "每日駕駛艙">

team:
  members:
    - member_id: <stable slug，如 a-chen>
      name: <顯示名>
      email: <可達 email>
      verified: false            # 經目錄/實證確認 email 後改 true；coaching send gate 用此擋
                                 #「未確認 email 拿去實寄」（防串錯人）。預設 false（含 onboarding
                                 # 代填的推斷值）。注意：alias_allowlist 內位址永遠視為 unverified。
      alias_allowlist: []        # 屬下回信可能用的其他位址（嚴格 email 比對用）
      # on_leave_until: 2026-07-31  # 可選：休假迄日（ISO YYYY-MM-DD，含當日）。缺報升級檢查
      #                             # 用此把「休假中」與「失聯」分開——<= 此日的工作日不計缺報，
      #                             # 過期自動恢復計算。未請假就整行省略（向後相容）。

email:
  adapter: df_graph              # df_graph（雲端 Graph API，OS 無關，建議）或 outlook_local（Windows COM）
  account: <你的 Microsoft 365 / Outlook 帳號 email>
  daily_report_folder: <收日報的子資料夾名>
  # processed_category 只有 outlook_local 需要（以 Outlook Category 標記已處理做去重）；
  # df_graph 改用本地檔（data/daily_reports/{date}/ 存在性）去重，可省略。
  # processed_category: <已處理標記類別>
  # --- 以下可選（不填用預設）---
  inbox_name: Inbox              # Outlook inbox 顯示名；中文版 Outlook 填「收件匣」
  # attachment_pattern: <日報附件檔名 regex；預設 daily_work_log_<date>.md>
  # report_subject_pattern: <主旨日期 regex；預設語言中性 (\d{4})[/-](\d{2})[/-](\d{2})>

paths:
  archive_dir: <日報歸檔目錄相對路徑，如 data/daily_reports>
  daily_proposal_dir: <每日提案/卡片輸出目錄，如 daily_proposal>
  # 可選：任務/進度追蹤文件清單，供 team-daily-fetcher 交叉比對對齊度（空 = 跳過對齊分析）
  tracking_files: []             # 如 [docs/team_tracking.md, docs/q2_assignments.md]

directive:
  subject_prefix: <催辦信主旨前綴，如 "【每日追蹤】">
  marker: <HTML/markdown anchor marker，如 "<!-- om-directive -->">

services:
  # 只填環境變數「名稱」，不填值。secret 放本機 env / Routine env。
  database_url_env: OM_COCKPIT_DATABASE_URL
  gemini_key_env: OM_COCKPIT_GEMINI_API_KEY
  n8n_api_url_env: OM_COCKPIT_N8N_API_URL
  n8n_api_key_env: OM_COCKPIT_N8N_API_KEY
  telegram_token_env: OM_COCKPIT_TELEGRAM_TOKEN
  telegram_chat_id_env: OM_COCKPIT_TELEGRAM_CHAT_ID

modules:
  # 產業特化模組——預設全停用。換成你自己產業的來源/關鍵字後再開。
  # storage：quick_only（不落 DB，預設）| sqlite（本機檔）| postgres（需 database_url_env）
  intel:
    enabled: false
    storage: quick_only
    sources: []                  # RSS feed URL 清單
    keywords: []                 # 篩選關鍵字
  tender:
    enabled: false
    storage: quick_only
    keywords: []                 # 標案關鍵字
  fb:
    enabled: false
    storage: quick_only
    org_ids: []                  # 追蹤的粉專/組織 id
```

## 各區塊說明

- **identity**：決定 AI 報告的署名與語氣，以及對外文書的部門/公司抬頭。全部來自此處，程式碼零寫死。
- **team.members**：`member_id` 是穩定 slug（換顯示名不影響追蹤）；`email` + `alias_allowlist` 用於 directive 回信的**嚴格 email 比對**，避免多屬下同主旨串錯人。`verified`（預設 false）標記主 email 是否經實證——coaching send gate 只放行已 verified email 的實寄/compose，未驗證者只能開 reply 草稿讓人工把關（`alias_allowlist` 內位址一律視為 unverified）。`on_leave_until`（可選，ISO 日期）標休假迄日，缺報升級檢查（`missing_report_check.py`）據此豁免休假中的成員、過期自動恢復。
- **email.adapter**：`df_graph`（雲端 Microsoft Graph API，OS 無關、每人一次 device-code 登入，建議）或 `outlook_local`（Windows COM，需 Outlook Desktop）。讀信兩者皆走對應 MCP；`df_graph` 不需 `processed_category`（改本地檔去重）。
- **directive**：`subject_prefix` + `marker` 構成催辦信契約，屬下端據此用 Outlook MCP 搜當日催辦信（繞過 reply-chain）。
- **services**：環境變數名稱對照表。loader 強制這些值是「名稱」而非密鑰本體。
- **modules**：情報雷達 / 標案追蹤 / 社群追蹤。`enabled: false` 時完全不執行、不寫任何 DB。`storage` 決定資料落地後端（見 Phase 4.5）。

> 你自己的真實值（RSS 來源、團隊名單等）填進你的私有 config，**不要**放進此通用 template；
> 需要參考時看 `examples/` 下的去識別化範例（該目錄被 no-hardcode 測試的掃描範圍排除）。
