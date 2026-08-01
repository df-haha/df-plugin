---
name: codex-image
description: 用 Codex CLI 內建生圖功能（image_generation feature flag，stable）產出 PNG 圖檔，透過 codex 帳號 token 計費，不需要額外的 OpenAI / Stability API key。觸發時機：當使用者說「生圖」「畫一張」「畫個」「生成圖片」「來張圖」「make image」「generate image」「draw me」「create a picture / illustration / icon / logo / banner / cover」、或描述想要的視覺內容（不論明不明確說要「圖」）時觸發。即使使用者只是隨口說「弄一張 X 的圖給我看」也應觸發。
allowed-tools: Bash, Read
---

# Codex Image — 用 Codex CLI 生成 PNG 圖片

透過 Codex CLI 的 `image_generation` feature flag（穩定功能旗標）呼叫內建 `image_gen` tool 生成 PNG。不需要額外 API key——計入 Codex 帳號一般額度。

## Plugin root（插件根目錄）解析

宿主不同，plugin root 解析方式也不同：

- **Claude Code**：使用 `${CLAUDE_PLUGIN_ROOT}` 環境變數。
- **Codex**：無此變數。從本 SKILL.md 所在路徑往上走兩層（`skills/codex-image/SKILL.md` → plugin root），確認其下有 `lib/` 目錄。

後續所有 `node <plugin>/lib/...` 呼叫，`<plugin>` 皆指上述解析出的 plugin root。

---

## 1. 兩層模型架構（Two-layer model architecture）

| 層級 | 模型 | 角色 |
|------|------|------|
| **調度層（dispatch LLM）** | 由使用者的 Codex config（設定檔）決定；setup skill 偵測後記錄為 `detected_dispatch_model` | 解讀使用者 prompt（提示詞），決定呼叫什麼 tool、帶什麼參數 |
| **生圖層（image model）** | 目前內建預設為 `gpt-image-2` | 實際生成圖片，不受 `--model` 參數影響 |

`--model` 只換調度層 LLM，不換生圖模型。更換生圖模型（例如使用 `gpt-image-1.5`）只能透過 CLI fallback（降級路徑）。

---

## 2. 不適用情境（When NOT to use）

以下情境不應使用本 skill，直接用 SVG / HTML / CSS 製作更合適：

- 簡單幾何圖形（圓、矩形、箭頭）
- 圖表（chart）、流程圖（diagram）、架構圖
- Wireframe（線框稿）
- 已有 SVG icon system（圖示系統）的專案需要新增同風格 icon

---

## 3. 參數可靠度分級（Parameter reliability tiers）

內建 `image_gen` tool 與 CLI fallback 的參數控制力截然不同。

| 分級 | 適用路徑 | 參數 | 說明 |
|------|---------|------|------|
| **Tier 1：Intent-only（意圖式）** | 內建 `image_gen` | `size`、`quality` | 以自然語言（natural language）在 prompt 中表達意圖（如 `Render at high quality if your image tool exposes a quality setting.`），但**不保證**調度層 LLM 會正確傳遞。生成後**必須驗證** PNG 的實際 IHDR（PNG 標頭）尺寸。引用：`references/prompting.md:84-86` — "Do not assume they are built-in `image_gen` tool arguments." |
| **Tier 2：Multi-variant（多變體）** | 內建 `image_gen` | 多張圖片 | 每個變體一次 `image_gen` 呼叫；不使用 `n` 參數。不同構圖需要不同 prompt。 |
| **Tier 3：CLI/API fallback** | CLI `scripts/image_gen.py` | `size`、`quality`、`n`、`moderation`、`output_format`、mask | **唯一能硬性控制**這些參數的路徑。需要 `OPENAI_API_KEY` 且 Codex config 須開啟 `network_access`。 |

### 負面提示的分界

| 對象 | 寫法 |
|------|------|
| **圖像內容**（畫面裡有什麼） | 只用正向白名單（positive whitelist）。禁止「不要畫 X」——命名禁忌物件反而誘發它出現。 |
| **調度層執行紀律**（codex 該怎麼做事） | 可用且必須用禁令。這段不進入圖像描述，是給 LLM 的執行約束。 |

### Known uncertainty: quality

內建路徑中，`quality` 只能以自然語言提示（`Render at high quality if your image tool exposes a quality setting.`）表達，無法直接傳入 `image_gen` tool 的參數清單。這與 CLI fallback 路徑（`--quality high` 硬性控制）之間存在張力，目前尚未解決。

**不要**寫成假參數清單（如 `- quality: high`）——那暗示它是 tool argument。

`moderation` 同樣無法在內建路徑透過 prompt 可靠控制。需要精確控制 `moderation` 時，必須走 CLI fallback（`fallback-only`）。

---

## 4. 硬規則：禁止 code-drawing（2026-07-08 事故教訓）

**失敗模式**：調度層 LLM 收到「文字必須逐字正確」之類的壓力措辭時，會放棄 `image_gen` 的產出，改用 PIL / matplotlib / SVG / HTML 截圖等 code 重繪整張圖——產出變成乾淨但廉價的點線圖。**這不是生圖，是畫圖**，違背本 skill 的目的。

**Prompt 必含以下三條反 code-drawing 條款**（全部強制）：

```
You MUST call the image_gen tool to produce the image.
You MUST NOT write or execute any code that draws, composes, edits, or re-renders the image (no PIL, no matplotlib, no SVG, no HTML/screenshot rendering, no font re-typesetting).
Deliver the image_gen output file AS-IS. If some text in the generated image is imperfect, do NOT fix it with code — just save the raw generated image and mention the imperfection.
```

文字需求寫成 `include the following text: "..."` 即可。**禁止**使用壓力措辭（`character for character`、`exactly as written`、`必須完全正確`）——這是誘發 code-drawing 的直接誘因。錯字透過**重新生成**修正，永遠不以 code 修補。

詳見 `references/text-and-cjk.md`。

---

## 5. 安全呼叫方式（Safe invocation）

**必須透過 `lib/generate.mjs` 這個 entry point（進入點）執行整條流程。** 直接在 shell 中拼接使用者文字是嚴格禁止的。

`lib/generate.mjs` 從 **stdin 讀取一份 JSON job spec（工作規格）**，跑完「呼叫 → 驗收 → 複製」全流程後，把一份 JSON 結果印到 stdout。使用者的圖像描述**全程只是 JSON 字串值**，不曾出現在任何命令列上——`$()`、反引號、引號、換行都是惰性資料。

依 repo shell 規則（避免 inline heredoc 跳脫），**先把 job 寫成檔案再 pipe（管道）進去**：

```bash
# 1) 用 Write tool 產生 job 檔（不要用 echo / heredoc 拼字串）
#    <staging>/job.json
# 2) 執行
node <plugin>/lib/generate.mjs < <staging>/job.json
```

Job spec 欄位：

```json
{
  "prompt": "<組合好的完整 prompt，見 §9>",
  "outputDir": "<config 的 default_output_dir 或使用者指定>",
  "filename": "pear.png",
  "denyPaths": ["<來自 config 的 deny_write_paths>"],
  "timeoutMs": 240000
}
```

結果 JSON 含 `ok`、`checks`（§7 六項）、`output`（最終絕對路徑）、`size`（實際 IHDR 尺寸）、`bytes`、`notes`、`stderrTail`。exit code 為 `0` 代表全部硬性檢查通過。

**目的地在花掉任何額度之前就先驗證**——deny list 命中或檔名穿越會在呼叫 Codex 前直接拒絕。

底層由 `lib/run.mjs` 的 `runCodex()` 負責實際 spawn：

```js
runCodex({ codexBin, prompt, stagingDir, timeoutMs })
// → { exitCode, stdout, stderr, timedOut }
```

每個 Codex CLI flag 的用途：

| Flag | 用途 |
|------|------|
| `--json` | 輸出結構化 JSONL 事件（而非 prose），供 `lib/parse-events.mjs` 解析 |
| `--ephemeral` | 不留下 session rollout（一次性執行、不佔用 session 紀錄） |
| `--skip-git-repo-check` | 避免工作目錄不是 git repo 時報錯 |
| `--sandbox workspace-write` | 允許 codex 寫入工作目錄（預設 read-only 會導致寫不出檔案） |
| `-C <stagingDir>` | 指定 codex 的工作根目錄為 staging dir（暫存目錄） |

**Prompt 傳遞方式**：透過 stdin pipe，**不**插入 shell command string（命令字串）中。`lib/run.mjs` 的 `shell:false` 確保不經 shell 展開。

---

## 6. Staging → 驗證 → 複製（Staging → verify → copy）

所有生成都進入隔離的 staging dir，通過驗收後才複製到目標路徑。

這四步由 `lib/generate.mjs` 一次跑完，不需要手動編排：

1. **驗證目的地**：`lib/safe-path.mjs` 的 `sanitizeFilename()` + `resolveOutputPath()`，在呼叫 Codex **之前**擋掉檔名穿越與 deny list 命中
2. **建立 staging dir** 並呼叫 Codex CLI（透過 `lib/run.mjs`）
3. **驗收**（見 §7）
4. **複製到目標**：通過驗收後，使用 `lib/safe-path.mjs` 的 `nonDestructivePath()` 確保不覆蓋既有檔案

預設輸出目錄是**專案內** `./assets/generated/`（不是 `~/Pictures`），避免 workspace-outside 的權限確認提示。此預設可在 config（設定檔）中透過 `default_output_dir` 覆寫。

---

## 7. 驗收證據（Acceptance checks）

生成完成後，依序執行六項檢查：

| # | 檢查項目 | 方法 | 判定 |
|---|---------|------|------|
| 1 | Exit code（結束碼） | `runCodex()` 回傳的 `exitCode` | 必須為 `0` |
| 2 | JSONL 含 `turn.completed` | `lib/parse-events.mjs` 的 `hasTurnCompleted()` | 必須為 `true` |
| 3 | **新建立**的 PNG 存在 | 比對 creation time（建立時間），非僅檔名 | staging dir 內有新 PNG |
| 4 | 實際 PNG IHDR 尺寸 | `lib/parse-events.mjs` 的 `readPngSizeFromFile()` 讀取 IHDR chunk | 回報實際 `WxH` |
| 5 | 檔案大小啟發式 | 真 `gpt-image-2` 輸出通常約 1.0–1.5 MB | <300 KB **可能**是 PIL fallback 的訊號——這是**軟性指標**（soft signal），閾值隨 size 和 quality 設定而漂移，不是硬性標準 |
| 6 | Code-drawing 檢查 | `lib/parse-events.mjs` 的 `detectCodeDrawing()` 解析 JSONL 中的 `command_execution` / tool events | **誠實標注為啟發式**（heuristic）——除非 JSONL 明確暴露 `image_gen` tool call，否則無法硬性證明 |

**code-drawing 檢查的重要說明**：解析對象是 JSONL 中的結構化事件（`command_execution`、tool call），**不是** grep prompt 或自然語言 messages。這使偵測具有合理信心，但仍是啟發式——JSONL 未暴露 `image_gen` call 時無法確定性證明。

---

## 8. 紅線：不自動降級

偵測到可能 fallback（降級）到 PIL / code-drawing 時，**立即停下回報**。不默默生成一張 PIL 圖片湊數。

失敗時詢問使用者：
- 重試（加強反 code-drawing 條款）
- 修改 prompt
- 改用其他工具

使用宿主可用的互動機制詢問並等待明確同意。

---

## 9. Prompt 組合

### 外層指示（wrapper instructions）——用英文

```text
Generate an image using the built-in image_gen tool.
Render at high quality if your image tool exposes a quality setting.
<size 指示，若有>

Image description:
<使用者的視覺描述——中英皆可>

HARD RULES:
- You MUST call the image_gen tool to produce the image.
- You MUST NOT write or execute any code that draws, composes, edits, or re-renders the image (no PIL, no matplotlib, no SVG, no HTML/screenshot rendering, no font re-typesetting).
- Deliver the image_gen output file AS-IS. If some text in the generated image is imperfect, do NOT fix it with code — just save the raw generated image and mention the imperfection.

Save the generated image as <filename>.png in the current directory (plain cp only).
```

外層指示用英文確保調度層 LLM 正確解析；使用者的視覺描述本身中英皆可。不要強制要求 prompt 必須英文。

### Size 指示

使用者指定尺寸時，加一句：`Target size: <W>x<H>.`。內建路徑下這只是 intent（意圖），不保證實際尺寸——驗收時以 IHDR 為準。

### Quality 表達

使用 `Render at high quality if your image tool exposes a quality setting.` 這個句式。不要寫成假參數清單。

---

## 10. 設定檔驅動（Config-driven）

所有可調參數從 config 檔讀取，不 hardcode（硬編碼）路徑或值。Config 檔位於：

```
${CODEX_HOME:-<os.homedir()>/.codex}/codex-image.local.md
```

由 `lib/config.mjs` 的 `loadConfigOrDefaults()` 負責讀取與 merge（合併）。

| 設定項 | 預設值 | 說明 |
|-------|-------|------|
| `default_quality` | `high` | 預設品質表達（intent-only，見 §3） |
| `default_size` | `auto` | 預設尺寸 |
| `default_output_dir` | `./assets/generated/` | 預設輸出目錄（專案內相對路徑） |
| `deny_write_paths` | 平台相關（由 `lib/env.mjs` 的 `defaultDenyWritePaths()` 產生） | 路徑黑名單——**護欄（guardrail）不是 sandbox（沙箱）**，symlink（符號連結）可繞過 |
| `allow_cli_fallback` | `no` | 是否允許 CLI fallback |
| `timeout_seconds` | `240` | 單次 Codex CLI 呼叫的 timeout（逾時秒數） |
| `max_parallel` | `3` | 多變體並行上限 |
| `override_dispatch_model` | `""` | 覆寫調度層模型（空字串 = 不覆寫） |
| `quality_hint_mode` | `natural-language` | 品質提示方式 |

**Config 不可讀時不中斷**：`loadConfigOrDefaults()` 會逐欄位 fallback（回退）到內建預設值，並印出一行提示：`codex-image-setup 可建立或修復設定檔`。

---

## 11. 透明背景處理（Transparency）

預設路徑是官方流程——在純色 chroma-key（色鍵）背景上生成，然後用 vendored（附帶的）去背腳本移除背景：

1. Prompt 加上：`on a perfectly flat solid #00ff00 chroma-key background`（主體為綠色時改用 `#ff00ff`）
2. 生成後執行：

```bash
python <plugin>/vendor/remove_chroma_key.py \
  --input <src> --out <final.png> \
  --auto-key border --soft-matte \
  --transparent-threshold 12 --opaque-threshold 220 \
  --despill
```

3. 驗證 alpha channel（透明通道）：四角透明、主體覆蓋合理、無明顯 key-color fringe（色邊）

CLI fallback 使用 `gpt-image-1.5 --background transparent` 是**降級路徑**，須明確告知使用者並取得同意。

**Python + pillow 在 Windows 上常常不存在**——透明背景是**選用功能（optional）**。缺少時明確說明缺什麼（`python3 not found` 或 `pillow not installed`），不要靜默失敗。

詳見 `references/transparency.md`。

---

## 12. CLI Fallback 前置條件

走 CLI fallback 路徑需要：

1. `OPENAI_API_KEY` 環境變數已設定
2. Codex config 中 `[sandbox_workspace_write] network_access = true`

注意：`--ask-for-approval never` **不會**開啟 network access（網路存取）——它只抑制確認提示。

---

## 13. 多變體生成（Multi-variant）

使用者要求多張變體時：

- 每個變體一次 Codex CLI 呼叫（透過 `lib/run.mjs`）
- 檔名格式：`<name>_v1.png`、`<name>_v2.png`、...
- 並行數受 config 中 `max_parallel` 限制（預設 3）

---

## 14. 費用提示（Cost awareness）

生圖計入 Codex 一般額度。大量生成（>10 張）前先告知使用者，使用宿主可用的互動機制詢問並等待明確同意。

不要陳述任何具體 token 數字——無可靠來源。

---

## 15. 執行流程總覽

### Step 1：解析使用者請求

| 欄位 | 取得方式 | 預設 |
|------|---------|------|
| `image_prompt` | 使用者描述的視覺內容 | 必填 |
| `quality` | 使用者指定；未指定 → config `default_quality` | `high`（intent-only） |
| `size` | 使用者指定尺寸；未指定 → config `default_size` | `auto` |
| `output_dir` | 使用者指定；未指定 → config `default_output_dir` | `./assets/generated/` |
| `filename` | 使用者指定；未指定 → 從語意推導 | 語意推導（`lib/safe-path.mjs` 的 `sanitizeFilename()`） |

### Step 2：路徑安全檢查

使用 `lib/safe-path.mjs` 的 `resolveOutputPath()` 與 `lib/env.mjs` 的 `defaultDenyWritePaths()` 比對。命中 deny list 時拒絕並提示替代路徑。

使用 `lib/validate-size.mjs` 的 `validateSize(input, { mode: 'builtin' })` 驗證尺寸。內建路徑下，violations 為 advisory（建議性質），不阻擋——見 `references/sizes.md`。

### Step 3：組合 Prompt 並呼叫

1. 組合 prompt（見 §9）
2. 用 Write tool 把 job spec 寫成 JSON 檔（見 §5）
3. `node <plugin>/lib/generate.mjs < <job.json>`

### Step 4：讀取結果

`generate.mjs` 已代跑 §7 的六項檢查，結果在回傳 JSON 的 `checks` 欄位。**不要**自己去 grep stdout。

### Step 5：產出或回報

- `ok: true` → 回報 `output`（絕對路徑）與 `size`（實際 IHDR 尺寸）。若 `notes` 非空（例如檔案偏小的軟性訊號），一併如實轉述
- `ok: false` → 依 §8 停下回報，附上 `checks` 中失敗的項目與 `stderrTail`，不自動降級

---

## 常見錯誤與處理

詳見 `references/troubleshooting.md`。
