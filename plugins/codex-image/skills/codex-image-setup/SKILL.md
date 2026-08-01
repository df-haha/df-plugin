---
name: codex-image-setup
description: 偵測環境並設定 codex-image plugin——平台偵測、Codex CLI 版本與登入、image_generation feature flag、Python/pillow、OPENAI_API_KEY、deny 清單、舊 skill 遷移、smoke test、寫設定檔。觸發時機：使用者說「codex-image setup」「設定生圖」「圖片生成設定」「codex image 環境」「image setup」、或首次使用 codex-image 前的環境檢查。
allowed-tools: Bash, Read
---

# Codex Image Setup — 環境偵測與設定

偵測目前環境的 Codex CLI、image_generation feature、Python toolchain（工具鏈）、API key 等狀態，產出一份 config 檔（設定檔）供 `codex-image` skill 使用。

## Plugin root（插件根目錄）解析

宿主不同，plugin root 解析方式也不同：

- **Claude Code**：使用 `${CLAUDE_PLUGIN_ROOT}` 環境變數。
- **Codex**：無此變數。從本 SKILL.md 所在路徑往上走兩層（`skills/codex-image-setup/SKILL.md` → plugin root），確認其下有 `lib/` 目錄。

後續所有 `node <plugin>/lib/...` 呼叫，`<plugin>` 皆指上述解析出的 plugin root。

---

## 寫入預算誠實公布

偵測本身不修改環境；所有 durable writes（持久寫入）逐項列出並取得授權：

| 可能的寫入 | 時機 | 說明 |
|-----------|------|------|
| Staging 目錄 | smoke test（Phase 10） | 暫存用，測試後清除 |
| Session rollout | 未加 `--ephemeral` 的 codex 呼叫 | smoke test 使用 `--ephemeral` 避免 |
| Config 檔（`codex-image.local.md`） | Phase 11 | 寫入由 `lib/config.mjs` 的 `writeConfigAtomic()` 處理（atomic rename） |
| Smoke 產生的 PNG | Phase 10 | 通過驗收後保留供檢視，使用者可刪 |
| Codex `config.toml`（`codex features enable`） | Phase 4（經使用者同意） | 只在 feature 為 `false` 且使用者同意時修改 |

---

## 偵測類欄位一律 tri-state

所有偵測結果使用 `true | false | unknown` 三態值。`unknown` 表示「查不到 / 格式不符預期」，不等同於 `false`。

單純的 boolean 會把「查不到」誤報為「沒有」——tri-state 避免此問題。

---

## Workflow

### Phase 0：平台偵測

使用 `lib/env.mjs` 的 `detectPlatform()` 偵測平台：

| 平台 | 偵測方式 |
|------|---------|
| `win32` | `process.platform === 'win32'` |
| `wsl` | Linux 且 `/proc/version` 含 `microsoft` |
| `linux` | 其餘 Linux |
| `darwin` | macOS |

結果決定後續預設（deny paths、Python 指令、路徑慣例）。同時記錄 `is_wsl`（WSL 環境為 `true`，其餘為 `false`）。

### Phase 1：讀既有設定

使用 `lib/config.mjs` 的 `loadConfigOrDefaults()` 讀取既有 config。

- **正常存在**：解析為 merge 基準，後續偵測結果逐欄位更新
- **不存在**：使用內建預設值
- **解析損壞**：經使用者同意後建立時間戳備份（`codex-image.local.md.<timestamp>.bak`），然後從預設值重建。正常重跑使用同目錄 temp + atomic rename（`writeConfigAtomic()`），不每次產生 `.bak`

### Phase 2：Codex CLI 版本

```bash
codex --version
```

- 有輸出 → 記錄為 `codex_cli_version`
- `command not found` → **終止 setup**，依平台給安裝指引：
  - `darwin` / `linux` / `wsl`：`npm install -g @anthropic-ai/codex`
  - `win32`：同上，但標注 native-Windows Codex CLI 行為尚未經本 plugin 測試

### Phase 3：Codex 登入狀態

```bash
codex login status
```

以 exit code 判定：
- `0` → 已登入（`codex_logged_in: true`）
- 非 `0` → 未登入（`codex_logged_in: false`）；提示使用者執行 `codex login`。smoke test（Phase 10）可跳過

### Phase 4：image_generation feature flag

```bash
codex features list
```

精確解析輸出中 `image_generation` 那一列的狀態值。

| 結果 | 處理 |
|------|------|
| `true` / `enabled` | 記錄 `image_generation_feature: true` |
| `false` / `disabled` | 印出 `codex features enable image_generation` 指令，使用宿主可用的互動機制取得同意後才代跑 |
| 指令不存在 / 輸出格式不符預期 | 記錄 `image_generation_feature: unknown`——**不 grep config 檔後假稱知道 effective state** |

### Phase 5：偵測調度層 model / effort

偵測使用者 Codex 環境中生效的調度層模型與 reasoning effort。

**不可只讀 user `config.toml`**——那會漏掉 project / profile / system / CLI 層級的 precedence（優先順序）覆寫。

- 取得到 → 記錄為 `detected_dispatch_model` 與 `detected_dispatch_effort`
- 取不到 → 記錄為 `unknown`

### Phase 6：Python 探測

使用 `lib/env.mjs` 的 `detectPythonCommand()` 依序嘗試：

1. `python3 --version`
2. `python --version`
3. `py -3 --version`（Windows）

找到後檢查 pillow：

```bash
<python> -c "import PIL; print(PIL.__version__)"
```

再檢查 vendor 去背腳本是否可用：

```bash
<python> <plugin>/vendor/remove_chroma_key.py --help
```

| 結果 | 記錄 |
|------|------|
| Python + pillow + 腳本均可用 | `python_cmd: python3`（或實際指令）、`pillow_available: true` |
| 缺 Python | `python_cmd: unknown`、`pillow_available: unknown`；**不自動安裝** |
| 有 Python 但缺 pillow | `python_cmd: <指令>`、`pillow_available: false`；**不自動安裝** |

透明背景可用性由 `python_cmd` + `pillow_available` 推導：兩者皆可用時透明背景功能才可用。不另存獨立欄位。

### Phase 7：CLI fallback 前置條件

偵測 `OPENAI_API_KEY` 環境變數是否存在，以及 Codex config 中 `[sandbox_workspace_write] network_access` 設定。

- **只偵測不修改**
- 只記錄布林狀態（`openai_api_key_present: true|false`、`network_access_configured: true|false|unknown`）
- **絕不寫入或列印 key 本身**
- Config 中 `allow_cli_fallback` 預設為 `no`

### Phase 8：路徑設定

依平台建議 `deny_write_paths`（使用 `lib/env.mjs` 的 `defaultDenyWritePaths(platform)`）。

詢問使用者預設輸出目錄。預設值為專案內 `./assets/generated/`。

**Setup 不建目錄**——目錄在實際生圖時由 `codex-image` skill 按需建立。

### Phase 9：舊 skill 遷移偵測

檢查以下兩個位置是否存在舊版 codex-image skill：

1. `~/.claude/skills/codex-image/`（Claude Code 舊 skill 位置）
2. `${CODEX_HOME:-$HOME/.codex}/skills/codex-image/`（Codex 舊 skill 位置）

- **存在** → 說明舊 skill 會與本 plugin skill 撞名（name collision），列出完整路徑，使用宿主可用的互動機制徵求同意後才移除。拒絕則提示使用者手動處理
- **不存在** → 跳過

**不自行刪檔**——必須取得明確同意。

### Phase 10：Smoke test（opt-in，預設不跑）

告知使用者：
- Smoke test 會消耗正常 Codex 額度
- 預設不執行，使用者可選擇跳過

使用宿主可用的互動機制詢問是否執行。

#### 如果執行

**主體必須是 raster-native（點陣原生）**——例如「紙張紋理上的水彩梨子」。

**明確禁止簡單紅色圓形**——簡單幾何圖形命中官方「When not to use」清單，且會主動誘發 code-drawing。

走與正式生圖相同的路徑——`node <plugin>/lib/generate.mjs < <job.json>`（見主 SKILL.md §5），這樣 smoke 驗的就是真正會被使用的通路。六項驗收檢查由該 entry point 代跑，結果在回傳 JSON 的 `checks` 欄位。

**Smoke 只驗通路，不驗高壓情境。** 通過只表示基本路徑可用，不保證複雜 prompt / 大尺寸 / CJK 文字 / 透明背景等高壓場景能正常運作。

結果記錄為 `smoke_status`（`passed` / `failed` / `skipped` / `unknown`）與 `last_smoke_at`（ISO 8601 時間戳）。

### Phase 11：寫設定檔

使用 `lib/config.mjs` 的 `serializeConfig()` + `writeConfigAtomic()` 寫入 config。

- 路徑：`${CODEX_HOME:-<os.homedir()>/.codex}/codex-image.local.md`
- 使用 atomic rename（原子重新命名）——先寫入同目錄 temp 再 rename
- `setup_at` 更新為當前時間戳
- 使用者手寫的 body 保留在 `<!-- codex-image:user-notes:begin -->` 與 `<!-- codex-image:user-notes:end -->` 之間
- 環境無變化重跑時，除 `setup_at` 外內容相同（idempotent，冪等）

### Phase 12：驗收清單

輸出一份驗收摘要，包含：

- 所有偵測欄位及其 tri-state 值
- 寫入與額度預算摘要（Phase 11 寫了什麼、smoke test 是否執行與結果）
- 任何標為 `unknown` 的欄位提示
- 透明背景功能可用性

---

## Config schema（設定檔結構）

欄位名稱與順序以 `lib/config.mjs` 的 `DETECTED_FIELDS` / `PREFERENCE_FIELDS` / `META_FIELDS` 陣列為 SSOT。

### 偵測類欄位（setup 寫入）

| 欄位 | 型別 | 說明 |
|------|------|------|
| `platform` | string | `win32` / `wsl` / `linux` / `darwin` |
| `is_wsl` | tri-state | 是否在 WSL 環境中（`platform` 為 `wsl` 時 `true`，其餘 `false`） |
| `codex_cli_version` | string \| `unknown` | Codex CLI 版本號 |
| `codex_logged_in` | tri-state | 登入狀態 |
| `image_generation_feature` | tri-state | `image_generation` feature flag 狀態 |
| `detected_dispatch_model` | string \| `unknown` | 偵測到的調度層模型 |
| `detected_dispatch_effort` | string \| `unknown` | 偵測到的 reasoning effort |
| `python_cmd` | string \| `unknown` | 偵測到的 Python 指令（`python3` / `python` / `py -3`）；`unknown` 表示找不到 Python |
| `pillow_available` | tri-state | pillow 套件是否已安裝 |
| `openai_api_key_present` | tri-state | `OPENAI_API_KEY` 是否存在（不記錄 key 本身） |
| `network_access_configured` | tri-state | Codex config 中 `[sandbox_workspace_write] network_access` 狀態 |
| `smoke_status` | string | `passed` / `failed` / `skipped` / `unknown` |
| `last_smoke_at` | ISO 8601 \| `""` | 最近一次 smoke test 時間 |

透明背景可用性由 `python_cmd`（非 `unknown`）+ `pillow_available`（`true`）推導，不另存欄位。

### 使用者偏好欄位

| 欄位 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `default_quality` | string | `high` | 預設品質意圖（intent-only） |
| `default_size` | string | `auto` | 預設尺寸 |
| `default_output_dir` | string | `./assets/generated/` | 預設輸出目錄 |
| `deny_write_paths` | JSON array | 平台相關 | 路徑黑名單（護欄，非 sandbox）；值必須以 `[` 開頭（JSON array 格式），`parseConfig()` 才能正確解析 |
| `allow_cli_fallback` | `yes` / `no` | `no` | 是否允許 CLI fallback |
| `timeout_seconds` | number | `240` | 單次呼叫 timeout |
| `max_parallel` | number | `3` | 多變體並行上限 |
| `override_dispatch_model` | string \| `""` | `""` | 覆寫調度層模型（空字串表示不覆寫，沿用偵測值） |
| `quality_hint_mode` | string | `natural-language` | 品質提示方式（`natural-language` = intent-only 句式） |

### Meta 欄位

| 欄位 | 型別 | 說明 |
|------|------|------|
| `schema_version` | string | Config schema 版本（`lib/config.mjs` 的 `CURRENT_SCHEMA_VERSION`） |
| `setup_version` | string | 產生此 config 的 setup skill 版本 |
| `setup_at` | ISO 8601 | 最近一次 setup 時間 |

### Body 區塊

| 標記 | 說明 |
|------|------|
| `<!-- codex-image:user-notes:begin/end -->` | 使用者手寫筆記區塊，跨重跑保留 |
