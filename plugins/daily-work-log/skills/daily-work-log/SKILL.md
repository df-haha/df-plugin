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

### Phase 1：Session 掃描 + 成本數據

執行 Python 腳本掃描所有專案的 session 檔案，同時抓取 ccusage 成本數據：

```bash
python3 ${CLAUDE_PLUGIN_ROOT}/scripts/daily_work_log.py {YYYY-MM-DD|today} --with-cost
```

腳本會：
- 掃描 `~/.claude/projects/*/` 下所有 `.jsonl` 檔
- 依檔案建立/修改時間篩選目標日期
- 解析每個 session 的訊息數、時間範圍、首則用戶訊息
- `--with-cost`：呼叫 `ccusage` CLI 取得當日 token 用量與成本（按專案分解 + 模型分解）
- 輸出 JSON 摘要（stdout），含 `cost` 欄位

### Phase 2：深度分析（可選）

如果 claude-mem MCP 可用，用 timeline 補充摘要：

```
mcp__plugin_claude-mem_mcp-search__timeline(date="{YYYY-MM-DD}")
```

這能取得當天已結構化的 observations，比原始 JSONL 更精煉。

若 claude-mem 不可用或資料不足，直接從 Phase 1 的 JSON 中的 `topic_hints`（用戶前幾句話）推斷工作主題。

### Phase 3：產出日誌 Markdown

輸出路徑：`daily_proposal/daily_work_log_{YYYY-MM-DD}.md`

#### 日誌結構模板

```markdown
# {YYYY-MM-DD} 工作日誌

> 產出時間：{now}
> 來源：Claude Code session 分析 + ccusage
> 統計：{N} sessions ｜ {M} 專案 ｜ {earliest} → {latest}
> 成本：${total_cost} （Opus: ${opus_cost} ｜ Sonnet: ${sonnet_cost} ｜ Haiku: ${haiku_cost}）

---

## 一、{專案名}（{session_count} sessions ｜ ${cost}）

### {工作主題}
- 具體工作項目 1
- 具體工作項目 2

## 二、{專案名}（{session_count} sessions ｜ ${cost}）
...

---

## Token 用量與成本

### 專案成本分解

| 專案 | Sessions | 成本 | 主要模型 |
|------|----------|------|--------|
| {專案名} | {N} | ${cost} | {models} |
| Subagents | {N} | ${cost} | mixed |
| **合計** | | **${total}** | |

### 模型用量分解

| 模型 | Input | Output | Cache Write | Cache Read | 成本 |
|------|-------|--------|-------------|------------|------|
| claude-opus-4-6 | {n} | {n} | {n} | {n} | ${cost} |
| claude-sonnet-4-6 | {n} | {n} | {n} | {n} | ${cost} |
| claude-haiku-4-5 | {n} | {n} | {n} | {n} | ${cost} |

> Token 數以 K（千）或 M（百萬）為單位顯示。成本來自 ccusage CLI。

---

## 產出清單

| # | 產出項目 | 狀態 |
|---|---------|------|
| 1 | ... | 完成 |

---

## 工作主題分類

1. **主題名稱**（專案名）：簡述
```

### Phase 4：呈現結果

1. 將產出的 markdown 路徑告知用戶
2. 在終端印出重點摘要（不超過 20 行）
3. 如果用戶要求，可以直接在終端印出完整日誌

### Phase 5：確認與寄信

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
   - 收件者：haha.huang@df-recycle.com.tw
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
- 不要捏造工作內容——只報告從 session 中實際觀察到的
- 若 JSONL 解析不出有意義的資訊，據實回報而非填充
