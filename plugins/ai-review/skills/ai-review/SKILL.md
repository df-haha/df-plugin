---
name: ai-review
description: AI 二次審查 - 使用 Codex CLI、Antigravity CLI（agy）或 Claude Code CLI 對程式碼、計畫或技術決策進行獨立審查。
  支援 codex 單審、agy 單審、claude 單審、或 codex+agy 雙重對審。
  觸發詞：「codex review」「agy review」「antigravity review」「claude review」「二次審查」「AI 審查」
  「跑 codex」「跑 agy」「對審」「交叉審查」「讓 codex/agy 看看」
allowed-tools: Bash, Read, Write, Glob, Grep
---

# AI 二次審查（Codex / Antigravity / Claude Code）

使用外部 AI CLI 對當前工作進行獨立二次審查。這個 skill 可同時被 Claude Code 與 Codex 使用：

- Claude Code：可用 `/ai-review ...` slash command，或自然語言觸發本 skill。
- Codex：用 `$ai-review`、`/skills` 選取本 skill，或用自然語言觸發；Codex 不會把 `commands/ai-review.md` 當成 `/ai-review`。

**Reviewer 依宿主選擇**（外審不自審）：

- 宿主是 Claude Code → 預設 `codex`，次選 `agy`；不用 `claude`（同家自審沒有獨立性）。
- 宿主是 Codex → 預設 `claude`，次選 `agy`；不用 `codex`。

## Orca ADE pane 模式（僅 REVIEWER=codex）

若宿主是 Claude Code 且 `$TERM_PROGRAM == "Orca"`（或 `$ORCA_TERMINAL_HANDLE` 非空）、
`command -v orca` 存在、且 `~/.claude/refs/orca-codex-pane.md` 存在：
**REVIEWER=codex 的審查全程改走 Orca pane 模式**（開 pane 跑 codex TUI、輪詢讀畫面、
多輪同 pane 追問、結束記 session id 後關 pane），細節依該 reference 檔執行，
不呼叫 runner。結果仍整理成下方「結構化呈現」格式。

**硬性閘門（hard gate）**：命中即禁止 runner／`codex exec`；開 pane 唯一正解是
`orca terminal split`（同 tab 分割）＋ `--command 'codex "<prompt>"'` 起 TUI——
**不得** `terminal create` 開新 tab 當終端機；開 pane 後 10–15 秒必 read 驗證 prompt
已送出（進 Working），卡在輸入欄就補送空 Enter。三種違規 2026-08-10 都實測踩過。

限制與 fallback：
- 僅適用 REVIEWER=codex；`agy` / `claude` reviewer 及 Codex 宿主一律走原 runner 流程。
- 上述任一條件不成立（含 reference 檔不存在，例如其他使用者的機器）→ 走原 runner 流程，行為不變。
- both 模式在 Orca 下：codex 走 pane，agy 照走 runner。

## 前置條件

至少一個 reviewer CLI 需要安裝並登入：

```bash
codex --version   # Codex CLI
agy --version     # Antigravity CLI
claude --version  # Claude Code CLI（Codex 宿主時的 reviewer）
```

未安裝時引導：

- Codex: `npm install -g @openai/codex && codex auth`
- Antigravity: 依 Antigravity CLI 官方安裝說明；`agy models` 可列出可用模型。
- Claude Code: `npm install -g @anthropic-ai/claude-code` 並完成登入。

## 三種模式

| 模式 | 說明 | 觸發詞 |
|------|------|--------|
| `code` | 審查未提交的程式碼變更（預設） | 「審查 code」「review 程式碼」「diff」 |
| `plan` | 審查實作計畫 | 「審查計畫」「review plan」 |
| `debate` | 針對技術決策進行魔鬼代言人攻防 | 「辯論」「debate」「挑戰這個決策」 |

任何模式都可以加：

- `--model <name>` 指定 reviewer 使用的模型。
- `--effort <level>` 指定 reasoning effort（**僅 codex 生效**，如 `low`/`medium`/`high`/`xhigh`/`max`/`ultra`）。

預設模型：

- Codex：**不帶參數**，由 `~/.codex/config.toml` 的 `model` 與 `model_reasoning_effort` 決定（集中管理，勿在此硬編碼）。
- Antigravity：`3.5-flash`。
- Claude Code：`claude-opus-4-6[1m]`，並固定加 `--effort max`。

Claude 權限模式（僅 `REVIEWER=claude`）：

- 預設：`--permission-mode plan`，避免 reviewer 在審查時直接改檔。
- 可選 opt-in：設定 `AI_REVIEW_CLAUDE_PERMISSION_MODE=bypassPermissions`，runner 會改用 `--permission-mode bypassPermissions`。
- `claude --help` 另有 `--dangerously-skip-permissions`，但不在 runner 預設使用，因為它會跳過所有權限檢查。

## 參數判斷

**模式判斷**：

- 提到「程式碼」「code」「改動」「diff」或沒指定 → `code`
- 提到「計畫」「plan」 → `plan`
- 提到「辯論」「debate」「決策」「挑戰」 → `debate`

**Reviewer 判斷**：

- 提到「codex」→ `REVIEWER=codex`
- 提到「agy」「antigravity」→ `REVIEWER=agy`（舊稱「gemini」runner 會自動映射到 agy）
- 提到「claude」「claude code」→ `REVIEWER=claude`
- 提到「都跑」「雙重」「both」「交叉」→ `REVIEWER=both`（固定展開為 codex + agy；Codex 宿主要雙審請分別跑 claude 與 agy 兩次，不要用 both）
- 預設 → 依宿主：Claude Code 宿主 `codex`、Codex 宿主 `claude`

## 準備與驗證

**code 模式**：

1. 先執行 `git diff --stat` 確認有未提交變更。
2. 沒有變更 → 告知使用者沒有可審查的 working tree diff，結束。

**plan 模式**：

1. 有指定路徑就用指定路徑。
2. 沒指定時，優先找最近的 `docs/superpowers/plans/*.md`，再找 `~/.claude/plans/*`。
3. 找不到 → 請使用者提供 plan 路徑。

**debate 模式**：

1. 確認有問題文字。
2. 沒有 → 請使用者提供要挑戰的技術決策或問題。

## Runner 路徑

優先順序：

1. 如果使用者或環境提供 `AI_REVIEW_RUNNER`，使用該路徑。
2. Claude Code plugin 環境若有 `CLAUDE_PLUGIN_ROOT`，使用 `${CLAUDE_PLUGIN_ROOT}/hooks/ai-review-runner.js`。
3. Codex plugin 環境：從本 skill 的來源路徑回推。若 `SKILL.md` 位於 `<plugin-root>/skills/ai-review/SKILL.md`，runner 就是 `<plugin-root>/hooks/ai-review-runner.js`。

## 執行審查

用 Bash 執行（timeout 600000ms —— Bash tool 同步等待上限；runner 內部預設同為 600s，可用 `AI_REVIEW_TIMEOUT_MS` 覆寫，agy 的 `--print-timeout` 會由同一值換算）。

建議使用 `AI_REVIEW_*` 環境變數；runner 仍相容舊的 `CODEX_*` 變數（`CODEX_MODE`/`CODEX_MODEL`/`CODEX_EFFORT`/`CODEX_QUESTION`）。
`AI_REVIEW_MODEL` / `AI_REVIEW_EFFORT` 空字串時不注入參數，使用上述 provider 預設。`AI_REVIEW_CLAUDE_PERMISSION_MODE` 空字串時使用 `plan`。

**code 模式**：

```bash
AI_REVIEW_MODE=code REVIEWER=codex PROJECT_DIR="$(pwd)" AI_REVIEW_MODEL="" AI_REVIEW_EFFORT="" node "$RUNNER"
```

```bash
AI_REVIEW_MODE=code REVIEWER=agy PROJECT_DIR="$(pwd)" AI_REVIEW_MODEL="" node "$RUNNER"
```

**plan 模式**：

```bash
AI_REVIEW_MODE=plan REVIEWER=codex PROJECT_DIR="$(pwd)" PLAN_PATH="path/to/plan.md" AI_REVIEW_MODEL="" AI_REVIEW_EFFORT="" node "$RUNNER"
```

**debate 模式**：

```bash
AI_REVIEW_MODE=debate REVIEWER=agy PROJECT_DIR="$(pwd)" AI_REVIEW_QUESTION="要辯論的問題" AI_REVIEW_MODEL="" node "$RUNNER"
```

**both 模式**（runner 依序跑 codex + agy）：

```bash
AI_REVIEW_MODE=code REVIEWER=both PROJECT_DIR="$(pwd)" AI_REVIEW_MODEL="" AI_REVIEW_EFFORT="" node "$RUNNER"
```

runner 會輸出一或多行 `SUCCESS: 結果已寫入 <path>`。用 Read 讀取每個結果檔。

- `COOLDOWN:` → 告知等待。
- `WARNING:` → 該 reviewer 執行失敗或無產出（結果檔內有錯誤訊息可讀），可能是參數、認證、模型、權限或逾時問題。
- `ERROR:` → 先修正參數或 runner 路徑，再重跑。

**超長審查（預估 >10 分鐘，如 ultra effort + 大 diff）**：Bash tool 同步等待上限是 600000ms，改用 `run_in_background` 執行同一指令（並設更大的 `AI_REVIEW_TIMEOUT_MS`，如 `1800000`），完成後從 stdout 取結果檔路徑再 Read。

## 多輪對審

收到第一輪結果後，自動進入收斂判斷，不要盲目接受外部 reviewer 的所有建議。

### 1. 逐項回應

對審查意見的每個項目逐項評估：

- 合理 → 採納：說明採納理由與預計修法。
- 不合理 → 反駁：給出具體反駁理由，引用對話脈絡、需求、程式碼或測試事實。

關鍵原則：

- 外部 AI 缺少本次對話的完整決策脈絡和使用者偏好，建議可能脫離實際需求。
- 衝突時以本 session 已確認的需求與技術事實為準，並解釋原因。

### 2. 必要時送回 reviewer

若還有重大分歧，把目前 agent 的逐項回應摘要用 `debate` 模式送回同一 reviewer：

```bash
AI_REVIEW_MODE=debate PROJECT_DIR="$(pwd)" REVIEWER=codex AI_REVIEW_QUESTION="<逐項回應摘要>" AI_REVIEW_MODEL="" AI_REVIEW_EFFORT="" node "$RUNNER"
```

### 3. 收斂條件

- Reviewer 不再提出新的實質異議 → 結束。
- 雙方對所有項目達成共識 → 結束。
- 最多 3 輪，避免無限循環和 token 浪費。

多輪後仍有無法達成共識的分歧點，明確列出，交由使用者最終決斷。

## 結構化呈現

**code / plan 模式（單一 reviewer）**：

```markdown
## AI 對審結果 (<Codex/Antigravity/Claude Code>, 模式: <mode>, 輪次: N)

### 共識項目（已採納）
- ...

### 已反駁（附理由）
- ...

### 分歧待決（需使用者決斷）
- 分歧點：...
  - 目前 agent 觀點：...
  - Reviewer 觀點：...

### 結論
- ...
```

**both 模式**：

```markdown
## AI 雙重對審結果 (Codex + Antigravity, 模式: <mode>)

### 雙方共識（高優先）
- ...

### 僅 Codex 提出
- ...

### 僅 Antigravity 提出
- ...

### 雙方矛盾（需使用者決斷）
- ...

### 比對分析
- 一致意見：...
- 分歧意見：...

### 結論
- ...
```

**debate 模式**：

```markdown
## AI 魔鬼代言人觀點 (<Codex/Antigravity/Claude Code>, 輪次: N)

### 反對理由
- ...

### 潛在風險
- ...

### 替代方案
- ...

### 權衡觀點
- ...

### 未解分歧（需使用者決斷）
- ...
```
