---
name: om-daily-work-log
description: OM 營運部專用日誌產生器（基於 daily-work-log plugin 擴展）。除了通用日誌功能外，自動偵測主管 compose/reply 的催辦信（directive marker 契約，繞過 reply-chain 限制），引導屬下用 CC 查 git/spec/tasks 後在新一日日報的「## 主管疑問回覆」區塊（HTML anchor 標記）填答。觸發時機：屬下說「日誌」「工作日誌」「彙整工作進度」「整理今天的工作」「日報」「工作報告」（OM 屬下機器優先觸發此 skill 而非通用 daily-work-log）。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, ToolSearch, TaskCreate, TaskUpdate, TaskList, AskUserQuestion, Skill, mcp__outlook-local__list_recent_emails_tool, mcp__outlook-local__get_folder_list_tool, mcp__outlook-local__search_email_by_subject_tool, mcp__outlook-local__get_email_by_number_tool
---

# OM 營運部日誌 + 主管疑問回覆閉環

屬下端 skill，負責：
1. 內建日誌產出功能（vendored 自 daily-work-log，員工只裝這一個 plugin 即可）
2. **新增**：偵測主管 reply 的「澄清問題卡」→ 引導屬下回覆 → 寫進日報的 anchor-bound 區塊

---

## MANDATORY EXECUTION PROTOCOL

包含 7 個 Phase（0-6）。

### 開始前必做

1. `ToolSearch("select:TaskCreate,TaskUpdate,TaskList,AskUserQuestion")` 載入工具
2. `TaskCreate` 建 7 個 task：

| # | Phase |
|---|-------|
| 1 | Phase 0：偵測主管疑問郵件 |
| 2 | Phase 1：內建日誌產出（掃描 JSON → 渲染 md → 寫檔） |
| 3 | Phase 2：日報 markdown 載入 + anchor 偵測 |
| 4 | Phase 3：插入「主管疑問回覆」區塊 |
| 5 | Phase 4：引導屬下用 CC 查證並回答 |
| 6 | Phase 5：屬下 review 日報整體 |
| 7 | Phase 6：內建 send_work_log_email.py 寄出 |

3. 每 Phase 開始 / 結束都 TaskUpdate

---

## Phase 0：偵測主管催辦信（directive-first）

### Phase 0 preflight：確認 outlook-local MCP 已就緒（硬性，不可跳過）

本 skill 的偵測（Phase 0）與寄信（Phase 6）都依賴 outlook-local MCP。**開工第一件事**先確認它在：

1. `ToolSearch("select:mcp__outlook-local__list_recent_emails_tool")`
2. **抓不到 → 立即停止本 skill**，告訴屬下（把觸發詞講白）：
   > 未偵測到 **outlook-local** MCP，無法自動讀主管催辦信／寄日報。
   > 請先跑 **work-log onboarding**——對我說「**work-log setup**」或「**設定 daily work-log**」，
   > 它會引導你安裝 outlook-local MCP server 並設好你的 `member_id`，設好再回來說「日報」。
3. **抓得到 → 繼續**下方偵測流程。

### 目標
找出主管當日寄來的「澄清問題卡 / 催辦信」。**主要走 Outlook MCP 依 directive 契約搜尋**——
同時涵蓋主管 **compose 開的新信**與 **reply 屬下日報**兩種來源；reply-chain 腳本退為 fallback。

> ⚠️ 為何不只用 reply-chain：主管 compose 的新催辦信是獨立 thread、沒有指向原日報的
> ConversationID，舊的「寄件備份→ConversationID 串 reply」路徑**永遠搜不到 compose 信**。
> directive marker 契約讓兩種來源都搜得到。

### Directive 契約（主管端 send_coaching_cards.py 寄出時一律帶）
- **主旨前綴**：`【每日追蹤】`（部門可於 cockpit config 自訂；屬下端用「前綴比對」放寬）
- **HTML anchor marker**（body 內，compose 與 reply 都有）：
  ```
  <!-- OM_DIRECTIVE directive_id=<id> target_date=<YYYY-MM-DD> employee_id=<id> source=compose|reply -->
  ```

### 執行（主要：Claude + Outlook MCP）

> **以 OM_DIRECTIVE marker 為唯一可靠訊號**，不要硬依賴主旨前綴（主管可在 cockpit config
> 自訂 `directive.subject_prefix`，屬下端不一定知道實際值）。主旨前綴只當「縮小掃描範圍」的軟過濾。

1. `ToolSearch("select:mcp__outlook-local__search_email_by_subject_tool,mcp__outlook-local__list_recent_emails_tool,mcp__outlook-local__get_email_by_number_tool")`
2. **掃近期信**：`list_recent_emails_tool(days=7)`（放寬到一週，涵蓋連假/請假/補寄；天數寧多勿少，靠 marker 精準命中）。
   逐封 `get_email_by_number_tool(email_number=N, mode="basic")` 取 body，抽 marker：
   ```python
   import re
   M = re.compile(r"<!--\s*OM_DIRECTIVE\s+(?P<meta>[^>]+?)-->")
   META = re.compile(r"(\w+)=(\S+)")   # directive_id / target_date / employee_id / source
   ```
   （若已知主旨前綴，可先 `search_email_by_subject_tool` 縮範圍，但**最終以 marker 命中為準**。）
3. **日期語義（關鍵）**：directive 的 `target_date` 指「**被追問的那份日報的日期**」——也就是屬下
   **上一個工作日**的日報，不是今天。先算 `prev = 上一個工作日(今天的報告日)`（遇連假往前找）。
4. 取 marker `target_date == prev`、`employee_id == 自己` 者的**最新一封**。
   （找不到 `prev` 命中時，可放寬到「最近 2 工作日內、employee_id 為自己」的最新 directive，避免連假錯位。）
5. 從 body 的 `## Q1 / ## Q2 …` 標題 + 內容抽出問題清單（`directive_id` 即原 card_id）。

### Fallback（MCP 搜不到或不穩時）
跑 reply-chain 腳本（只認 reply 來源，抓不到 compose 催辦信）：
```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/handle_supervisor_questions.py \
  {target_date} --output-json
```
輸出 JSON：`has_supervisor_email` / `previous_date` / `card_id` / `review_thread_id` /
`review_message_id` / `questions[]`（`{id, title, body, evidence_hint}`）。

### 結果分支
- 偵測到催辦信 → 顯示：「主管寄了 N 個問題（來源：{source}）：Q1…、Q2…，待會引導你回覆」→ 進 Phase 1
- 無 → 「未偵測到主管催辦信，本日日報跳過 Phase 3-4」→ 進 Phase 1

---

## Phase 1：產出日誌（本 plugin 內建：掃描 → 渲染 → 寫檔）

日誌功能已**內建**於本 plugin（vendored 自 daily-work-log，員工只需裝這一個 plugin）。
分三步：跑腳本拿 JSON → 用 JSON 渲染成 md → 寫檔。

> ⚠️ **腳本只輸出 JSON 到 stdout，不會自己寫 md**。必須由你（模型）依下方模板把 JSON 渲染成
> `daily_proposal/daily_work_log_{target_date}.md`，否則 Phase 2（anchor 偵測）/ Phase 6（寄信）
> 會因為檔案不存在而失敗。

### 1-1. 跑掃描腳本，**擷取 stdout 的 JSON**

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/daily_work_log.py {target_date} --with-cost
```
> 掃描員工自己的 Claude Code / Codex / Gemini session，彙整當日工作 + AI 用量，**印出 JSON**。
> JSON 結構：`date` / `projects.{名稱}.sessions[]`（含 `topic_hints` / `first_user_msg` / 起訖時間）/
> `stats`（`earliest` / `latest` / `total_projects` / `providers`）/（`--with-cost` 時）`cost`。
> 無須額外安裝 daily-work-log plugin。

### 1-2. 把 JSON 渲染成日報 md（**必做**，路徑 `daily_proposal/daily_work_log_{target_date}.md`）

依下方模板，**只用 1-1 的 JSON** 渲染（本 plugin 未 vendored git-status / usage-tracker /
session-detail 腳本，故不引用它們；缺的欄位就省略或標 N/A，不要腦補）。**`## 待處理事項` heading
必留**——Phase 3 的 anchor 區塊靠它定位插入。

```markdown
# {target_date} 工作日誌

> 日期：{target_date}（{星期}）
> 工作時段：{stats.earliest} → {stats.latest}
> 涉及專案：{stats.total_projects} 個
> 今日 AI 使用費用：${cost.daily.totalCost｜無資料時標 N/A（ccusage 未安裝）}

---

## 一、{專案名（白話）}

### {工作主題（白話描述）}
- {從該專案 sessions 的 topic_hints / first_user_msg 歸納「實際做了什麼」}
- {著重成果：完成/產出/解決了什麼；未完成的講進度與下一步}

## 二、{下一個專案（白話）}
- ...

---

## 今日產出

| # | 項目 | 狀態 |
|---|------|------|
| 1 | {白話描述產出物} | 完成 |
| 2 | {白話描述} | 進行中（{卡在哪}） |

---

## 待處理事項

- [ ] {需後續處理的事；沒有就寫「無」}

---

## AI 使用費用明細

| 專案 | 使用的 AI 模型 | 費用 | 備註（原始 repo 名）|
|------|---------------|------|----------------------|
| {專案名（白話）} | {Claude Opus / Sonnet …} | ${cost} | {projects key} |
| **合計** | | **${cost.daily.totalCost}** | |

> 無 `cost` 區塊時，整段標「AI 使用費用：N/A（ccusage 未安裝）」，不要捏造數字。

<!-- Part 2: 技術執行細節（供主管的 AI 助理解析，非人工閱讀區）-->
# 技術執行細節

> 統計：Claude {providers.claude} / Codex {providers.codex} / Gemini {providers.gemini} sessions｜時段 {earliest} → {latest}

## 各專案 session 摘要
### {專案名}（{session_count} sessions）
- {逐 session：起訖時間 + topic_hints 摘要，據實記錄，不誇大}
```

### 1-3. 寫作紀律（保守原則，非常重要）

- **做多少寫多少**：只記 session 裡**實際觀察到**的動作，不用「優化/完善/重構/深入研究」這種誇大詞。
- **Part 1 白話**：主管是非技術職——不要出現 commit / push / git / MCP / API / token / cache / session /
  plugin / hook / repo 這些詞；改用 程式 / 系統 / 網站 / 檔案 / 更新 / 修正 / 測試 / 上線 / 同步。
- **狀態三分**：完成（有產物且驗證過）/ 進行中（動了未交付）/ 中斷（被打斷或報錯未修）。
- **不捏造數字**：費用一律來自 `cost` 區塊；沒有就標 N/A。

---

## Phase 2：日報 markdown 載入 + anchor 偵測

### 路徑
日報應已產生在：`daily_proposal/daily_work_log_{target_date}.md`

### Anchor 偵測規則
讀檔，搜 anchor：
```
<!-- OM_QA_START card_id=<UUID> version=<N> target_date=<DATE> -->
...
<!-- OM_QA_END -->
```

### 結果分支
- 已存在 anchor（同 card_id）→ 屬下今日重跑 skill，要更新 anchor 內容（不重複插入）
- 不存在 anchor → 進 Phase 3 插入新 anchor 區塊

---

## Phase 3：插入「主管疑問回覆」區塊（v2-2 anchor-based contract）

### 規則
**不**用 heading 字串 patch，**用 HTML comment anchor**：

```python
# 偽碼：插入位置 = `## 待處理事項` heading 之前
# 若該 heading 不存在 → fallback 寫到檔案末尾並標 anomaly
```

### 區塊格式

```markdown
<!-- OM_QA_START card_id={card_id} version={card_version} target_date={previous_date} -->
## 主管疑問回覆

> 來源：主管 {previous_date} reply 我寄出的「每日工作報告 {previous_date}」郵件
> card_id: {card_id}
> review_thread_id: {ConversationID}

### Q1. {主管問題標題}
{屬下用 CC 查證後的回覆}

**佐證**：
- {commit hash + path}
- {spec/plan/tasks 段落引用}

### Q2. ...

<!-- OM_QA_END -->
```

### 重跑時的覆寫規則
- 用 regex `<!-- OM_QA_START.*?-->.*?<!-- OM_QA_END -->`（non-greedy + DOTALL）替換 anchor 內整段
- **不**動 anchor 之外的 markdown
- 若上游 daily-work-log 升級導致 `## 待處理事項` heading 改名 → fallback 到末尾並在區塊上方加：

```markdown
> ⚠️ ANOMALY: 預期 heading「## 待處理事項」未找到，本區塊已 fallback 寫到檔案末尾。
```

---

## Phase 4：引導屬下用 CC 查證並回答

### 一題一題引導
對每題 Q{N}，按下面的流程做：

1. 顯示題目給屬下：「主管問 Q{N}: {title}\n{body}」
2. **執行查證指令**（從 evidence_hint 推 git/spec/tasks 路徑）：
   ```bash
   # 範例：evidence_hint = "<專案 repo>: spec.md / plan.md / tasks.md"
   cd /path/to/<專案 repo>
   git log --since="{previous_date} 00:00" --until="{target_date} 23:59" --oneline
   Read spec.md / plan.md / tasks.md 相關段落
   ```
3. 把查到的事實整理成 100-200 字回覆，附 **commit hash + 檔案路徑** 佐證
4. 引導屬下確認回覆是否符合事實 → 用 `AskUserQuestion`：
   - 確認 OK → 寫進區塊
   - 修改 → 重寫
   - 我不知道 → 標「Q{N}：（待下次回覆，因 ...）」**不要靜默跳過**

### Q 漏答的處理
若屬下 Q{N} 跳過/不答：
```markdown
### Q{N}. {主管問題標題}
（待下次回覆，因 {原因，例如「需確認與廠商會議結論」「卡在等同事的回覆」}）
```
**不**留空、**不**靜默跳過 — 主管下次 /hi 才知道為何漏答。

### evidence_hint 三層穩定度（v2-5 同步）
回覆中的佐證盡量用：
- ★★★ `git:<sha>:<path>#L<n>-L<m>`
- ★★ commit hash only
- ★ `<path>#L<n>`（不綁 commit，會漂移；只用為輔助）

---

## Phase 5：屬下 review 日報整體

把完整日報 md 顯示給屬下，問：
```
日報已寫入 daily_proposal/daily_work_log_{target_date}.md
- 工作項目區塊
- 主管疑問回覆區塊（N 題、漏答 M 題）
- AI 用量區塊
- 待處理事項

要修改哪個區塊？還是 OK 寄出？
```

用 `AskUserQuestion`：
1. OK 寄出 → Phase 6
2. 修改 Q{N} 回覆 → 跳回 Phase 4 該題
3. 修改其他區塊 → 屬下手動 Edit md → 回 Phase 5
4. 跳過寄出 → 結束（檔案保留）

---

## Phase 6：寄出（本 plugin 內建 send_work_log_email.py）

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/send_work_log_email.py \
  daily_proposal/daily_work_log_{target_date}.md --to {主管 email}
```
> 收件人（主管 email）用 `--to` 指定，或事先設在 `~/.claude/daily-work-log/config.json` 的 `outlook_email`。
> 寄信功能已內建，無須額外安裝 daily-work-log plugin。

主管會在「每日工作報告」資料夾收到屬下日報。
- subject: `每日工作報告 {target_date 用 / 分隔}`
- body: HTML 渲染的日報
- attachment: daily_work_log_{target_date}.md

> ⚠️ **重要**：屬下日報主旨與主管 reply 的 conversation 串自動關聯，下一輪主管 /hi 跑 team-daily-fetcher Step 5.6 用 anchor regex 抽 OM_QA_START/END 區塊，比對 card_id 確認回覆閉環。

---

## 終止條件

- ✅ 7 個 task 全 completed
- ✅ 日報 md 已寫入（含或不含 OM_QA anchor 區塊）
- ✅ 屬下確認寄出 OR 主動跳過

---

## 故障排除

| 問題 | 解法 |
|------|------|
| Phase 0 找不到主管郵件 | 確認 Outlook MCP 連線；確認屬下「寄件備份」資料夾有 previous_workday 日報 |
| Phase 1 日誌 md 沒產出 | 腳本只印 JSON——確認你有依 1-2 模板把 JSON 渲染並**寫檔**到 `daily_proposal/daily_work_log_{date}.md`（最常見漏做步驟） |
| Phase 3 找不到 `## 待處理事項` heading | fallback 到末尾並標 anomaly（已內建） |
| Phase 4 evidence_hint 找不到對應 repo | 屬下手動指定 repo 路徑，或標 Q{N} 為「待下次回覆」 |
| Phase 6 Outlook COM 失敗 | 屬下手動 attach md 並寄送 |
