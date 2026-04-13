---
name: daily-work-log
description: 跨專案工作日誌產生器。掃描指定日期的所有 Claude Code session JSONL 檔案，彙整各專案工作進度，輸出結構化 markdown 日誌。觸發時機：當用戶說「彙整工作進度」、「工作日誌」、「session summary」、「今天做了什麼」、「整理今天的工作」、「產出工作日誌」、「日誌」時使用。即使用戶只是隨口問「今天做了哪些事」也應觸發此 skill。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep, ToolSearch, TaskCreate, TaskUpdate, TaskList, AskUserQuestion
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
1. 若有未 commit 修改或未 push commit → **先提醒使用者去 commit/push**，等使用者回覆後再繼續
2. 使用者說「做完了」→ 重新檢查一次 git 狀態
3. 使用者說「先不做」或「跳過」→ 繼續產出 md，並在日誌的「待辦事項」區塊明列哪些專案有未提交/未推送的變更

> **→ TaskUpdate: Phase 2 → completed**

---

## Phase 3：深度分析（可選）

> **→ TaskUpdate: Phase 3 → in_progress**

如果 claude-mem MCP 可用，用 timeline 補充摘要：

```
mcp__plugin_claude-mem_mcp-search__timeline(date="{YYYY-MM-DD}")
```

這能取得當天已結構化的 observations，比原始 JSONL 更精煉。

若 claude-mem 不可用或資料不足，直接從 Phase 1 的 JSON 中的 `topic_hints`（用戶前幾句話）推斷工作主題。

> **→ TaskUpdate: Phase 3 → completed**

---

## Phase 4：產出日誌 Markdown

> **→ TaskUpdate: Phase 4 → in_progress**

輸出路徑：`daily_proposal/daily_work_log_{YYYY-MM-DD}.md`

### 日誌結構模板

```markdown
# {YYYY-MM-DD} 工作日誌

> 產出時間：{now}
> 來源：Claude Code session 分析 + ccusage
> 統計：Claude {N} sessions | Codex {N} sessions | Gemini {N} sessions | {M} 專案 | {earliest} → {latest}
> 成本：${total_cost} （Opus: ${opus_cost} | Sonnet: ${sonnet_cost} | Haiku: ${haiku_cost}）

---

## 一、{專案名}（Claude {N} + Codex {N} + Gemini {N} sessions | ${cost}）

### Claude Code 工作
- 具體工作項目 1
- 具體工作項目 2

### Codex CLI / Desktop 工作
- ...（標註來源：CLI 或 Desktop）

### Gemini CLI 工作
- ...

## 二、{專案名}（Claude {N} + Codex {N} + Gemini {N} sessions | ${cost}）
...

---

## Git 狀態

### {專案名}
- 今日 commit：{N} 個
  - `{hash}` {commit message}
- 未 push：{N} 個（或「無」）
- 未 commit 修改：{N} 個檔案（或「無」）

---

## 待辦事項（若有未提交/未推送的變更）

- [ ] {專案名}：{N} 個檔案未 commit
- [ ] {專案名}：{N} 個 commit 未 push

---

## Token 用量與成本

### 專案成本分解（含模型用量）

#### {專案名} | {N} sessions | ${cost}
| 模型 | Input | Output | Cache Create | Cache Read | Total | 成本 |
|------|-------|--------|--------------|------------|-------|------|
| {model} | {n} | {n} | {n} | {n} | {n} | ${cost} |

#### claude-mem observer（背景記憶寫入稅）| {N} sessions | ${cost}
| 模型 | Input | Output | Cache Create | Cache Read | Total | 成本 |
|------|-------|--------|--------------|------------|-------|------|
| claude-sonnet-4-5 | {n} | {n} | {n} | {n} | {n} | ${cost} |

> claude-mem observer 為背景程序，非人工作業。

#### 合計

| 項目 | Sessions | Total Tokens | 成本 |
|------|----------|--------------|------|
| **當日合計** | {N} | {n} | **${total}** |

> 來源：`ccusage daily -i -j`。Token 數以 K/M 為單位。

---

## 產出清單

| # | 產出項目 | 狀態 |
|---|---------|------|
| 1 | ... | 完成 |
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

讀取 `~/.claude/daily-work-log/config.json`：
- `outlook_email` 為空 → 告知「寄信功能未設定，如需啟用請執行 Phase 0 設定流程」，標記完成
- `outlook_email` 有值 → 執行寄信腳本：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/send_work_log_email.py daily_proposal/daily_work_log_{YYYY-MM-DD}.md
```

腳本會自動：
- 讀取 md 檔並轉換為正式郵件 HTML（微軟正黑體、大豐綠配色）
- 郵件標題：`每日工作報告 YYYY/MM/DD`
- 收件者：讀取 config 的 `outlook_email`
- 透過 Outlook COM 開啟草稿視窗（**不會自動發送**）

告知使用者：「Outlook 草稿已開啟，請確認內容後按發送。」

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
