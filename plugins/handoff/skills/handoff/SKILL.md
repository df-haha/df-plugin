---
name: handoff
description: 跨 session 複雜任務交接 — 從本次對話收集脈絡（commit、決策共識、待辦、相關 memory），自動產出結構化 handoff prompt 並落盤到 ~/.claude/handoffs/，落盤後呼叫 Codex CLI 對草稿做最後一道把關（只挑實質影響的問題、不挑刺），print 完整 prompt 讓使用者複製貼到下個 session。觸發時機：使用者說「準備 handoff」「下個 session」「交接」「跨 session」「/handoff」、或對話結束前要把複雜任務（多 Phase、多步驟、含 AI 對審輪次）傳給下一個 session 接手時觸發。簡單接續用 remember:remember；本 skill 用於需要詳細結構化的複雜交接。
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
- **上個 plan mode 通過的 plan**（若本 session 有 `ExitPlanMode` 拍板的 plan markdown，整段保留下來；handoff 常用情境就是「plan mode 通過後跨 session 接手」，plan 是下個 session 的執行藍本）
- **進行中的 TodoList 狀態**（本 session 最後一次 TodoWrite / TaskCreate 的清單，按 `completed` / `in_progress` / `pending` 三類分組；下個 session 才知道接手點在哪）

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

**重要**：模板最後的「開工順序」清單第 1 步**必須**填入本次 handoff md 的具體路徑，讓下個 session 拿到後第一件事就 `rm` 自我清理。

### Step 5：落盤草稿

`Write` 完整 prompt 到 `~/.claude/handoffs/<project-slug>-<YYYYMMDD-HHMM>.md`。先落盤，下一步才能交給 Codex 讀檔審核。

### Step 6：Codex 對審（只挑有實質影響的問題）

把草稿丟給 Codex CLI 做最後一道把關。**目的是抓會誤導下個 session 的問題，不是挑標點**。

**重要工程細節（驗證過會踩雷的）**：
1. `codex exec` 從 Claude Code 的 Bash tool 呼叫時**沒有 TTY** → 必須用 `script -qc "..." /dev/null` 包起來提供假 TTY，否則會無限卡住（log 0 bytes、0% CPU、不報錯）
2. 中文 prompt 可能含 `"` → **不要用 heredoc 內嵌**，先 `Write` 到 temp file 再 `codex exec ... < /tmp/file`，避免 shell quoting 衝突
3. 在非 git 目錄跑要加 `--skip-git-repo-check`（codex 預設要求 trusted dir）

執行步驟：

```bash
# 1) 把 prompt 寫到 temp file（避免 shell quoting）
cat > /tmp/codex-handoff-prompt.txt <<'EOF'
請審查這份 handoff md：<HANDOFF_PATH>

這是要給下個 Claude session 接手複雜任務的交接 prompt。只挑「有實質影響」的問題，不要挑刺。

審核重點（這些是會讓下個 session 走錯方向的）：
1. 「本次目標 / Step A/B/C / Scope」是否清楚、可執行（input/output 路徑明確、不會誤解）
2. 「禁止清單」是否漏了會讓下個 session 重蹈覆轍的關鍵共識（從決策共識段反推）
3. 上下文一致性：commit hash、未 commit 警告、TodoList 接手點 三者之間有無矛盾
4. Plan markdown 與 Step A/B/C 是否邏輯銜接（Step 偏離 Plan 範圍會誤導）
5. 「開工順序」第 1 步的 rm 路徑是否與本 md 檔名一致
6. 推斷不足的段落是否已標 <待補：...>

不要審（這些回報會被忽略）：
- 標點、用字、語氣、排版瑣事
- 個人風格偏好（除非違反既定規則）
- 大幅重寫建議
- 推測未來可能需要但本 session 沒提到的事

輸出格式（嚴格遵守）：
- 每個問題一行，前綴標籤：
  - [BLOCKER] 不修會讓下個 session 走錯方向
  - [IMPORTANT] 建議補強，影響執行品質
  - [NICE] 可選優化（盡量不要報這類）
- 無實質問題時，第一行回「OK: 無實質問題」後即可結束
EOF

# 在 prompt 內把 <HANDOFF_PATH> 取代成實際絕對路徑
sed -i "s|<HANDOFF_PATH>|$HANDOFF_PATH|" /tmp/codex-handoff-prompt.txt

# 2) 用 script 包出假 TTY 跑 codex（依 ~/.codex/config.toml 預設模型/effort，不帶 --model）
script -qc 'codex exec --skip-git-repo-check --sandbox read-only - < /tmp/codex-handoff-prompt.txt' /tmp/codex-handoff-review.log </dev/null

# 3) 剝 ANSI escape codes 取乾淨文字
sed -e 's/\x1b\[[0-9;]*m//g' /tmp/codex-handoff-review.log
```

實測：gpt-5.5 medium 約 5–30 秒、~13k tokens 一次審核完成。

降級規則（任一條件命中就跳 Step 8 不擋流程）：
- `command -v codex` 失敗 → 印 `⚠️ Codex review skipped: codex CLI not found`
- `codex exec` exit code != 0 → 印 `⚠️ Codex review skipped: exit code <N>`
- 整段執行超時 180s → 印 `⚠️ Codex review skipped: timeout` 並 `pkill -f "codex exec"`
- 輸出 0 bytes（typical TTY 卡住症狀）→ 印 `⚠️ Codex review skipped: empty output (TTY hang?)`

### Step 7：套用修正（如有 BLOCKER / IMPORTANT）

解析 Codex 回應：

| Codex 標籤 | 處理方式 |
|------------|----------|
| `OK: 無實質問題` | 不動，直接 Step 8 |
| `[BLOCKER]` | **必修**。Claude 直接 `Edit` 修正 handoff md，修完口頭交代「已依 Codex BLOCKER 修正：<項目>」 |
| `[IMPORTANT]` | 列給使用者，問「要套用嗎？(y/n/挑選)」。預設不自動套，因為可能與 session 內未明說的脈絡衝突 |
| `[NICE]` | **直接忽略**，不顯示給使用者，避免噪音 |

修正策略細節：
- 補資訊類（漏列禁止項、路徑寫錯、rm 路徑不一致）→ Claude 直接編輯
- 結構性問題（Plan 與 Step 矛盾、Scope 衝突）→ 列給使用者拍板再改，不擅自重構
- 修正完重 `Read` 確認，再進 Step 8

### Step 8：Print + 完成通知

1. **將最終（可能已套用 Codex 修正）的完整 prompt 內容直接 print 到對話**（不是檔案路徑，是整段 prompt）
2. 結尾告知使用者：

```
✅ Handoff 已落盤：~/.claude/handoffs/<檔名>.md
🔍 Codex 審核：<無實質問題 / 已套用 N 項 BLOCKER 修正 / IMPORTANT 待你決定 / skipped: 原因>

📋 複製上方完整 prompt（從「你好！上個 session...」到最末），貼到下個 session 的第一句話即可。
新 session 會依 prompt 內含的「開工順序」第 1 步自動 rm 這份 md，不需手動清。
```

---

## Handoff Prompt 模板

> 以下是 print 給使用者的內容。每個 `<...>` 變數由 skill 即時推斷填入；固定段落（H2/H3 結構、Plan 區塊、TodoList 區塊、執行風格、開工順序）**不可省略**。

```markdown
你好！上個 session（commit <hash>）完成了 <Phase/Task 名稱>。
<一段話交代決策共識，特別是與 Codex / 其他 AI 對審後的結論；若無對審則交代主要決策邏輯>

## 已落盤狀態（commit <hash>）
- <artifact 路徑 1>（<簡述用途>）
- <artifact 路徑 2>（<簡述用途>）
- <測試/驗證狀態，例如「12 tests passing」或「無自動測試，已手動驗證 X」>
<若有未 commit 變更，加：⚠️ 含未 commit 變更：<檔案>>

## 上個 session 拍板的 Plan
> 來自上個 session `ExitPlanMode` 通過的 plan，是本次任務的執行藍本。若無則填「（本 session 未經 plan mode）」。

<完整 plan markdown 內容；保留原 H2/H3 結構，不重寫>

## TodoList 接手狀態
> 來自上個 session 最後一次 TodoWrite。下個 session 開工前先 `TodoWrite` 把下表 pending / in_progress 重建回 task list。

**已完成**（已驗證，列出供脈絡）
- [x] <completed task 1>
- [x] <completed task 2>

**進行中**（接手點，需先確認進度）
- [/] <in_progress task>（<目前卡在哪 / 下一步要做什麼>）

**待辦**（本次 session 要消化的）
- [ ] <pending task 1>
- [ ] <pending task 2>

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

### 開工順序（依序執行，不要跳過）

1. **🧹 清理本份 handoff md**（避免 ~/.claude/handoffs/ 堆積）：
   ```bash
   rm ~/.claude/handoffs/<本次 handoff md 完整檔名>.md
   ```
2. **重建 TodoList**：用 `TodoWrite` 把上方「TodoList 接手狀態」的 in_progress + pending 重建回 task list（completed 不重建，只是脈絡參照）。
3. **讀 input artifact schema**：`head -3 <input artifact 路徑>` + 欄位清單，確認與 <下游需求> 對接無遺漏。
4. 開始 Step A。
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
| `<plan markdown>` | 本 session 最近一次 `ExitPlanMode` 通過的 plan 內容；無則填「（本 session 未經 plan mode）」 |
| `<TodoList completed/in_progress/pending>` | 本 session 最後一次 TodoWrite/TaskCreate 的 todo 清單，按狀態分組 |

## 推斷不足時的處理

若對話中**無明確的下次目標** / Step A/B/C 無法填：

- **不要瞎填**。在對應段落寫 `<待補：使用者請在貼到新 session 前手動補上 Step A 的具體輸入/輸出>`
- 在 Step 5 print 結尾額外提醒使用者：「⚠️ 偵測到 <Step A/Scope/...> 推斷不足，請手動補上再貼到新 session」

## 與其他 skill 的關係

- **不取代 `remember:remember`**：簡單「明天繼續寫測試」用 remember；複雜「下次要處理 Codex R1 共識 + Step A 改檔 X + 禁止 Y」才用 handoff
- **不自動清理舊 handoff**：清理責任在「下個 session 貼 prompt 時執行 rm」。若使用者想統一清，可手動 `ls ~/.claude/handoffs/` 檢視
- **不寫到 /mnt/c/**：嚴格遵守 user CLAUDE.md 紀律，handoff md 一律放 `~/.claude/handoffs/`
