---
name: team-daily-fetcher
description: 讀取 Outlook 日報資料夾內團隊成員的昨日工作日報（成員名單來自 config）。PowerShell + Outlook COM 抓 .md 附件原檔並加「已處理」Category；MCP 讀 body；檢查未寄送與格式異常；（可選）交叉比對 tracking_files；解析 om-daily-work-log 的主管疑問回覆 anchor；產出結構化團隊引導資料。由 /hi 呼叫。department-agnostic、config 驅動、零 hard-code。
allowed-tools: Bash, Read, Glob, mcp__outlook-local__get_folder_list_tool, mcp__outlook-local__load_emails_by_folder_tool, mcp__outlook-local__view_email_cache_tool, mcp__outlook-local__get_email_by_number_tool
---

# 團隊工作日報擷取 & 分析

目的：替主管自動完成「昨日工作日下屬日報」的擷取、歸檔、格式檢查、進度對齊、引導建議與疑問提問。
**所有部門特定值（成員名單、Outlook 帳號/資料夾、追蹤檔路徑）一律來自 config，技術碼零 hard-code。**

## 資料來源

| 資訊 | 來源 |
|------|------|
| 成員名單 + Outlook 帳號/資料夾 + Category + 路徑 | oc-config（`--config` 或 `OM_DAILY_COCKPIT_CONFIG`） |
| 目標日期（最近工作日） | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/last_workday.py` |
| 附件原檔 | `python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_daily_reports.py --config <cfg>`（PowerShell + Outlook COM） |
| Body 文字 | Outlook MCP `get_email_by_number_tool(mode="basic")` |
| 任務/進度交叉比對（可選） | `config.paths.tracking_files`（清單，空則跳過對齊度分析） |

## 執行流程

### Step 1：載入 config、算 target_date

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/oc_core/config.py --validate <cfg>   # 確認 config 合法
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/last_workday.py                       # e.g. 2026-04-21
```

從 config 取：`team.members[]`（顯示名 + member_id + email/alias）、`email.account`、
`email.daily_report_folder`、`email.processed_category`、`paths.archive_dir`、
`paths.daily_proposal_dir`、`paths.tracking_files`。

### Step 2：本地快取檢查（同日重跑 /hi 時省資源）

對每位成員檢查 `{archive_dir}/{target_date}/{name}_daily_work_log_{target_date}.md` 是否存在：
- **全員都有** → 直接讀本地 md，跳過 Outlook 呼叫
- **有缺** → 進 Step 3

### Step 3：PowerShell 抓附件 + 加 Category

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/fetch_daily_reports.py --config <cfg>
```

輸出 JSON（stderr 有進度訊息）。關心欄位：`results[]`（含 `attachment_saved`、`already_processed`）、
`missing[]`（名單內但這次沒抓到的人）、`errors[]`。

若 `errors` 含 `stage: "setup"` → Outlook COM 不可用（多半是 Outlook 沒開），進 Step 4 降級走 MCP only。

### Step 4：MCP 讀 body（取得日報全文）

即便 Step 3 成功存檔附件，body 仍是最方便的分析來源；若 Step 3 失敗，MCP 是唯一 fallback。

```
mcp__outlook-local__load_emails_by_folder_tool(folder_path="{email.daily_report_folder}", days=7)
mcp__outlook-local__view_email_cache_tool(page=1..N)
```

挑出：`subject` 含 target_date（用 `email.report_subject_pattern` 抓日期）或附件名匹配
`email.attachment_pattern`；且 `from` 以 config 成員顯示名任一結尾。
對每位成員取**最新一封**（`received` 最大），呼叫：

```
mcp__outlook-local__get_email_by_number_tool(email_number=N, mode="basic", include_attachments=false)
```

> ⚠️ **不要**用 `mode="enhanced"` 或 `include_attachments=true`：MCP 不回傳附件內容、enhanced response 會膨脹至 ~100KB 浪費 token。附件抓取已由 Step 3 處理。

### Step 5：格式檢查（對每份日報）

優先讀附件原檔，沒有就讀 body。最小 spec（任一**必要**項缺 → 格式異常）：

| 層級 | 項目 | 判準 |
|------|------|------|
| 🔴 必要 | 日期 | 出現 `YYYY-MM-DD` 或 `YYYY/MM/DD`，與 target_date 一致 |
| 🔴 必要 | 工作項目分段 | 至少一個 H2/H3/H4 標題，下方有條列或段落 |
| 🔴 必要 | 狀態標記 | 出現「完成/進行中/待處理/已擱置」其一，或有帶狀態欄的表格 |
| 🟡 建議 | 明日計畫/待處理 | 有獨立區塊 |
| ⚪ 可選 | AI 使用費用 | 不一定適用，缺不算異常 |

缺項寫入 `format_issues[]`（一句話描述缺什麼）。

### Step 5.5：抽取 AI 用量資料（可選，缺則跳過）

從每份日報 md 抓兩塊（**都非必要欄位，缺就該欄顯示「—」，不標格式異常**）：

- **當日 API 費用**：找「AI 使用費用」類表格，或 grep `\$[0-9]+\.[0-9]+` 取最大值作合計。
  抽 `daily_total_usd` + `by_project[]`（project/model/cost_usd）+ `primary_model`。
- **訂閱額度**：找「Claude Code 訂閱/本週用量」區塊，抽 `rolling_5h_pct`、`rolling_7d_pct`、
  `subscription_plan`、reset 時間。

### Step 5.6：解析「主管疑問回覆」anchor（om-daily-work-log 閉環）

由 om-daily-work-log plugin 在屬下端寫入；解析屬下對主管前一輪「澄清問題卡」的回覆。
**用 HTML comment anchor，不用 heading 字串**（會被 formatter 改名）：

```
<!-- OM_QA_START card_id=<UUID> version=<N> target_date=<YYYY-MM-DD> -->
...content...
<!-- OM_QA_END -->
```

```python
import re
ANCHOR_RE = re.compile(
    r'<!--\s*OM_QA_START\s+(?P<meta>[^>]+?)-->\s*(?P<content>.*?)\s*<!--\s*OM_QA_END\s*-->',
    re.DOTALL,
)
META_RE = re.compile(r'(\w+)=(\S+)')
```

對每份日報：命中則抽 `card_id`/`version`/`target_date` + Q&A；未命中標 `anchor_present: false`。
比對前一輪卡片 `{daily_proposal_dir}/team_coaching_cards_{previous_date}.md`（若存在）的 `questions[]`，
判定 `all_replied` / `partial_replied` / `none_replied` / `mismatched_card_id`。

結果欄位（每位成員）：

```yaml
om_qa:
  anchor_present: true
  card_id: <uuid>
  expected_questions: 3
  replied: 2
  skipped: 1
  status: partial_replied
  replies:
    - {question_id: Q1, question_title: ..., answer_summary: ..., evidence: [...], is_skipped: false}
```

> ⚠️ 只在 om-daily-work-log 已部署到屬下端時才會看到 anchor。Pilot 階段部分成員
> `anchor_present=false` 屬正常，不算異常。

### Step 6：交叉比對任務分派（可選，依 config.paths.tracking_files）

**若 `config.paths.tracking_files` 為空 → 跳過本步，對齊度欄一律顯示「—」。**
若有設定，逐一 `Read` 這些檔案作為任務基準，對每條日報工作項目判斷對齊度：

| 對齊度 | 定義 |
|--------|------|
| ✅ 對齊（核心）| 命中第一個 tracking 來源的項目（通常是部門重點追蹤表）|
| ✅ 對齊（指派）| 命中其他 tracking 來源的任務線 |
| ⚪ 必要 | 行政/ERP/簽核/雜務 |
| ⚪ 等待外部 | 任務 block 在外部回饋 |
| ⚠️ 偏離 | 所有來源皆無對應、且非雜務（建議列入主管疑問）|

> 多來源衝突時，以**較新版本**的來源為準（tracking_files 清單可依新鮮度排序，後者較新可在 config 註記）。

### Step 7：產出結構化輸出（給 /hi 消費）

回傳一份 markdown，結構如下（成員名、數字一律來自實際資料，缺則「—」，**不捏造**）：

```markdown
## 🎯 團隊引導（AI 非同步教練）

> 資料來源：Outlook 日報 × tracking_files × {target_date}

### 📋 寄送狀態總覽
| 成員 | 寄送 | 附件日期 | 收件時間 | 格式 | 存檔 |
|------|------|----------|----------|------|------|
| {member} | ✅/❌ | ... | ...（補寄）| ✅/⚠️ 缺X | {path 或 —} |

### 🚨 未寄送（需催繳）
- {member}：{target_date} 未在日報資料夾收到

### ⚠️ 格式異常
- {member}：{缺什麼}

### 💻 AI 用量總覽（Step 5.5；任一欄缺顯示「—」，不捏造）
**當日 API 費用** / **訂閱額度（rolling window）** 兩張表（成員逐列 + 合計）。
異常標記：7d≥40% ⚠️、7d≥70% 🔴、5h≥50%、當日>$100。

### 🔁 主管疑問回覆（僅在偵測到 anchor 時輸出）
| 成員 | card_id | 預期 Q | 已答 | 跳過 | 狀態 |
已答 Q 不再進新卡；跳過/漏答 Q 進入新一輪 team-coaching-cards 候選題庫。

### 📝 日報解析（每人一段）
#### {member}
**任務基準**（從 tracking_files 抽，無則「該成員無對應追蹤項目」）
**昨日工作摘要**：工作項目 × 對齊度 × 說明 表
**引導建議**：🔴 今日最重要 / 🔵 次要 / ⚠️ 風險（僅在偵測到卡關/deadline/行政佔比過高時）
**❓ 待釐清疑問（主管視角）**：1-3 則，寧缺勿濫
```

## 錯誤降級表

| 失敗服務 | 偵測 | 降級 | 輸出 |
|----------|------|------|------|
| Outlook COM（沒開）| Step 3 errors stage=setup | 只走 MCP，跳過附件歸檔與 Category | 「⚠️ Outlook COM 不可用，未歸檔附件原檔；請確認 Outlook Desktop 已啟動」|
| Outlook MCP（down）| MCP 呼叫 error | 讀本地 `{archive_dir}/{date}/` 既有檔案 | 「⚠️ Outlook MCP 不可用，使用本地快取」|
| 兩者都不可用 | 上述皆 fail | 空報告 + 錯誤詳情 | 「❌ 無法取得團隊日報，請人工檢查 Outlook 與連線狀態」|
| tracking_files 讀取失敗 | Read error | 對齊度欄顯示「—」 | 「⚠️ tracking 檔讀取失敗：{path}」|

## 注意事項

1. **同人同日多封**：Step 3 PowerShell 已按 `SentOn` 降冪只取最新；Step 4 MCP 也取 `received` 最大。
2. **補寄情境**：以附件/主旨日期為準；若收件日期 > 附件日期，加註「（補寄）」。
3. **Category 去重**：PowerShell 已加 `already_processed` 旗標。
4. **不要自動寄信催繳**：未寄送只在報告裡提醒主管，由主管決定 follow up。
5. **疑問數量**：每人 1–3 則，寧缺勿濫，不為填欄位硬擠。
