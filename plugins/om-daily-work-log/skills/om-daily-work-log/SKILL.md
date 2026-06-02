---
name: om-daily-work-log
description: OM 營運部專用日誌產生器（基於 daily-work-log plugin 擴展）。除了通用日誌功能外，自動偵測主管 compose/reply 的催辦信（directive marker 契約，繞過 reply-chain 限制），引導屬下用 CC 查 git/spec/tasks 後在新一日日報的「## 主管疑問回覆」區塊（HTML anchor 標記）填答。觸發時機：屬下說「日誌」「工作日誌」「彙整工作進度」「整理今天的工作」「日報」「工作報告」（OM 屬下機器優先觸發此 skill 而非通用 daily-work-log）。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, ToolSearch, TaskCreate, TaskUpdate, TaskList, AskUserQuestion, Skill, mcp__outlook-local__list_recent_emails_tool, mcp__outlook-local__get_folder_list_tool, mcp__outlook-local__search_email_by_subject_tool, mcp__outlook-local__get_email_by_number_tool
---

# OM 營運部日誌 + 主管疑問回覆閉環

屬下端 skill，負責：
1. 沿用既有 daily-work-log plugin 的日誌產出功能（reuse 不 copy）
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
| 2 | Phase 1：呼叫既有 daily-work-log 日誌產出 |
| 3 | Phase 2：日報 markdown 載入 + anchor 偵測 |
| 4 | Phase 3：插入「主管疑問回覆」區塊 |
| 5 | Phase 4：引導屬下用 CC 查證並回答 |
| 6 | Phase 5：屬下 review 日報整體 |
| 7 | Phase 6：呼叫既有 send_work_log_email.py 寄出 |

3. 每 Phase 開始 / 結束都 TaskUpdate

---

## Phase 0：偵測主管催辦信（directive-first）

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
1. `ToolSearch("select:mcp__outlook-local__search_email_by_subject_tool,mcp__outlook-local__list_recent_emails_tool,mcp__outlook-local__get_email_by_number_tool")`
2. 搜當日信：`search_email_by_subject_tool(subject="【每日追蹤】")`（前綴比對），
   或 `list_recent_emails_tool(days=1)` 後過濾主旨含前綴者。
3. 對候選信 `get_email_by_number_tool(email_number=N, mode="basic")` 取 body，抽 marker：
   ```python
   import re
   M = re.compile(r"<!--\s*OM_DIRECTIVE\s+(?P<meta>[^>]+?)-->")
   META = re.compile(r"(\w+)=(\S+)")   # directive_id / target_date / employee_id / source
   ```
4. 取 `target_date` 命中今日目標日、`employee_id` 為自己者的**最新一封**。
5. 從 body 的 `## Q1 / ## Q2 …` 標題 + 內容抽出問題清單（`directive_id` 即原 card_id）。

### Fallback（MCP 搜不到或不穩時）
跑 reply-chain 腳本（只認 reply 來源，抓不到 compose 催辦信）：
```bash
python3 ~/.claude/plugins/cache/df-haha-plugins/om-daily-work-log/1.0.0/scripts/handle_supervisor_questions.py \
  {target_date} --output-json
```
輸出 JSON：`has_supervisor_email` / `previous_date` / `card_id` / `review_thread_id` /
`review_message_id` / `questions[]`（`{id, title, body, evidence_hint}`）。

### 結果分支
- 偵測到催辦信 → 顯示：「主管寄了 N 個問題（來源：{source}）：Q1…、Q2…，待會引導你回覆」→ 進 Phase 1
- 無 → 「未偵測到主管催辦信，本日日報跳過 Phase 3-4」→ 進 Phase 1

---

## Phase 1：呼叫既有 daily-work-log 日誌產出

### 重要：reuse 不 copy

直接呼叫 daily-work-log plugin 安裝路徑的腳本：

```bash
python3 ~/.claude/plugins/cache/df-haha-plugins/daily-work-log/1.7.2/scripts/daily_work_log.py \
  {target_date} \
  --with-cost
```

> **路徑解析**：實際版本可能不是 1.7.2，用 `ls ~/.claude/plugins/cache/df-haha-plugins/daily-work-log/` 取最新版號
> ```bash
> DWL_VERSION=$(ls ~/.claude/plugins/cache/df-haha-plugins/daily-work-log/ | sort -V | tail -1)
> python3 ~/.claude/plugins/cache/df-haha-plugins/daily-work-log/$DWL_VERSION/scripts/daily_work_log.py {target_date} --with-cost
> ```

### Fallback
若 daily-work-log plugin 未安裝：
- 顯示錯誤：「請先安裝 daily-work-log plugin：`/plugin install daily-work-log@df-haha-plugins`」
- 退出 skill

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

## Phase 6：寄出（reuse 既有 send_work_log_email.py）

### 重要：reuse 不 copy

```bash
DWL_VERSION=$(ls ~/.claude/plugins/cache/df-haha-plugins/daily-work-log/ | sort -V | tail -1)
python3 ~/.claude/plugins/cache/df-haha-plugins/daily-work-log/$DWL_VERSION/scripts/send_work_log_email.py \
  daily_proposal/daily_work_log_{target_date}.md
```

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
| Phase 1 daily-work-log plugin 未安裝 | `/plugin install daily-work-log@df-haha-plugins` |
| Phase 3 找不到 `## 待處理事項` heading | fallback 到末尾並標 anomaly（已內建） |
| Phase 4 evidence_hint 找不到對應 repo | 屬下手動指定 repo 路徑，或標 Q{N} 為「待下次回覆」 |
| Phase 6 Outlook COM 失敗 | 屬下手動 attach md 並寄送 |
