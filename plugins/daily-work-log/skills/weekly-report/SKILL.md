---
name: weekly-report
description: 屬下端週報產生器。掃描一週 Claude Code session + 各 repo 的 spec/plan/tasks.md，偵測 spec drift（實作偏離 spec）、抽品質/方向/風險 signal（老手 vs AI-coder junior 雙檔），產出週報 md 並透過 Outlook 寄給主管，夾帶 md 附件。觸發時機：使用者說「週報」「週回顧」「weekly report」「本週工作總結」「這週做了什麼」「上週工作報告」時使用。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, ToolSearch, TaskCreate, TaskUpdate, TaskList, AskUserQuestion
---

# 週報產生器（屬下端）

自動掃描一週的工作內容，交叉比對各 repo 的 SDD spec（spec.md / plan.md / tasks.md），偵測 spec drift，並依屬下角色（老手/AI 小白）抽對應 signal，最後組裝週報 md 寄給主管。

---

## MANDATORY EXECUTION PROTOCOL

**此 skill 包含 6 個 Phase（0-5），每個 Phase 都必須執行。不可跳過、不可簡化、不可合併。**

### 開始前必做：

1. `ToolSearch("select:TaskCreate,TaskUpdate,TaskList,AskUserQuestion")` 載入工具
2. `TaskCreate` 建立以下 6 個 tasks：

| Task | 內容 |
|------|------|
| 1 | Phase 0：環境與設定檢查 |
| 2 | Phase 1：Spec 狀態掃描（各 repo） |
| 3 | Phase 2：Session 掃描 + Signal 偵測 |
| 4 | Phase 3：Spec drift 交叉分析 |
| 5 | Phase 4：產出週報 Markdown |
| 6 | Phase 5：確認與寄信 |

3. 每 Phase 開始 → `TaskUpdate` 標 `in_progress`
4. 每 Phase 完成 → `TaskUpdate` 標 `completed`
5. **Phase 4 完成後必須立即進入 Phase 5，不得停止回應**
6. **Phase 5 完成後 → `TaskList` 確認 6 個 tasks 全 completed → 才能結束**

---

## 接受參數

- **週範圍**：
  - 預設「本週」（週一 00:00 ~ 週日 23:59，GMT+8）
  - 「上週」→ `--last-week`
  - 「4/14 那週」→ `--week 2026-04-14`（傳入的日期所在週）

---

## Phase 0：環境與設定檢查

> **→ TaskUpdate: Phase 0 → in_progress**

讀 `~/.claude/daily-work-log/config.json`。檢查以下欄位：

| 欄位 | 必要 | 說明 |
|------|------|------|
| `user_name` | ✅ | 屬下本人姓名（寫進週報 header） |
| `user_role` | ✅ | `senior` 或 `ai-coder-junior`（決定 signal 規則） |
| `manager_email` | ✅ | 週報收件者（主管） |
| `repos` | ✅ | 要掃的 repo 絕對路徑清單（至少 1 個） |
| `week_start` | 選配 | `monday`（預設）或 `sunday` |
| `outlook_email` | 選配 | 日報用，週報用 `manager_email`；若後者缺失則 fallback |

### 若缺失任何必要欄位

用 `AskUserQuestion` 逐題引導設定（一次問 1-3 題）：

1. **user_name**：「你的姓名（會出現在週報標題）」
2. **user_role**：「你偏向 (a) 資深工程師（手寫 code 為主）還是 (b) AI-coder 小白（純靠 AI 寫）？影響 signal 分析方式」
3. **manager_email**：「主管收件 email」
4. **repos**：「你負責的 repo 路徑（可多個，每行一個絕對路徑）」

收到回覆後，建立/更新 `~/.claude/daily-work-log/config.json`（**保留現有欄位**，只 merge 新欄位）。

```bash
mkdir -p ~/.claude/daily-work-log
```

> **→ TaskUpdate: Phase 0 → completed**

---

## Phase 1：Spec 狀態掃描

> **→ TaskUpdate: Phase 1 → in_progress**

對 `config.repos` 每個 repo 檢查 SDD 三檔（`spec.md` / `plan.md` / `tasks.md`）是否存在、最後更新時間、git log。

**這一步和 Phase 2 合併在同一個 script call，不重複呼叫**——執行：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/weekly_report.py {--week YYYY-MM-DD | --last-week | 留空=本週}
```

輸出為 JSON，包含：
- `meta`：週範圍、user_name、user_role、manager_email、iso_week
- `repos[]`：每個 repo 的 `spec`、`drift`、`signals`、`sessions`、`session_count`
- `summary`：總數統計

**注意**：script 不會自己寫 md，它只產資料。你（Claude）在 Phase 4 讀這份 JSON 組裝 md。

**若某個 repo `spec.md` 不存在**：在 Phase 4 的週報裡該 repo 區塊明確標記「⚠ 未找到 SDD 檔案（spec.md/plan.md/tasks.md）」並提醒屬下補上。

### 1-1. Claude Code 訂閱用量

呼叫 usage tracker 取得當前 7 天/5 小時滾動窗用量（Anthropic `/usage` 背後的 endpoint）：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/claude_usage_tracker.py
```

- 若 `ok: true` → 記下 `seven_day.utilization_pct` 與 `seven_day.resets_at_local`，Phase 4 的 md header 要加一行呈現；若有 `seven_day_sonnet` / `seven_day_opus` 也一併列出
  - 若 `resets_at_local` 為 null，改用 `resets_at_utc`；若兩者都為 null，md header 只寫百分比，省略括號內的重置時間
- 若 `ok: false`（token 過期、API key 用戶、endpoint 失效等）→ Phase 4 標示「Claude Code 用量：無法取得（原因）」，不要中斷流程

此 endpoint 未文檔化，失敗屬正常降級，**不要**視為錯誤重試。

> **→ TaskUpdate: Phase 1 → completed**

---

## Phase 2：Session 掃描 + Signal 偵測

> **→ TaskUpdate: Phase 2 → in_progress**

Phase 1 的 script 同時完成 session 掃描與 signal 偵測。你需要**仔細讀 JSON 輸出的以下欄位**：

### 若 `user_role = ai-coder-junior`

重點看：
- `signals.ignored_ai_warnings`：AI 警告被忽略的案例（🔴 高風險）
- `signals.no_verification_commits`：寫完沒跑測試/執行就 commit（🔴）
- `signals.fast_accept_ratio`：AI 給 code 秒接受的比例（>0.4 = 🟡 高度依賴）
- `signals.repeated_errors`：週內重複出現的錯誤關鍵字

### 若 `user_role = senior`

重點看：
- `signals.repeated_debugging`：多 session 碰同檔案 + 出現錯誤關鍵字（🟡 卡關信號）
- `signals.detour_sessions`：單 session 高工具多樣性 + 長時長（可能繞路）

### 對每個 signal 必須做的事

- **有具體 session_id 引用**才能寫進週報，沒有就不要提
- 用白話描述：不寫「fast_accept_ratio = 0.5」，寫「AI 給 code 後約一半時候立刻採用，沒自己修改」

> **→ TaskUpdate: Phase 2 → completed**

---

## Phase 3：Spec drift 交叉分析

> **→ TaskUpdate: Phase 3 → in_progress**

讀 JSON 的 `repos[].drift`。每個 repo 會有：

| 欄位 | 含意 |
|------|------|
| `out_of_scope_files` | 該週碰的檔案，但 spec.md 沒提 → 「超前 spec」 |
| `untouched_scope_files` | spec.md 提到但該週沒碰 → 「停滯」 |
| `tasks_done_but_unchecked` | commit 看起來做完但 tasks.md checkbox 沒勾 → 「checkbox drift」|

### 撰寫規則

- 超前 spec → 寫「⚠ 建議更新 spec.md 加入 {檔案/功能}」
- 停滯 → 寫「spec 列了 {檔案} 但本週未碰，原因？」
- checkbox drift → 列出應勾選的 task
- 若 spec.md 本身 `days_since_spec_update > 14` → 標 🔴「spec 可能已僵屍化，建議主管 review」

### Drift 視覺化（放進 md）

```
Scope 對齊度：{%}
🟢 照 spec：{前 3 項}
⚠ 超前 spec：{out_of_scope_files 前 5 個}
🔴 停滯未做：{untouched_scope_files 前 5 個}
```

Scope 對齊度計算：`len(touched ∩ spec_files) / len(touched ∪ spec_files) * 100`（近似 Jaccard）。若 spec_files 為空則標 N/A。

> **→ TaskUpdate: Phase 3 → completed**

---

## Phase 4：產出週報 Markdown

> **→ TaskUpdate: Phase 4 → in_progress**

輸出路徑：`weekly_reports/weekly_report_{iso_week}.md`（`iso_week` 從 JSON `meta.iso_week`，例 `2026-W16`）

### md 結構模板

**關鍵設計**：分 AI 觀察 Lock 區 + 屬下補充區。主管閱讀時 AI 觀察區是 ground truth，補充區是屬下自己加的 context。

```markdown
# 週報｜{user_name}｜{iso_week}

> 週範圍：{week_start} ~ {week_end}
> 角色：{user_role_中文}（資深工程師 / AI-coder 小白）
> 產出時間：{generated_at}
> 涉及 repo：{N} 個｜Session：{N}｜Commit：{N}
> Claude Code 本週用量：{seven_day_pct}%（重置 {resets_at_local}）{若 Sonnet/Opus 分開：｜Sonnet {N}%｜Opus {N}%}

---

<!-- ========== AI 觀察原文（請勿修改）==========  -->

## 本週概況

{3-5 條白話，從 signal / drift / commits 提煉}

## 各 repo 狀態

### {repo_name}

#### Spec 狀態
- spec.md：最後更新 {date}（{days_ago} 天前）{僵屍化警示 if days>14}
- plan.md / tasks.md：{存在性 + tasks 完成度 X/Y}
- 本週 commit：{N} 個
  - `{hash}` {subject}
  - ...（最多列 5 個）
- 未 push：{N} 個{如 >0 標 🟡}
- 未 commit 修改：{有/無}

#### Spec 對齊
- Scope 對齊度：{%}
- ⚠ 超前 spec（建議補進 spec）：{檔案清單}
- 🔴 停滯（spec 列了未碰）：{檔案清單}
- Tasks checkbox drift：{已完但未勾的 task}

#### 本週觀察（signal）

{依 user_role 寫對應 signal，每條必須附 session 引用}

範例（小白）：
- 🔴 4/15 session `xxx123`：AI 提醒 SQL injection 風險後，
  我回「好」直接使用該 code，沒實際修改
- 🟡 本週 12 次 AI 給 code → 立刻採用比例 55%

範例（老手）：
- 🟡 `auth/session.py` 本週在 4 個 session 出現錯誤關鍵字
  （4/14 abc, 4/16 def, 4/18 ghi）→ 可能卡關
- 🟡 4/17 session `mno789`：單 session 用了 9 種工具 + 180 分鐘
  → 可能方向不穩

### {下一個 repo}
...

<!-- ========== 以下由我補充 ==========  -->

## 我的補充說明

（屬下自由撰寫：為什麼這週這樣安排、遇到什麼外部阻礙、需要主管協助的地方）

## 下週計畫

- [ ] {task 1}
- [ ] {task 2}
```

### 寫作原則（嚴格）

- **AI 觀察區只能包含 JSON 裡有的事實**，不腦補、不誇大
- 每個 signal 必須附 session_id 引用（沒有就刪掉不寫）
- 不使用 emoji（🔴🟡🟢 可以用，因為是狀態碼）
- 用白話但專業，主管看得懂不用太技術
- 「我的補充說明」和「下週計畫」兩區要留空讓屬下填（放引導文字）

### 產出後

```bash
mkdir -p weekly_reports
```

寫進 `weekly_reports/weekly_report_{iso_week}.md`。

> **→ TaskUpdate: Phase 4 → completed**
> 
> **⚠ 還沒完成。Phase 5 是必做步驟。不得在此停止。立即進入 Phase 5。**

---

## Phase 5：確認與寄信

> **→ TaskUpdate: Phase 5 → in_progress**

### 5-1. 請屬下補充

用 `AskUserQuestion` 詢問：

> 週報 AI 觀察區已產出（位置：`weekly_reports/weekly_report_{iso_week}.md`）。
>
> 請補上兩區：
> 1. **我的補充說明**：這週額外要跟主管說的 context
> 2. **下週計畫**：下週準備做什麼（checkbox 格式）
>
> 可以直接在終端回覆，我幫你填進去；或說「我自己改 md 了」，我直接進下一步寄信。

等待回覆：
- 使用者提供文字 → Edit md 檔案的對應區段（找 `## 我的補充說明` 和 `## 下週計畫`）
- 使用者說自己改了 → 跳過填寫
- 使用者說不寄 → 跳過 5-2 直接完成

### 5-2. 再次確認與寄信

用 `AskUserQuestion` 最終確認：

> 週報 md 已完成，我要寄給主管（{manager_email}）並夾帶 md 附件。確認寄出嗎？

- 「確認」/「好」/「寄」→ 執行寄信
- 「還要改」→ 依指示修改再次確認

寄信已改走 df-graph（純雲端 Graph API，不再需要 Windows + Outlook Desktop）。
腳本只負責「md → HTML → 寫暫存檔 → emit payload」，由 agent 呼叫 df-graph 建**草稿**（不自動寄出）。

跑腳本取得 payload（stdout 為一行 JSON；進度在 stderr）：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/send_weekly_report_email.py weekly_reports/weekly_report_{iso_week}.md
```

payload 形如：`{"action":"mail_draft","to":...,"subject":...,"body_file":"/tmp/.../*.html","attachments":".../*.md"}`

用 payload 呼叫 df-graph 建草稿（**大型 HTML 內文走 `body_file`，不經對話**）：
```
mcp__df-graph__mail_draft(to=<payload.to>, subject=<payload.subject>,
                          body_file=<payload.body_file>, attachments=<payload.attachments>)
```

腳本會自動：
- 讀 md 檔並轉為 HTML（AI 觀察區會用黃色框視覺標記）
- 郵件標題：`週工作報告 YYYY 第 NN 週`
- 收件者：`config.manager_email`（未設則 fallback `outlook_email`）
- 自動夾帶 md 原檔為附件

草稿會出現在你的「草稿匣」，過目後手動寄出。

告知屬下：「郵件草稿已建立在草稿匣，請確認內容後手動寄出。md 檔已夾為附件。」

> **→ TaskUpdate: Phase 5 → completed**

---

## COMPLETION GATE

1. `TaskList` 列所有 tasks
2. 確認 6 個 Phase tasks **全部** 為 completed
3. 未完成 → 回去補，不得結束
4. 全 completed → skill 執行完畢

**未通過 COMPLETION GATE 就結束 = 執行失敗。**

---

## 注意事項

### 保守原則（與 daily-work-log 一致）

- AI 觀察區只寫從 JSON 實際觀察到的，不誇大
- signal 沒有具體 session 引用就不寫
- 「完成」/「進行中」/「卡關」嚴格區分，不亂貼

### Spec drift 常見誤判

- **檔名大小寫不一致** → script 已用 basename 比對，但仍可能漏掉
- **spec.md 用相對路徑 vs session 用絕對路徑** → script 會同時比 basename 避免
- 若 `spec_file_refs` 為空（spec.md 寫的是純文字沒路徑）→ 跳過 drift 檢查，標記「spec 缺乏可比對的結構化 scope」

### 小白 signal 特別注意

- `ignored_ai_warnings` 是高風險 signal，但**會誤報**：屬下回「好」不等於真的忽略警告，可能只是回答「好我知道了然後我有改」。寫進週報時用「疑似」，並建議主管 review 該 commit 的實際內容。
- `fast_accept_ratio` 高不代表壞事（學會信任 AI 可能是好事），只是值得注意的 signal。

### 目錄結構

- 週報 md → `weekly_reports/weekly_report_{iso_week}.md`（relative to cwd）
- config → `~/.claude/daily-work-log/config.json`
- 附件即 md 本身，不另存

### 頻率建議

屬下每週五下班前跑一次。主管週末或週一彙整。
