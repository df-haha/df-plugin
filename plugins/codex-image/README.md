# codex-image

用 Codex CLI 的內建 `image_generation` feature flag 生成 PNG 圖片。透過 Codex 帳號 token 計費，
不需要額外的 OpenAI / Stability API key。設計原則：**staging-then-verify、config-driven、
禁止 code-drawing**。

## 前置條件

| 項目 | 必要性 | 說明 |
|------|-------|------|
| Codex CLI | 必要 | 已安裝且已登入（`codex login`） |
| `image_generation` feature flag | 必要 | 透過 `codex features enable image_generation` 開啟 |
| Node.js | 必要 | 執行 `lib/*.mjs` 模組 |
| Python 3 + pillow | 選用 | 僅透明背景功能需要（chroma-key 去背） |
| `OPENAI_API_KEY` | 選用 | 僅 CLI fallback 路徑需要 |

## 安裝

### Claude Code

啟用 plugin 後執行：

```
/codex-image setup
```

Plugin root 透過 `${CLAUDE_PLUGIN_ROOT}` 解析。

### Codex

將本 plugin 目錄加入 Codex skills 搜尋路徑，或直接引用 skill。Codex 無 `${CLAUDE_PLUGIN_ROOT}`
——skill 從 SKILL.md 所在路徑往上走兩層解析 plugin root。

## Skills

| Skill | 用途 |
|-------|------|
| `codex-image` | 主要生圖 skill：解析請求 → staging 生成 → 驗收 → 複製到目標 |
| `codex-image-setup` | 環境偵測與設定：13 個 Phase（0-12），產出 config 檔 |

## Slash command

```
/codex-image <圖片描述>
/codex-image setup
```

## Config 檔位置與 schema

路徑：`${CODEX_HOME:-$HOME/.codex}/codex-image.local.md`

由 `codex-image-setup` 產生，`codex-image` 讀取。使用 YAML frontmatter + markdown body。

**偵測類欄位**（setup 自動填寫，tri-state：`true` / `false` / `unknown`）：

| 欄位 | 型別 | 說明 |
|------|------|------|
| `platform` | string | `win32` / `wsl` / `linux` / `darwin` |
| `is_wsl` | tri-state | 是否在 WSL 環境 |
| `codex_cli_version` | string / `unknown` | 偵測到的 CLI 版本 |
| `codex_logged_in` | tri-state | 登入狀態 |
| `image_generation_feature` | tri-state | `image_generation` feature flag 狀態 |
| `detected_dispatch_model` | string / `unknown` | 調度層模型 |
| `detected_dispatch_effort` | string / `unknown` | 調度層 reasoning effort |
| `python_cmd` | string / `unknown` | Python 指令（`unknown` = 找不到） |
| `pillow_available` | tri-state | pillow 套件已安裝 |
| `openai_api_key_present` | tri-state | API key 是否存在 |
| `network_access_configured` | tri-state | Codex config 中 `network_access` 狀態 |
| `smoke_status` | string | `passed` / `failed` / `skipped` / `unknown` |
| `last_smoke_at` | ISO 8601 | 最近 smoke test 時間 |

**使用者偏好欄位**：

| 欄位 | 型別 | 預設 | 說明 |
|------|------|------|------|
| `default_quality` | string | `high` | 品質意圖（intent-only） |
| `default_size` | string | `auto` | 預設尺寸 |
| `default_output_dir` | string | `./assets/generated/` | 預設輸出目錄 |
| `deny_write_paths` | JSON array | 平台相關 | 路徑黑名單 |
| `allow_cli_fallback` | `yes` / `no` | `no` | 是否允許 CLI fallback |
| `timeout_seconds` | number | `240` | 單次呼叫 timeout |
| `max_parallel` | number | `3` | 多變體並行上限 |
| `override_dispatch_model` | string | `""` | 覆寫調度層模型 |
| `quality_hint_mode` | string | `natural-language` | 品質提示方式 |

**Meta 欄位**：

| 欄位 | 型別 | 說明 |
|------|------|------|
| `schema_version` | string | Config schema 版本 |
| `setup_version` | string | Setup skill 版本 |
| `setup_at` | ISO 8601 | 最近 setup 時間 |

透明背景可用性由 `python_cmd`（非 `unknown`）+ `pillow_available`（`true`）推導，不另存欄位。

## 已知風險（Known risks）

1. **Smoke test 不等於真實負載（smoke =/= real load）**：setup 的 smoke test 只驗通路，不驗高壓情境（大尺寸、CJK 密集文字、透明背景）。
2. **Code-drawing 偵測是啟發式（heuristic）**：透過解析 JSONL `command_execution` events 判斷，除非 JSONL 明確暴露 `image_gen` tool call，否則無法硬性證明。
3. **`codex features list` 是人類可讀輸出，不是 API**：輸出格式隨 CLI 版本可能改變，解析失敗時標 `unknown` 而非猜測。
4. **Quality 張力尚未解決**：內建路徑的 `quality` 只能以自然語言提示，無法直接控制；CLI fallback 才有硬性參數。
5. **`deny_write_paths` 是護欄不是 sandbox**：symlink 可繞過此限制。
6. **Windows 常缺 Python + pillow**：透明背景功能在 Windows 上可能不可用。
7. **Vendored 去背腳本需手動同步上游**：`vendor/remove_chroma_key.py` 是從 `$CODEX_HOME/skills/.system/imagegen/scripts/` 複製而來，上游更新時需手動重新 vendor。
8. **Plugin root 解析因宿主而異**：Claude Code 有 `${CLAUDE_PLUGIN_ROOT}`；Codex 需從 SKILL.md 路徑反推。兩邊的 `lib/` 呼叫方式不同。
9. **Native-Windows Codex CLI 行為未測試**：`win32` 平台分支的所有行為尚未經實際 Windows 機器驗證——需要一位有 Windows 環境的同事驗證。
