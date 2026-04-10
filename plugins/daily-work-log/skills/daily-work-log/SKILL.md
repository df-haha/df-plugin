---
name: daily-work-log
description: 跨專案工作日誌產生器。掃描指定日期的所有 Claude Code session JSONL 檔案，彙整各專案工作進度，輸出結構化 markdown 日誌。觸發時機：當用戶說「彙整工作進度」、「工作日誌」、「session summary」、「今天做了什麼」、「整理今天的工作」、「產出工作日誌」、「日誌」時使用。即使用戶只是隨口問「今天做了哪些事」也應觸發此 skill。
allowed-tools: Bash, Read, Write, Edit, Glob, Grep
---

# 跨專案工作日誌產生器

自動掃描指定日期的所有 Claude Code session 檔案，跨專案彙整工作進度，產出格式化的 markdown 工作日誌。

## 接受參數

- **日期**：從用戶訊息中提取目標日期，預設為今天
  - 「今天做了什麼」→ today
  - 「昨天的工作日誌」→ yesterday (計算日期)
  - 「3/9 工作日誌」→ 2026-03-09

## 執行流程

### Phase 0：環境與設定檢查

1. 檢查 ccusage：
   - `ccusage --version`
   - 失敗 → 提示：`npm install -g ccusage`（GitHub: ryoppippi/ccusage）
   - ccusage 是 optional，跳過不影響核心功能

2. 檢查 config：
   - 讀 `~/.claude/daily-work-log/config.json`
   - 不存在或無 outlook_email →
     詢問使用者：「首次使用，請提供 Outlook 收件者 email（或輸入 skip 跳過寄信功能）」
   - 使用者提供 email → 建立 config.json: `{"outlook_email": "user@example.com"}`
   - 使用者 skip → 建立 config.json: `{"outlook_email": ""}`
   - 已有 config 且有 email → 直接進 Phase 1

### Phase 1：Session 掃描 + 成本數據

執行 Python 腳本掃描所有專案的 session 檔案（Claude Code + Codex CLI/Desktop + Gemini CLI）：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/daily_work_log.py {YYYY-MM-DD|today}
```

另外**務必**直接呼叫 ccusage 拿到準確的當日按專案分解成本（腳本內建的 `--with-cost` 從 JSONL 累加 costUSD 會嚴重虛高，不要用）：

```bash
ccusage daily --since {YYYYMMDD} --until {YYYYMMDD} -i -j
```

這會得到 JSON：
- `projects.{project_dir}[].totalCost` — 該專案當日實際計費
- `projects.{project_dir}[].modelBreakdowns[]` — 該專案當日每個模型的 input/output/cache tokens 與成本
- `totals.totalCost` — 當日合計

**claude-mem observer 專案的說明**：若出現 `-home-hahahuang--claude-mem-observer-sessions` 專案，這是 `claude-mem` plugin 的背景 observer 程序（把主 session 動作結構化成 memory），**不是人工作業**。在日誌中獨立列出並標註為「背景記憶寫入稅」，不要當成實際專案。

### Phase 2：Git 狀態檢查（新增，必做）

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

### Phase 3：深度分析（可選）

如果 claude-mem MCP 可用，用 timeline 補充摘要：

```
mcp__plugin_claude-mem_mcp-search__timeline(date="{YYYY-MM-DD}")
```

這能取得當天已結構化的 observations，比原始 JSONL 更精煉。

若 claude-mem 不可用或資料不足，直接從 Phase 1 的 JSON 中的 `topic_hints`（用戶前幾句話）推斷工作主題。

### Phase 4：產出日誌 Markdown

輸出路徑：`daily_proposal/daily_work_log_{YYYY-MM-DD}.md`

#### 日誌結構模板

```markdown
# {YYYY-MM-DD} 工作日誌

> 產出時間：{now}
> 來源：Claude Code session 分析 + ccusage
> 統計：Claude {N} sessions ｜ Codex {N} sessions ｜ Gemini {N} sessions ｜ {M} 專案 ｜ {earliest} → {latest}
> 成本：${total_cost} （Opus: ${opus_cost} ｜ Sonnet: ${sonnet_cost} ｜ Haiku: ${haiku_cost}）

---

## 一、{專案名}（Claude {N} + Codex {N} + Gemini {N} sessions ｜ ${cost}）

### Claude Code 工作
- 具體工作項目 1
- 具體工作項目 2

### Codex CLI / Desktop 工作
- ...（標註來源：CLI 或 Desktop）

### Gemini CLI 工作
- ...

## 二、{專案名}（Claude {N} + Codex {N} + Gemini {N} sessions ｜ ${cost}）
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

#### {專案名} ｜ {N} sessions ｜ ${cost}
| 模型 | Input | Output | Cache Create | Cache Read | Total | 成本 |
|------|-------|--------|--------------|------------|-------|------|
| {model} | {n} | {n} | {n} | {n} | {n} | ${cost} |

#### claude-mem observer（背景記憶寫入稅）｜ {N} sessions ｜ ${cost}
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

### Phase 5：呈現結果

1. 將產出的 markdown 路徑告知用戶
2. 在終端印出重點摘要（不超過 20 行）
3. 如果用戶要求，可以直接在終端印出完整日誌

### Phase 6：確認與寄信

日誌呈現後，進入確認寄信流程：

1. **詢問使用者**：「日誌已產出，要補充或修改嗎？確認後我開 Outlook 寄出。」
2. **等待回覆**：
   - 使用者要修改 → 依指示修改 md 檔後重新呈現，再次確認
   - 使用者確認 OK → 進入寄信步驟
   - 使用者說不用寄 → 結束流程
3. **執行寄信腳本**：
   ```bash
   python3 ${CLAUDE_PLUGIN_ROOT}/scripts/send_work_log_email.py daily_proposal/daily_work_log_{YYYY-MM-DD}.md
   ```
4. 腳本會自動：
   - 讀取 md 檔並轉換為正式郵件 HTML（微軟正黑體、大豐綠配色）
   - 郵件標題：`每日工作報告 YYYY/MM/DD`
   - 收件者：讀取 `~/.claude/daily-work-log/config.json` 的 `outlook_email`（未設定則跳過寄信）
   - 透過 Outlook COM 開啟草稿視窗（**不會自動發送**）
5. 告知使用者：「Outlook 草稿已開啟，請確認內容後按發送。」

**注意**：
- 郵件語氣為正式商務風格，主管會看——強調成果與進度，技術細節簡化
- 收件者可用 `--to` 參數覆蓋（如需寄給其他人）
- 不使用 emoji，用「完成」、「進行中」等文字標記

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
