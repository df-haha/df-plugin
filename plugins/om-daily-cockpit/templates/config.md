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
      alias_allowlist: []        # 屬下回信可能用的其他位址（嚴格 email 比對用）

email:
  adapter: outlook_local         # MVP 只支援 outlook_local（PowerShell + Outlook COM）
  account: <你的 Outlook 帳號 email>
  daily_report_folder: <收日報的資料夾名，如 每日工作報告>
  processed_category: <已處理標記類別，如 AI 已處理>

paths:
  archive_dir: <日報歸檔目錄相對路徑，如 data/daily_reports>
  daily_proposal_dir: <每日提案/卡片輸出目錄，如 daily_proposal>

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
- **team.members**：`member_id` 是穩定 slug（換顯示名不影響追蹤）；`email` + `alias_allowlist` 用於 directive 回信的**嚴格 email 比對**，避免多屬下同主旨串錯人。
- **email.adapter**：MVP 僅 `outlook_local`（現況為 PowerShell COM）。未來新增 Graph/Gmail adapter 時於此擴充，onboarding 不承諾尚未實作的能力。
- **directive**：`subject_prefix` + `marker` 構成催辦信契約，屬下端據此用 Outlook MCP 搜當日催辦信（繞過 reply-chain）。
- **services**：環境變數名稱對照表。loader 強制這些值是「名稱」而非密鑰本體。
- **modules**：情報雷達 / 標案追蹤 / 社群追蹤。`enabled: false` 時完全不執行、不寫任何 DB。`storage` 決定資料落地後端（見 Phase 4.5）。

> 你自己的真實值（RSS 來源、團隊名單等）填進你的私有 config，**不要**放進此通用 template；
> 需要參考時看 `examples/` 下的去識別化範例（該目錄被 no-hardcode 測試的掃描範圍排除）。
