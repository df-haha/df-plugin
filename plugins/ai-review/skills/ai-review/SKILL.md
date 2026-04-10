---
name: ai-review
description: AI 二次審查 — 使用 Codex CLI 或 Gemini CLI 對程式碼、計畫或技術決策進行獨立審查。
  支援 codex 單審、gemini 單審、或 codex+gemini 雙重對審。
  觸發詞：「codex review」「gemini review」「二次審查」「AI 審查」
  「跑 codex」「跑 gemini」「對審」「交叉審查」「讓 codex/gemini 看看」
allowed-tools: Bash, Read, Write, Glob, Grep
---

# AI 二次審查（多 Provider 對審）

使用 Codex CLI 或 Gemini CLI 對當前工作進行獨立的二次審查，支援單一 provider 或雙重對審模式。

## 前置條件

至少一個 AI CLI 需要安裝：

```bash
codex --version   # Codex CLI
gemini --version  # Gemini CLI
```

未安裝時引導：
- Codex: `npm install -g @openai/codex && codex auth`
- Gemini: `npm install -g @google/gemini-cli && gemini auth`

## 三種模式

| 模式 | 說明 | 觸發詞 |
|------|------|--------|
| **code** | 審查未提交的程式碼變更（預設） | 「審查 code」「review 程式碼」 |
| **plan** | 審查實作計畫 | 「審查計畫」「review plan」 |
| **debate** | 針對技術決策進行魔鬼代言人攻防 | 「辯論」「debate」「挑戰這個決策」 |

任何模式都可以加 `--model <name>` 指定模型。**沒指定就不帶 model 參數，使用各 CLI 預設模型。**

## 執行流程

### Step 1：確認環境

```bash
codex --version 2>/dev/null
gemini --version 2>/dev/null
```

至少一個要成功。都失敗 → 提示使用者安裝。

### Step 2：判斷模式、審查者與參數

**模式判斷**（從使用者的自然語言）：
- 提到「程式碼」「code」「改動」「diff」或沒指定 → **code 模式**
- 提到「計畫」「plan」 → **plan 模式**
- 提到「辯論」「debate」「決策」「挑戰」 → **debate 模式**

**審查者選擇**：
- 提到「codex」→ `REVIEWER=codex`
- 提到「gemini」→ `REVIEWER=gemini`
- 提到「都跑」「雙重」「both」「交叉」→ **both 模式**（兩個都跑）
- 預設 → `REVIEWER=codex`

### Step 3：準備與驗證

**code 模式**：
- `git diff --stat` 確認有未提交變更
- 沒有變更 → 告知結束

**plan 模式**：
- 有指定路徑就用
- 沒有 → `ls -t ~/.claude/plans/ | head -1` 找最新 plan
- 找不到 → 告知結束

**debate 模式**：
- 確認有問題文字
- 沒有 → 請使用者提供

### Step 4：執行審查

用 Bash 執行（timeout 300000ms）：

**單一 provider**：
```bash
CODEX_MODE=code REVIEWER=codex PROJECT_DIR="$(pwd)" CODEX_MODEL="" node "${CLAUDE_PLUGIN_ROOT}/hooks/ai-review-runner.js"
```

```bash
CODEX_MODE=code REVIEWER=gemini PROJECT_DIR="$(pwd)" CODEX_MODEL="" node "${CLAUDE_PLUGIN_ROOT}/hooks/ai-review-runner.js"
```

**both 模式**（依序執行兩個 provider）：
1. `REVIEWER=codex` → 取得 codex output
2. `REVIEWER=gemini` → 取得 gemini output
3. Claude 比對兩份 output，彙整為統一報告

> 如果 `CLAUDE_PLUGIN_ROOT` 不可用，runner 路徑 fallback 到 `~/.claude/hooks/ai-review-runner.js`。

從 stdout 找 `SUCCESS: 結果已寫入 <path>`，用 Read 讀取結果。
- `COOLDOWN:` → 告知等待
- `WARNING:` → 告知該 provider 無產出，可能是認證或網路問題

### Step 5：多輪對審（核心流程）

收到第一輪結果後，**自動進入多輪辯論循環**：

#### 5a. Claude 逐項回應

對審查意見的每個項目，逐項評估：
- **合理的 → ✅ 採納**：說明採納理由
- **不合理的 → ❌ 反駁**：給出具體反駁理由，引用對話脈絡或技術事實

關鍵原則：
- 外部 AI 缺少本次對話的決策脈絡和使用者偏好，建議可能脫離實際需求
- 衝突時以我們的先前決策為準，並解釋為什麼

#### 5b. 將回應送回 reviewer

把 Claude 的逐項回應組裝成文字，用 debate 模式送回：

```bash
CODEX_MODE=debate PROJECT_DIR="$(pwd)" REVIEWER=<provider> CODEX_QUESTION="<Claude的回應摘要>" CODEX_MODEL="" node "${CLAUDE_PLUGIN_ROOT}/hooks/ai-review-runner.js"
```

#### 5c. 收斂判斷

- Reviewer 不再提出新的實質異議 → **結束辯論**
- 雙方對所有項目達成共識 → **結束辯論**
- **最多 3 輪**（避免無限循環和 token 浪費）

#### 5d. 殘餘分歧

多輪後仍有無法達成共識的分歧點 → 明確列出，交由使用者最終決斷。

#### 5e. both 模式特殊處理

1. 分別收集 Codex 和 Gemini 的審查結果
2. Claude 比對兩份結果，整理出：
   - 兩邊都提到的共識問題（高優先）
   - 只有一邊提到的問題（需判斷是否合理）
   - 兩邊矛盾的建議（交由使用者決斷）
3. 如果是 debate 模式：Claude 的彙整可選擇送回雙方進行多輪收斂

### Step 6：結構化呈現

**code / plan 模式（單一 provider）**：

```markdown
## AI 對審結果 (Codex/Gemini, 模式: <mode>, 輪次: N)

### ✅ 共識項目（已採納）
- ...

### ❌ 已反駁（附理由）
- ...

### ⚖️ 分歧待決（需使用者決斷）
- 分歧點：...
  - Claude 觀點：...
  - Codex/Gemini 觀點：...

### 結論
- ...
```

**code / plan 模式（both）**：

```markdown
## AI 雙重對審結果 (Codex + Gemini, 模式: <mode>)

### ✅ 雙方共識（高優先）
- ...

### 🔵 僅 Codex 提出
- ...

### 🟢 僅 Gemini 提出
- ...

### ⚖️ 雙方矛盾（需使用者決斷）
- ...

### 比對分析
- 一致意見：...
- 分歧意見：...

### 結論
- ...
```

**debate 模式**：

```markdown
## AI 魔鬼代言人觀點 (Codex/Gemini, 輪次: N)

### 反對理由
- ...

### 潛在風險
- ...

### 替代方案
- ...

### 權衡觀點
- ...

### ⚖️ 未解分歧（需使用者決斷）
- ...
```
