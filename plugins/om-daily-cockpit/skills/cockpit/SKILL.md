---
name: cockpit
description: Daily Cockpit orchestrator — 由 /hi 委派。整合郵件分流（Outlook MCP）+ 團隊日報追蹤（team-daily-fetcher）+ 教練 directive loop（team-coaching-cards）+（可選）情報/標案/社群雷達。全程 config 驅動、零 hard-code。mode=quick 只跑核心。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, Skill, ToolSearch, TaskCreate, TaskUpdate, TaskList, mcp__outlook-local__list_recent_emails_tool, mcp__outlook-local__load_emails_by_folder_tool, mcp__outlook-local__get_email_by_number_tool, mcp__outlook-local__search_email_by_subject_tool
---

# Daily Cockpit Orchestrator

主管每日啟動。**所有部門特定值（成員、Outlook 帳號/資料夾、追蹤檔、情報來源）一律來自 oc-config，技術碼零 hard-code。**

## 設計原則

1. **查詢優先於爬取**：先查既有資料（本地快取 / DB），有則用，無才爬。
2. **精簡輸出**：每區塊最多 5 項，重點優先。
3. **自動存檔**：報告存入 `{config.paths.daily_proposal_dir}/daily_proposal_{date}.md`。
4. **執行確認**：所有提案需主管確認後才執行。
5. **執行透明**：報告末尾記錄所有執行異常（hooks/MCP/scripts/skills），無異常顯示「✅ 本次執行無異常」。

## 前置

1. 載入 config（`--config` 或 `OM_DAILY_COCKPIT_CONFIG`），`python3 ${CLAUDE_PLUGIN_ROOT}/scripts/oc_core/config.py --validate <cfg>`。
2. 取用：`identity`（署名/語氣）、`team.members`、`email.*`、`paths.*`、`modules.*`。
3. `mode=quick` → 只跑 Phase 1.1 + 1.5 + 3.X + 4（核心）；`mode=full` → 加所有 `modules.*.enabled=true` 區塊。

---

## Phase 1：資料收集（並行）

> **異常收集**：全程記錄 hook 攔截 / MCP error / script stderr / skill 中斷，統一輸出到報告末尾「🔧 執行紀錄」。

### 1.1 郵件分流（核心，Outlook MCP）

用 `mcp__outlook-local__list_recent_emails_tool(days=1)` 取收件匣，套用 **email-triage skill** 規則做 P0-P3 分類。
（MVP 走 outlook_local MCP，不依賴外部 webhook。）

### 1.5 團隊日報（核心）

委派 **team-daily-fetcher skill**（傳 `--config`）：算最近工作日 → 本地快取檢查 → `fetch_daily_reports.py --config` 抓附件 → MCP 讀 body → 格式檢查 → AI 用量抽取 → 解析 om-daily-work-log 的 `OM_QA` anchor → 交叉比對 `config.paths.tracking_files`（空則跳過對齊分析）。產出「🎯 團隊引導」結構化區塊。

### 1.3 情報（可選；`config.modules.intel.enabled`）

**若 `intel.enabled=false` → 跳過整段（MVP 預設）。**
否則先確認 crawler 存在：`test -f ${CLAUDE_PLUGIN_ROOT}/scripts/intel_crawler.py`。
> ⚠️ **crawler runtime 不隨 plugin 打包**（產業特定）。檔案不存在 → 提示主管「intel 模組已啟用但未提供
> `scripts/intel_crawler.py`，請放入自家產業爬蟲後再跑」，並跳過本段，**不**讓它變成 file-not-found 崩潰。
若存在，依 `intel.storage`：`quick_only` 即抓即用不落 DB；`sqlite`/`postgres` 先查既有當日資料、無則 scan 落該後端再讀回。
套用 **intel-scan skill** 的 First-Principles 框架分析。來源/關鍵字一律取自 `config.modules.intel`。

### 1.4 標案（可選；`config.modules.tender.enabled`）

**若 `tender.enabled=false` → 跳過。** 否則同 1.3：先 `test -f` 確認 `scripts/gov_tender_tracker.py` 存在
（同屬 tenant 自備 runtime，缺則提示並跳過），再依 storage 邏輯跑，關鍵字取自 config。

### 1.2 任務（可選，若 tenant 接了 Todoist/其他）

有設定才查；MVP 不強制。

---

## Phase 2：策略脈絡（條件式）

讀 `config.paths.tracking_files`（若有），取進度基準供 Phase 1.5 對齊與 Phase 3 分析。空則跳過。

---

## Phase 3：分析

1. **情報分析**（若 intel 啟用）：intel-scan skill 的 First-Principles 三問（本質？關聯？行動？），每範疇 top 3。
2. **郵件分類**：email-triage skill 的 P0-P3。
3. **團隊引導**：team-daily-fetcher 已於 1.5 產出，直接嵌入 Phase 4。語氣＝早會給方向，非績效考核。

### Phase 3.X：團隊澄清卡產生（om-daily-work-log）

前置：1.5 已抓日報、Phase 3 已產對齊度。呼叫 **team-coaching-cards skill**（om-daily-work-log 提供）：
吃「待釐清疑問」結構化資料 → 每位成員產 1 張澄清卡（card_id UUID + state machine frontmatter）→
寫到 `{config.paths.daily_proposal_dir}/team_coaching_cards_{target_date}.md`。

**寄送由主管確認**（出現在 Phase 4「💡 待確認提案」）。確認寄出時，先定位相依 plugin
om-daily-work-log 的腳本（不寫死 marketplace 名/版本，取最新版）：
```bash
SCC=$(ls ~/.claude/plugins/cache/*/om-daily-work-log/*/scripts/send_coaching_cards.py 2>/dev/null | sort -V | tail -1)
python3 "$SCC" {daily_proposal_dir}/team_coaching_cards_{target_date}.md \
  --subject-prefix "{config.directive.subject_prefix}"
```
預設 `--mode reply`（接屬下原日報，找不到自動轉 compose）+ 只開草稿；加 `--auto-send` 才直接寄。
> 若 `$SCC` 為空 → 提示主管確認 om-daily-work-log plugin（≥1.1.0）已安裝。

---

## Phase 4：報告產出

存檔至 `{config.paths.daily_proposal_dir}/daily_proposal_{YYYY-MM-DD}.md`。署名/語氣用 `config.identity`。

### 報告結構

```
# {identity.persona} 每日啟動報告 - {date}

## 總覽
| 項目 | 數量 |
| 郵件 | N 封（🔴P0 🟡P1 🟢P2 ⚪P3）|
| 團隊日報 | {len(members)} 人中 N 已寄、M 未寄、K 格式異常 |
| AI 用量 | 當日合計 $X；任一人 7d≥40% 加 ⚠️ |
| 情報 | N 則（僅 intel 啟用時）|

## 🎯 團隊引導（AI 非同步教練）
（team-daily-fetcher 輸出：寄送狀態 / 未寄送 / 格式異常 / 💻 AI 用量總覽 / 🔁 主管疑問回覆 / 📝 日報解析）

## 📬 郵件
### 🔴 P0 + 🟡 P1（含回覆建議）  ### 📊 P2/P3 統計

## 📡 情報精選（僅 intel 啟用；First-Principles 5 欄表）
## 📋 標案機會（僅 tender 啟用）

## 💡 待確認提案
| # | 操作 | 內容 |
> 說「確認 1、2」或「全部確認」執行

## 🔧 執行紀錄
| 步驟 | 狀態 | 詳情 |（郵件 / 團隊日報 / 情報 / 標案 / 各 MCP）
> 無異常顯示「✅ 本次執行無異常」；附執行時間
```

---

## 錯誤降級表

| 失敗服務 | 偵測 | 降級 | 提示 |
|----------|------|------|------|
| Outlook MCP | 呼叫失敗 | 讀本地 `{archive_dir}/{date}/` 快取 | 「⚠️ Outlook MCP 不可用，使用本地快取」|
| team-daily-fetcher COM | fetch errors stage=setup | 只走 MCP body，跳過附件歸檔 | 「⚠️ Outlook COM 不可用，請確認 Outlook Desktop 已啟動」|
| intel/tender script | script error | 查既有 / 跳過該區 | 「⚠️ {模組}爬蟲不可用」|
| tracking_files 讀取 | Read error | 對齊度顯示「—」 | 「⚠️ tracking 檔讀取失敗」|

## 確認機制

報告產出後等主管確認：`確認 1` / `確認 1,3` / `全部確認` / `跳過`。
