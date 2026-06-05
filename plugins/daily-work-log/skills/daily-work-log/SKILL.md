---
name: daily-work-log
description: 跨專案工作日誌產生器。掃描指定日期的所有 Claude Code session JSONL 檔案，彙整各專案工作進度，輸出結構化 markdown 日誌。觸發時機：當用戶說「彙整工作進度」、「工作日誌」、「session summary」、「今天做了什麼」、「整理今天的工作」、「產出工作日誌」、「日誌」時使用。即使用戶只是隨口問「今天做了哪些事」也應觸發此 skill。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, ToolSearch, TaskCreate, TaskUpdate, TaskList, AskUserQuestion, Skill, mcp__plugin_claude-mem_mcp-search__timeline, mcp__github__push_files
---

# 跨專案工作日誌產生器

自動掃描指定日期的所有 Claude Code session 檔案，跨專案彙整工作進度，產出格式化的 markdown 工作日誌。

---

## MANDATORY EXECUTION PROTOCOL

**此 skill 包含 7 個 Phase（0-6），每個 Phase 都必須執行。不可跳過、不可簡化、不可合併。**

### 開始前必做（在執行任何 Phase 之前）：

1. 使用 `ToolSearch("select:TaskCreate,TaskUpdate,TaskList,AskUserQuestion")` 載入所需工具
2. 使用 `TaskCreate` 建立以下 7 個 tasks：

| Task | 內容 |
|------|------|
| 1 | Phase 0：環境與設定檢查 |
| 2 | Phase 1：Session 掃描 + 成本數據 |
| 3 | Phase 2：Git 狀態檢查 |
| 4 | Phase 3：深度分析 |
| 5 | Phase 4：產出日誌 Markdown |
| 6 | Phase 5：呈現結果 |
| 7 | Phase 6：確認與寄信 |

3. 每個 Phase 開始時 → `TaskUpdate` 標記 `in_progress`
4. 每個 Phase 完成後 → `TaskUpdate` 標記 `completed`
5. **Phase 5 完成後必須立即進入 Phase 6，不得停止回應**
6. **Phase 6 完成後 → `TaskList` 確認全部 7 個 tasks 都是 completed → 才能結束**

**如果你發現自己想跳過某個 Phase，停下來。那就是你需要執行它的信號。**

---

## 接受參數

- **日期**：從用戶訊息中提取目標日期，預設為今天
  - 「今天做了什麼」→ today
  - 「昨天的工作日誌」→ yesterday (計算日期)
  - 「3/9 工作日誌」→ 2026-03-09

---

## Phase 0：環境與設定檢查

> **→ TaskUpdate: Phase 0 → in_progress**

### 0-1. 檢查 ccusage

```bash
ccusage --version 2>&1
```

- **成功** → 記錄版本，進入 0-2
- **失敗（command not found）** → 使用 AskUserQuestion 引導安裝：

  > ccusage 尚未安裝。ccusage 用於精確計算每日 token 成本，沒有它成本數據將不可用。
  >
  > 請選擇：
  > 1. `npm install -g ccusage` 立即安裝
  > 2. `pnpm add -g ccusage` 立即安裝
  > 3. 跳過（本次不統計成本）

  - 使用者選 1 或 2 → 執行對應指令，安裝完再跑 `ccusage --version` 確認
  - 使用者選 3 → 標記 `ccusage_available = false`，後續跳過成本統計步驟

### 0-2. 檢查寄信設定

```bash
cat ~/.claude/daily-work-log/config.json 2>/dev/null
```

- **檔案存在且有 `outlook_email`（非空字串）** → 記錄 email，進入 Phase 1
- **檔案不存在或 `outlook_email` 為空** → 使用 AskUserQuestion 引導設定：

  > 首次使用 daily-work-log，需要設定 Outlook 寄信收件者。
  >
  > 請提供收件者 email（例如：name@company.com）
  > 或輸入「skip」跳過寄信功能。

  - 使用者提供 email → 建立目錄和 config：
    ```bash
    mkdir -p ~/.claude/daily-work-log
    ```
    寫入 `~/.claude/daily-work-log/config.json`：`{"outlook_email": "user@example.com"}`
  - 使用者輸入 skip → 寫入：`{"outlook_email": ""}`

> **→ TaskUpdate: Phase 0 → completed**

---

## Phase 1：Session 掃描 + 成本數據

> **→ TaskUpdate: Phase 1 → in_progress**

執行 Python 腳本掃描所有專案的 session 檔案（Claude Code + Codex CLI/Desktop + Gemini CLI）：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/daily_work_log.py {YYYY-MM-DD|today}
```

若 Phase 0 確認 ccusage 可用，**務必**呼叫 ccusage 拿到準確的當日按專案分解成本（腳本內建的 `--with-cost` 從 JSONL 累加 costUSD 會嚴重虛高，不要用）：

```bash
ccusage daily --since {YYYYMMDD} --until {YYYYMMDD} -i -j
```

這會得到 JSON：
- `projects.{project_dir}[].totalCost` — 該專案當日實際計費
- `projects.{project_dir}[].modelBreakdowns[]` — 該專案當日每個模型的 input/output/cache tokens 與成本
- `totals.totalCost` — 當日合計

若 ccusage 不可用，跳過成本統計，日誌中成本欄位標示「N/A（ccusage 未安裝）」。

**claude-mem observer 專案的說明**：若出現 `-home-hahahuang--claude-mem-observer-sessions` 專案，這是 `claude-mem` plugin 的背景 observer 程序（把主 session 動作結構化成 memory），**不是人工作業**。在日誌中獨立列出並標註為「背景記憶寫入稅」，不要當成實際專案。

### 1-1. Claude Code 訂閱用量

呼叫 usage tracker 取得當前 7 天/5 小時滾動窗用量（Anthropic `/usage` 背後的 endpoint）：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/claude_usage_tracker.py
```

- 若 `ok: true` → 記下 `seven_day.utilization_pct` 與 `seven_day.resets_at_local`，Phase 4 的 md header 要加一行呈現
  - 若 `resets_at_local` 為 null，改用 `resets_at_utc`；若兩者都為 null，md header 只寫百分比，省略括號內的重置時間
- 若 `ok: false`（token 過期、API key 用戶、endpoint 失效等）→ Phase 4 標示「Claude Code 用量：無法取得（原因）」，不要中斷流程

此 endpoint 未文檔化，失敗屬正常降級，**不要**視為錯誤重試。

> **→ TaskUpdate: Phase 1 → completed**

---

## Phase 2：Git 狀態檢查

> **→ TaskUpdate: Phase 2 → in_progress**

在產出 md 之前，**先檢查每個今天有活動的專案目錄的 git 狀態**：

```bash
cd {project_dir} && git log --since="{YYYY-MM-DD} 00:00" --until="{YYYY-MM-DD} 23:59" --oneline 2>/dev/null
cd {project_dir} && git status --short 2>/dev/null
cd {project_dir} && git log @{u}.. --oneline 2>/dev/null  # 未 push 的 commit
```

對每個專案記錄：
- 今日 commit 數量與訊息
- 是否有未 commit 的修改（working tree dirty）
- 是否有未 push 的 commit

**處理規則**：
1. 若有未 commit 修改或未 push commit → 進入 **2-1 半自動 commit 協助**
2. 若完全乾淨 → 直接完成 Phase 2

### 2-1. 半自動 commit / push 協助（僅在偵測到 uncommitted 或 unpushed 時執行）

**若使用者回覆「全部跳過」→ 立即終止迴圈，剩下 repo 全進「待處理事項」（含未 commit 檔案清單），不再逐一詢問。**

**對每個有變動的 repo，逐一處理**（不要批次、不要一次全部 commit）：

1. **收集 repo 狀態**：
   ```bash
   cd {repo_dir} && git rev-parse --abbrev-ref HEAD 2>/dev/null                 # 目前 branch
   cd {repo_dir} && git status --short 2>/dev/null                              # 哪些檔案動了
   cd {repo_dir} && git diff --stat 2>/dev/null | tail -20                      # unstaged diff stat
   cd {repo_dir} && git diff --staged --stat 2>/dev/null | tail -20             # staged diff stat
   cd {repo_dir} && git log @{u}.. --oneline 2>/dev/null || true                # 未 push 的 commit
   cd {repo_dir} && git diff 2>/dev/null | wc -l                                # 總 diff 行數
   ```
   記錄：branch 名、變動檔案清單、unstaged/staged 比例、diff 總行數。

2. **Secret 預檢（硬性，不可跳過）**：
   ```bash
   cd {repo_dir} && git status --short | grep -iE '\.(env|key|pem|p12|pfx)$|credentials|secret|\.pypirc|id_rsa'
   ```
   - 若有 match → **停下來**，印出匹配到的檔案，用 AskUserQuestion 警告：「偵測到疑似密鑰檔 X，確定要 commit 嗎？」預設「否」。選「否」→ 本 repo 跳過，記進待辦並明列這些疑似密鑰檔要手動處理。

3. **讀 diff**（根據步驟 1 的總行數決定）：
   - 總行數 ≤ 300 → 完整 `git diff`
   - 300 < 總行數 ≤ 2000 → `git diff --stat`（所有檔案）+ `git diff --name-only`（全清單）
   - 總行數 > 2000 → 只用 `git diff --stat`，**並在 commit message draft 強制加上**「⚠ 建議拆成多個 commit（diff 超過 2000 行）」

4. **產生 commit message draft**（你自己寫，不要呼叫外部 script）：
   - subject < 50 字元，格式 `<type>: <description>`（type = feat/fix/refactor/docs/test/chore）
   - 關注「why」而不是「what」
   - 若偵測到多個無關變動（例如同時改 `.claude/skills/*` 和 `backend/data/*`）→ draft 開頭加「⚠ 變動涉及多個不相關領域，建議拆成多個 commit」

5. **用 `AskUserQuestion` 問使用者**：

   > {repo_name}（branch: {branch_name}）有變動（N 檔 / +X/-Y 行）。
   > {若 branch != main/master：⚠ 目前不在主分支}
   > 我建議的 commit message：
   > ```
   > <draft>
   > ```
   > 怎麼處理？

   選項：
   - `commit + push（用建議 message）` — 照 draft 跑步驟 6-7
   - `我自己編輯 message` — 等使用者貼新 message。收到後**必須**再用 AskUserQuestion 二次確認（「確認用此 message commit？ <new_message>」），yes 才繼續步驟 6。不得跳過二次確認
   - `這個 repo 跳過` — 記進 Phase 4 的「待處理事項」（含未 commit 檔案清單）
   - `全部跳過` — 終止本迴圈，剩下 repos 全記進待辦（含檔案清單），不再問

6. **執行 commit**（使用者確認後）：
   - **優先**：使用 `Skill` tool 呼叫 `commit-commands:commit`（若可用，格式與使用者既有流程一致）
   - **fallback**（屬下未裝 `commit-commands` plugin）：
     ```bash
     # 只 add 步驟 1 列出的「屬於本次變動」的檔案；禁用 git add -A 避免意外納入未預期檔案（尤其是 secrets）
     cd {repo_dir} && git add -- <檔案1> <檔案2> ... && git commit -m "<message>"
     ```
     （若 pre-commit hook 失敗，**不得** `--no-verify` 繞過；修問題後重跑）

7. **執行 push**：
   ```bash
   cd {repo_dir} && git push 2>&1
   ```

   **技術說明**：本 skill 的 push 一律用 bash，不用 `mcp__github__push_files`。push_files 是呼叫 Contents API 產生新的遠端 commit，無法疊加到已存在的本地 commit（會衝突）。使用者 CLAUDE.md 規則「git push 一律用 GitHub MCP」只適用於「未 commit 直接推檔案」的場景。本段若觸發 `git-push-reminder.sh` 等 hook 的提示，屬預期行為，忽略即可。

8. **Push 失敗處理**（return code 非 0）：

   印出完整 stderr，然後用 AskUserQuestion 詢問：

   > {repo} push 失敗：{stderr 第一行}
   > 怎麼處理？

   選項：
   - `保留本地 commit，之後手動 push` — 記進 Phase 4 待辦「已 commit 未 push」
   - `撤回 commit（git reset --soft HEAD~1）` — 撤 commit 保留 working tree 變動，記進待辦
   - `重試一次 push` — 再跑一次 `git push`（只允許重試 1 次，再失敗回到本題）

9. **記錄結果**（塞進 Phase 4 要用的結構）：
   - 成功：`{repo, hash, subject, branch}`
   - 跳過：`{repo, 原因, 未 commit 檔案清單（git status --short 原文）}`
   - commit 成功但 push 失敗：`{repo, hash, stderr, 使用者選擇}`

### 2-2. 原流程收尾

- Phase 4 的日報「Git 狀態」區塊：列出本流程新增的 commit（標「本次 skill 自動協助 commit」；若 branch 非主分支也標出）
- Phase 4 的「待處理事項」：
  - 跳過的 repo（含未 commit 檔案清單）
  - commit 成功但 push 失敗的 repo
  - 撤回的 commit（若使用者選 reset）

> **→ TaskUpdate: Phase 2 → completed**

---

## Phase 3：Session 內容分析

> **→ TaskUpdate: Phase 3 → in_progress**

### 3-1. claude-mem 優先（主要資料來源）

**先嘗試 claude-mem**。如果 claude-mem MCP 可用，用 timeline 取得當日結構化觀察記錄：

```
mcp__plugin_claude-mem_mcp-search__timeline(date="{YYYY-MM-DD}")
```

- **成功取得資料** → claude-mem 的觀察記錄已包含檔案操作、工具呼叫、決策等結構化資訊，**跳過 3-2**，直接用 claude-mem 資料撰寫日誌
- **失敗（MCP 不可用、無資料、或回傳錯誤）** → 進入 3-2 fallback 流程

### 3-2. Session JSONL 分析（fallback）

**僅在 claude-mem 不可用時執行此步驟。**

從 Phase 1 結果中，排除 observer sessions 後，篩選**所有** `user_msg_count >= 5` 的 sessions（不設上限，全部分析）。記錄每個 session 的 JSONL 完整路徑：`~/.claude/projects/-home-{project_key}/{session_file}`

對篩選出的 sessions 執行：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/extract_session_details.py {session1_full_path} {session2_full_path} ...
```

輸出每個 session 的：
- `user_messages`：使用者實際說了什麼（過濾掉系統訊息）
- `tool_usage`：使用了哪些工具、各幾次
- `files_touched`：讀寫了哪些檔案
- `commands_run`：執行了哪些指令
- `mcp_tools`：呼叫了哪些外部服務

利用這些資訊在 Phase 4 撰寫準確的工作描述，而非僅從 `topic_hints` 猜測。

> **→ TaskUpdate: Phase 3 → completed**

---

## Phase 4：產出日誌 Markdown

> **→ TaskUpdate: Phase 4 → in_progress**

輸出路徑：`daily_proposal/daily_work_log_{YYYY-MM-DD}.md`

### 日誌結構模板

日誌分為兩個部分：
- **Part 1（工作摘要）**：給主管看的。白話文、無技術術語、著重成果與進度。主管是非技術職，看不懂 git、token、cache 這些詞。
- **Part 2（技術執行細節）**：給主管的 Claude Code 看的。包含 session 細節、指令記錄、token 用量、git 狀態等。主管的 AI 助理可以用這些資訊追蹤具體做了什麼。

### Part 1 寫作原則

- **用白話文**：「修好了照片上傳的問題」而不是「fix photo upload silent failure」
- **講成果不講工具**：「完成課程投影片 30 頁」而不是「使用 Pencil MCP batch_design 建立 30 個 slide nodes」
- **講目的不講手段**：「評估伺服器規格」而不是「研究 GitHub README 分析 Docker image 資源需求」
- **狀態用中文**：完成、進行中、待處理、已擱置
- **不要出現的詞**：cache、session、commit、push、git、MCP、API、SDK、CLI、plugin、hook、agent、node、deploy、repo
- **可以出現的詞**：程式、系統、網站、應用、伺服器、資料庫、檔案、更新、修正、測試、上線、同步
- **成本與用量**：Part 1 包含按專案的費用分解表，用「AI 使用量」代替 token，用「輸入/輸出/快取」代替 input/output/cache

```markdown
# {YYYY-MM-DD} 工作日誌

> 日期：{YYYY-MM-DD}（{星期}）
> 工作時段：{earliest} → {latest}
> 涉及專案：{M} 個
> 今日 AI 使用費用：${total_cost}
> Claude Code 本週用量：{seven_day_pct}%（重置 {resets_at_local}）

---

## 一、{專案名（白話）}

### {工作主題（白話描述）}
- {用一般人聽得懂的方式描述做了什麼}
- {著重成果：完成了什麼、產出了什麼、解決了什麼問題}
- {如有未完成的項目，說明目前進度和下一步}

## 二、{專案名（白話）}

### {工作主題}
- ...

---

## 今日產出

| # | 項目 | 狀態 |
|---|------|------|
| 1 | {白話描述產出物} | 完成 |
| 2 | {白話描述} | 進行中（{簡述卡在哪裡}） |

---

## 待處理事項

- [ ] {白話描述需要後續處理的事情}

---

## AI 使用費用明細

| 專案 | 使用的 AI 模型 | 費用 | 備註 |
|------|---------------|------|------|
| {專案名（白話）} | {模型名，如 Claude Opus / Sonnet} | ${cost} | {原始 repo 名稱} |
| {專案名（白話）} | {模型名} | ${cost} | {原始 repo 名稱} |
| 背景自動化程序 | Claude Sonnet | ${cost} | claude-mem observer |
| **合計** | | **${total}** | |

> 「背景自動化程序」為 AI 記憶系統的自動運作費用，非人工操作。
> 「備註」欄為原始程式專案名稱，供技術人員對照。

---
---

<!-- Part 2: 技術執行細節（供 AI 助理參考，非人工閱讀區域） -->

# 技術執行細節

> 以下內容為 AI 可讀的結構化執行記錄，供後續追蹤與分析使用。

## Session 概覽

> 來源：Claude Code session 分析 + ccusage
> 統計：Claude {N} sessions | Codex {N} sessions | Gemini {N} sessions
> 時段：{earliest} → {latest}

## 各專案執行細節

### {專案名}（{session_count} sessions | ${cost}）

#### 執行動作
- {實際執行的指令、讀寫的檔案、呼叫的工具}
- {具體的 session 內容摘要}

#### Git 狀態
- 今日 commit：{N} 個
  - `{hash}` {commit message}
- 未 push：{N} 個（或「無」）
- 未 commit 修改：{N} 個檔案（或「無」）

### claude-mem observer（背景程序，非人工作業）
- sessions：{N}
- 用途：自動記憶寫入

---

## Token 用量與成本

### 按專案分解

#### {專案名} | {N} sessions | ${cost}
| 模型 | Input | Output | Cache Create | Cache Read | Total | 成本 |
|------|-------|--------|--------------|------------|-------|------|
| {model} | {n} | {n} | {n} | {n} | {n} | ${cost} |

#### claude-mem observer（背景記憶寫入稅）| {N} sessions | ${cost}
| 模型 | Input | Output | Cache Create | Cache Read | Total | 成本 |
|------|-------|--------|--------------|------------|-------|------|
| {model} | {n} | {n} | {n} | {n} | {n} | ${cost} |

### 合計

| 項目 | Sessions | Total Tokens | 成本 |
|------|----------|--------------|------|
| **當日合計** | {N} | {n} | **${total}** |

> 來源：`ccusage daily -i -j`。Token 數以 K/M 為單位。
```

> **→ TaskUpdate: Phase 4 → completed**

---

## Phase 5：呈現結果

> **→ TaskUpdate: Phase 5 → in_progress**

1. 將產出的 markdown 路徑告知用戶
2. 在終端印出重點摘要（不超過 20 行）
3. 如果用戶要求，可以直接在終端印出完整日誌

> **→ TaskUpdate: Phase 5 → completed**
>
> **⚠️ 你還沒完成。Phase 6 是必做步驟。不得在此停止回應。立即進入 Phase 6。**

---

## Phase 6：確認與寄信

> **→ TaskUpdate: Phase 6 → in_progress**
>
> **此 Phase 不可跳過。無論日報或週報、無論用戶是否提到寄信，都必須執行此 Phase。**

### 6-1. 確認日誌內容

使用 AskUserQuestion 詢問：

> 日誌已產出，要補充或修改嗎？確認後我開 Outlook 寄出。

等待使用者回覆：
- 使用者要修改 → 依指示修改 md 檔後重新呈現，再次確認
- 使用者確認 OK → 進入 6-2
- 使用者說不用寄 → 跳過 6-2，直接標記完成

### 6-2. 執行寄信（若有設定 email）

寄信已改走 df-graph（純雲端 Graph API，不再需要 Windows + Outlook Desktop）。
腳本只負責「md → HTML → 寫暫存檔 → emit payload」，由 agent 呼叫 df-graph 建**草稿**（不自動寄出）。

讀取 `~/.claude/daily-work-log/config.json`：
- `outlook_email` 為空 → 告知「寄信功能未設定，如需啟用請執行 Phase 0 設定流程」，標記完成
- `outlook_email` 有值 → 跑腳本取得 payload（stdout 為一行 JSON；進度在 stderr）：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/send_work_log_email.py daily_proposal/daily_work_log_{YYYY-MM-DD}.md
```

payload 形如：`{"action":"mail_draft","to":...,"subject":...,"body_file":"/tmp/.../*.html","attachments":".../*.md"}`

用 payload 呼叫 df-graph 建草稿（**大型 HTML 內文走 `body_file`，不經對話**）：
```
mcp__df-graph__mail_draft(to=<payload.to>, subject=<payload.subject>,
                          body_file=<payload.body_file>, attachments=<payload.attachments>)
```

草稿會出現在你的「草稿匣」，過目後手動寄出。

告知使用者：「郵件草稿已建立在草稿匣，請確認內容後手動寄出。」

**注意**：
- 郵件語氣為正式商務風格，主管會看——強調成果與進度，技術細節簡化
- 收件者可用 `--to` 參數覆蓋（如需寄給其他人）
- 不使用 emoji，用「完成」、「進行中」等文字標記

> **→ TaskUpdate: Phase 6 → completed**

---

## COMPLETION GATE

**Phase 6 完成後，執行最終檢查：**

1. `TaskList` 列出所有 tasks
2. 確認 7 個 Phase tasks **全部** 為 completed
3. 若有任何 task 未完成 → 回去補完，不得結束
4. 全部 completed → skill 執行完畢，可以結束

**未通過 COMPLETION GATE 就結束 = 執行失敗。**

---

## 注意事項

- Session 數量可能很多（30+），優先展示有實質工作的 sessions，跳過只有 1-2 則訊息的短 session
- 專案名從目錄名推導：`-home-haha-CC-project-AI-Copilot` → `AI_Copilot`
- 時間一律用 GMT+8
- 如果某專案只有零星維護（< 3 個 user messages），歸入「其他專案」

### 保守原則（非常重要）

**做多少事就寫多少事，不要誇大、不要補完、不要猜測**：

- 只記錄從 session 訊息中**實際觀察到**的動作（讀了什麼檔、執行了什麼指令、寫了什麼程式）
- 不要用「優化」、「完善」、「重構」、「深入研究」這種抽象誇大詞，除非 session 裡真的有對應的大量動作
- 「讀了兩份 md 調用 skill 做 PPT」就寫「讀取 L04/L05 md、調用 xlab-course-slides skill」，**不要腦補**「完成課程簡報製作」如果實際上只是起了個頭
- 產出清單狀態嚴格區分：
  - **完成**：有明確產物且驗證過
  - **進行中**：開始動作但未交付
  - **中斷**：user interrupted 或報錯未修
- 若 session 只有零星讀檔沒有實質產出，就寫「探索/查看」，不要寫成「分析」
- 若 JSONL 解析不出有意義的資訊，據實回報「該 session 無實質產出」而非填充

### claude-mem observer 處理

- `-home-hahahuang--claude-mem-observer-sessions` 永遠單獨列為「背景記憶寫入」區塊，不要算入實際工作專案
- 在工作主題那部分不要提到它
