---
name: handoff
description: 跨 session 複雜任務交接 — 從本次對話收集脈絡（commit、決策共識、待辦、相關 memory），自動產出結構化 handoff prompt 並落盤到 ~/.claude/handoffs/，print 完整 prompt 讓使用者複製貼到下個 session。觸發時機：使用者說「準備 handoff」「下個 session」「交接」「跨 session」「/handoff」、或對話結束前要把複雜任務（多 Phase、多步驟、含 AI 對審輪次）傳給下一個 session 接手時觸發。簡單接續用 remember:remember；本 skill 用於需要詳細結構化的複雜交接。
allowed-tools: Bash, Read, Write, Glob, Grep
---

# 跨 Session 任務交接（Handoff）

## 目的

在當前 session 結束前，把「複雜的多步驟任務脈絡」打包成一份結構化 prompt，讓下個 session 無歧義接續。

## 設計前提

- **本 skill 只在當前 session 結束前被呼叫一次**。新 session 不會再跑此 skill，會直接貼 prompt。
- 因此**不做啟動清理檢查**——「刪除舊 handoff md」的指令**內嵌在生成的 prompt 裡**，由下個 session 自己負責清理。

## 執行流程

### Step 1：收集當前 session 脈絡

並行（同一 message 多個 Bash tool call）執行：

1. `git log -1 --oneline 2>/dev/null` — 取最近 commit hash + 訊息
2. `git diff --stat HEAD 2>/dev/null` — 看是否有未 commit 變更
3. `git status --short 2>/dev/null` — 看 untracked / staged 狀態
4. `pwd` — 推斷專案 slug（取 basename）
5. `date +"%Y%m%d-%H%M"` — handoff md 時間戳
6. `ls ~/.claude/projects/-home-haha-CC-project/memory/ 2>/dev/null | head -20` — 列出可參考的 memory 檔
7. `ls -t ~/.claude/handoffs/ 2>/dev/null | head -5` — 看現有 handoff 累積狀況

從**對話歷史**整理（不需 tool call，直接從 context 推斷）：

- 本 session 完成了什麼（commit / artifact / 決策）
- 達成的決策共識（特別是 Codex 對審後的結論）
- 明確的待辦事項（使用者下次想做的 Step A/B/C）
- 已浮現的禁止清單（對話中明確的「不要 X」）

### Step 2：未 commit 變更提醒

若 `git status` 顯示有未 commit 的修改，**先告知使用者**：

> ⚠️ 偵測到未 commit 變更：<檔案清單>。建議先 commit 再 handoff，否則下個 session 拿到的 commit hash 與工作狀態會不一致。
>
> 要繼續產 handoff 嗎？（y / 先 commit）

若使用者說繼續，handoff prompt 內 commit hash 段落要明確標注「⚠️ 含未 commit 變更：<檔案>」。

### Step 3：決定 handoff md 路徑

```
~/.claude/handoffs/<project-slug>-<YYYYMMDD-HHMM>.md
```

- `<project-slug>` = `pwd` 的 basename（小寫、空格換 `-`）
- 例：`~/.claude/handoffs/CC_project-20260425-2305.md`

確認 `~/.claude/handoffs/` 目錄存在（不存在就 `mkdir -p`）。

### Step 4：套用模板生成 handoff prompt

使用下方「Handoff Prompt 模板」，把 Step 1 收集的脈絡填進去。

**重要**：模板最後的「🧹 開工前清理」段落**必須**填入本次 handoff md 的具體路徑，讓下個 session 拿到後執行 `rm` 自我清理。

### Step 5：落盤 + Print

1. `Write` 完整 prompt 到 `~/.claude/handoffs/<project-slug>-<YYYYMMDD-HHMM>.md`
2. **將完整 prompt 內容直接 print 到對話**（不是檔案路徑，是整段 prompt）
3. 結尾告知使用者：

```
✅ Handoff 已落盤：~/.claude/handoffs/<檔名>.md

📋 複製上方完整 prompt（從「你好！上個 session...」到最末），貼到下個 session 的第一句話即可。
新 session 會依 prompt 內含的 🧹 清理段落自動 rm 這份 md，不需手動清。
```

---

## Handoff Prompt 模板

> 以下是 print 給使用者的內容。每個 `<...>` 變數由 skill 即時推斷填入；固定段落（H2/H3 結構、執行風格、🧹 清理段落）**不可省略**。

```markdown
你好！上個 session（commit <hash>）完成了 <Phase/Task 名稱>。
<一段話交代決策共識，特別是與 Codex / 其他 AI 對審後的結論；若無對審則交代主要決策邏輯>

## 已落盤狀態（commit <hash>）
- <artifact 路徑 1>（<簡述用途>）
- <artifact 路徑 2>（<簡述用途>）
- <測試/驗證狀態，例如「12 tests passing」或「無自動測試，已手動驗證 X」>
<若有未 commit 變更，加：⚠️ 含未 commit 變更：<檔案>>

## 本次目標：<Phase/Task 名稱>

### Scope（必做）
<明確的 input artifact 路徑 + 範圍說明，例如：>
- 讀 `<input 路徑>` 的 schema，產出 `<output 路徑>`
- 涵蓋 <具體欄位 / 具體場景>

### 步驟（建議用 TDD + Codex 對審）
1. **Step A：<名稱> (TDD)**
   - 新檔/改檔：<具體路徑>
   - 邏輯：<具體說明>
   - 輸出：<具體路徑>
2. **Step B：<名稱>**
   - <具體說明>
3. **Step C：<名稱>**
   - <具體說明>
4. **Codex 對審（最多 3 輪）**
   - R1：<具體要 Codex 檢查的問題>
   - R2：<具體要 Codex 檢查的問題>
   - R3：<具體要 Codex 檢查的問題>
5. **Commit + 更新 memory**
   - commit message 格式：<type>: <description>
   - 觸發條件：<什麼時候要寫 RFC / 升 Phase / 更新 spec>

### 禁止
- 不要 <事項 1，從本 session 對話共識提煉>
- 不要 <事項 2>
- 不要 <事項 3>

### 參考 memory
- `<memory_file_1>.md`（<說明為何相關>）
- `<memory_file_2>.md`（<說明為何相關>）

### 執行風格
- TDD 紀律：先紅再綠，每個 Step 獨立驗證
- /commit 用 commit-commands:commit skill
- Codex 對審用 codex-review skill
- 每完成一步 update TaskList
- 不確定 API 用法先 context7 查文件再寫

請先讀 `<input artifact 路徑>` 的 schema（`head -3` + 欄位清單），確認與 <下游需求> 對接無遺漏，再開始 Step A。

---

### 🧹 開工前清理

這份 handoff 來自上個 session，**請在開始 Step A 前刪除以下檔案**（避免 ~/.claude/handoffs/ 堆積）：

```bash
rm ~/.claude/handoffs/<本次 handoff md 完整檔名>.md
```

刪除後再開始任務。
```

## 變數推斷指引

| 變數 | 推斷來源 |
|------|----------|
| `<hash>` | `git log -1 --oneline` |
| `<Phase/Task 名稱>` | 對話最後幾則訊息中提到的 Phase / 任務名 |
| `<決策共識段>` | Codex 對審結論 / 使用者拍板的設計決策 |
| `<artifact 路徑>` | git diff / 對話中明確產出的檔案 |
| `<input/output 路徑>` | 使用者明確提到的下次任務輸入/輸出 |
| `<Step A/B/C>` | 使用者本來打算下次做什麼；若不明確就保留為「<待補>」並提醒使用者補充 |
| `<禁止 1/2/3>` | Codex debate 共識 + 對話中明確的「不要 X」「禁止 X」 |
| `<參考 memory>` | `ls ~/.claude/projects/-home-haha-CC-project/memory/` 中與本任務相關的檔 |
| `<本次 handoff md 完整檔名>` | Step 3 決定的路徑 basename |

## 推斷不足時的處理

若對話中**無明確的下次目標** / Step A/B/C 無法填：

- **不要瞎填**。在對應段落寫 `<待補：使用者請在貼到新 session 前手動補上 Step A 的具體輸入/輸出>`
- 在 Step 5 print 結尾額外提醒使用者：「⚠️ 偵測到 <Step A/Scope/...> 推斷不足，請手動補上再貼到新 session」

## 與其他 skill 的關係

- **不取代 `remember:remember`**：簡單「明天繼續寫測試」用 remember；複雜「下次要處理 Codex R1 共識 + Step A 改檔 X + 禁止 Y」才用 handoff
- **不自動清理舊 handoff**：清理責任在「下個 session 貼 prompt 時執行 rm」。若使用者想統一清，可手動 `ls ~/.claude/handoffs/` 檢視
- **不寫到 /mnt/c/**：嚴格遵守 user CLAUDE.md 紀律，handoff md 一律放 `~/.claude/handoffs/`
