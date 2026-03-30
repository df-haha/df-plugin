---
name: codex-review
description: Codex CLI 二次審查 — 使用 Codex CLI 對程式碼���計畫或技術決策進行獨立審查，並自動進行多輪辯論直到意見收斂。使用時機：當用戶說「codex review」、「跑 codex」、「二次審查」、「codex 審一下」、「讓 codex 看看」、「對審」、「交叉審查」，或提到要用 Codex 檢查程式碼/計畫時觸發。
allowed-tools: Bash, Read, Write, Glob, Grep
---

# Codex CLI 二次審查（多輪對審）

使用 OpenAI Codex CLI 對當前工作進行獨立的二次審查，並自動進行多輪辯論直到雙方意見趨於一致。

## 前置條件

使用前必須確認 Codex CLI 已安裝：

```bash
codex --version
```

如果未安裝，引導使用者執行：

```bash
npm install -g @openai/codex
codex auth   # 登入 OpenAI 帳號
```

安裝完成後才能繼續。

## 三種模式

| 模式 | 說明 | 觸發詞 |
|------|------|--------|
| **code** | 審查未提交的程式碼變更（預設） | 「審查 code」「review 程式碼」 |
| **plan** | 審查實作計畫 | 「審查計畫」「review plan」 |
| **debate** | 針對技術決策進行魔鬼代言人攻防 | 「辯論」「debate」「挑戰這個決策」 |

任何模式都可以加 `--model <name>` 指定模型。**沒指定就不帶 model 參數，使用 Codex 預設模型。**

## 執行流程

### Step 1：確認環境

```bash
codex --version
```

如果失敗，提示使用者安裝：
> 需要先安裝 Codex CLI：`npm install -g @openai/codex`，然後 `codex auth` 登入。

### Step 2：判斷模式與參數

從使用者的自然語言判斷模式：
- 提到「程式碼」「code」「改動」「diff」或沒指定 → **code 模式**
- 提到「計���」「plan」 → **plan 模式**
- 提到「辯論」「debate」「決策」「挑��」 → **debate 模式**

### Step 3：準備與驗證

**code 模式**：
- `git diff --stat` 確認有未提交變更
- 沒有變更 → 告知結束

**plan 模式**���
- 有指定路徑就用
- 沒有 → `ls -t ~/.claude/plans/ | head -1` 找最新 plan
- 找不��� → 告知結束

**debate 模式**：
- 確認有問題文字
- 沒有 → 請使用者提供

### Step 4：執行 Codex 審查

用 Bash 執行（timeout 300000ms）：

```bash
# code 模式
CODEX_MODE=code PROJECT_DIR="$(pwd)" CODEX_MODEL="" node "${CLAUDE_PLUGIN_ROOT}/hooks/codex-review-runner.js"

# plan 模式
CODEX_MODE=plan PROJECT_DIR="$(pwd)" PLAN_PATH="<path>" CODEX_MODEL="" node "${CLAUDE_PLUGIN_ROOT}/hooks/codex-review-runner.js"

# debate 模式
CODEX_MODE=debate PROJECT_DIR="$(pwd)" CODEX_QUESTION="<問題>" CODEX_MODEL="" node "${CLAUDE_PLUGIN_ROOT}/hooks/codex-review-runner.js"
```

> 如果 `CLAUDE_PLUGIN_ROOT` 不可用，runner 路徑 fallback 到 `~/.claude/hooks/codex-review-runner.js`。

從 stdout 找 `SUCCESS: 結果已寫入 <path>`，用 Read 讀取結果。
- `COOLDOWN:` → 告知等待
- `WARNING:` → 告知 Codex 無產出，可能是認證或網路問題

### Step 5：多輪對審（核心流程）

收到 Codex 第一輪結果後，**自動進入多輪辯論循環**：

#### 5a. Claude 逐項回應

對 Codex 的每個意見，逐項評估：
- **合理的 → ✅ 採納**：說明採納理由
- **不合理的 → ❌ 反駁**：給出具體反駁理由，引用��話脈絡或技術事實

關鍵原則：
- Codex 缺少本次對話的決策脈絡和使用者偏好，它的建議可能脫離實際需��
- 衝突時以我們的先前決策為準，並解釋為什麼

#### 5b. 將回應送回 Codex

把 Claude 的逐項回應組裝成文字，用 debate 模式送回 Codex：

```bash
CODEX_MODE=debate PROJECT_DIR="$(pwd)" CODEX_QUESTION="<Claude的回應摘要>" CODEX_MODEL="" node "${CLAUDE_PLUGIN_ROOT}/hooks/codex-review-runner.js"
```

#### 5c. 收斂判斷

- Codex 不再提出新的實質異議 → **結��辯論**
- 雙方對所有項目達成共識 → **結束辯論**
- **最多 3 輪**（避免無限循��和 token ���費）

#### 5d. 殘餘分歧

多輪後仍有無法達成共��的分歧點 → 明確列出，交由使用者最終決斷。

### Step 6：結構化呈現

**code / plan 模���**：

```markdown
## Codex 對審結果 (模式: <mode>, 輪次: N)

### ✅ 共識項目（已採納）
- ...

### ❌ 已反駁（附理由）
- ...

### ⚖️ 分歧待決（需使用者決斷）
- 分歧點：...
  - Claude 觀點：...
  - Codex 觀點：...

### 結論
- ...
```

**debate 模式**：

```markdown
## Codex 魔鬼代言人觀點 (輪次: N)

### 反對理由
- ...

### 潛��風險
- ...

### 替代��案
- ...

### ���衡觀點
- ...

### ⚖️ 未解分歧（需使用者決斷）
- ...
```
