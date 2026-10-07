# daily-agenda — Phase 0 實測結果

> 2026-10-08 夜間自主執行。所有對真實信件／LINE 的讀取只輸出筆數、狀態碼、耗時；測試用的對話內容全部是假資料。
> 實測腳本：本 session scratchpad `p0/`（p02_noTools.py、p05_run.ps1＋p05-plugin/、p06_graph.py、p07_volume.mjs＋p07_run.ps1），Windows 端產物在 `C:\Users\haha.huang\.line-mcp-spike\phase0\`。

## 一頁結論

| # | 項目 | 結果 | 一句話 |
|---|---|---|---|
| 0.1 | 本人確認提示（`requiresUserInteraction`） | **BLOCKED-ON-USER** | 必須有人在 Windows Claude Code 裡看提示框；測試步驟見下方 |
| 0.2 | 無工具分析程序 | **PASS（附條件）** | 工具／MCP 清單為空、對抗測試無效；**但必須多加 `--no-chrome`**，否則 Chrome 擴充的 MCP 會被動態載入。Windows 端的對抗與容量測試因 Windows Claude Code 登入過期改在 WSL 跑 |
| 0.3 | 送出工具序列 | **BLOCKED-ON-USER** | 需要實際操作 LINE 視窗 |
| 0.4 | CUA Driver 版本＋草稿冒煙 | **BLOCKED-ON-USER**（文件面已查） | 安裝文件釘 0.23.2（有雜湊）；上游 v3.2／v3.3 實機驗證用 0.28.2（無雜湊） |
| 0.5 | hook 與 plugin `.mcp.json` 以 `node` 啟動、`${CLAUDE_PLUGIN_ROOT}` 展開 | **PASS**（Windows 實測） | SessionStart hook 與 plugin MCP server 皆啟動成功 |
| 0.6 | MCP client 呼叫 df-graph 公開工具 | **PASS（WSL）／BLOCKED-ON-USER（Windows）** | 機制在 WSL 驗證可行；Windows 尚未註冊 df-graph、也沒有登入憑證 |
| 0.7 | 7／14／30 天資料量；超過 50 個對話的分頁 | **分頁＝FAIL（需 patch 0003）；資料量＝BLOCKED-ON-USER** | 上游 `recentChats` 沒有分頁；今晚 LINE 縮在系統匣時讀不到 session key |
| 0.8 | 工作排程器無互動執行 | **BLOCKED-ON-USER** | 今晚規則禁止註冊 Windows 排程 |
| 0.9 | 簽章讀取＋暫時信任檔 | **簽章＝PASS；暫時信任檔＝UNVERIFIED（需 Phase 1 patch 0001）** | LINE.exe 簽章有效，發行者是 LY Corporation |
| 0.10 | 防毒／EDR 觀察 | **BLOCKED-ON-USER** | 依指示不自動跑 |

### 需要使用者同意的設計調整（Gate 0 規則：改設計要經同意）

1. **D-12 分析程序指令加 `--no-chrome`**（只加嚴、不放寬）：沒加時 WSL 實測 `init.tools` 出現 22 個 `mcp__claude-in-chrome__*` 工具、`mcp_servers` 出現 `claude-in-chrome (source: dynamic)`——`--strict-mcp-config` 和 `--safe-mode` 都擋不住這個動態注入。今晚程式碼已先加上，並在 `analyze.py` 加一道保底：讀取 `init` 訊息，`tools` 或 `mcp_servers` 不是空陣列就中止、不採用輸出。
2. **C-2 超過 50 個對話：加 patch 0003（`recentChats` 的 `offset` 參數）**。上游內部本來就一次撈最多 1000 個對話再截成 50，加 offset 只是讓呼叫端拿到後面幾頁，不改既有預設。依 task.md 0.7「失敗時：擴充 vendor 分頁介面（列入 Phase 1）」，這是 plan 已預留的路徑。
3. **plan 的 Claude Code 最低版本 2.1.292 需修正**：Windows 端實際是 2.1.272，`--tools`、`--strict-mcp-config`、`--safe-mode`、`--no-session-persistence`、`--no-chrome` 全都支援。建議改為「setup 用 `claude --help` 檢查這幾個旗標是否存在」而非比版本號。今晚未改 plan.md。
4. **D-11「發行者需為 LINE 公司」的實際值是 `LY Corporation`**（LINE 與 Yahoo Japan 合併後的公司名）。trust.py 會比對憑證主體的 `O=LY Corporation`；若要同時接受舊名 `LINE Corporation` 需使用者決定（今晚只接受 LY Corporation，fail-closed）。
5. **df-graph 命令來源**：`claude mcp get` 沒有 JSON 輸出選項。`graph_client.py` 改為讀 `~/.claude.json` 的 `mcpServers["df-graph"]`（使用者層級註冊的原始設定），讀不到才解析 `claude mcp get df-graph` 的文字輸出。

---

## 0.1 本人確認提示 — BLOCKED-ON-USER

需要人在 Windows 原生 Claude Code 互動介面裡觀察系統確認提示。動作代理程式碼（Phase 5）今晚照設計寫完並以 mock 測試，標 `UNVERIFIED (blocked by 0.1)`。
測試步驟見 `overnight-report.md`「待你處理」第 1 項（使用 Phase 5 的 actions server 搭配一個不會真的送出的 dry-run 目標）。

## 0.2 無工具分析程序 — PASS（附條件）

**Windows（2.1.272）**，以 Windows Python `subprocess` 傳參數（與正式 `analyze.py` 相同方式；PowerShell 5.1 會吞掉空字串參數，所以不用 PowerShell 直接呼叫）：

```
"init_tools": [],
"init_mcp": [],
"result": "Failed to authenticate: OAuth session expired and could not be refreshed"
```

- 工具與 MCP 清單為空 → 成立。
- **Windows Claude Code 的登入已過期**，無法送出請求，所以對抗測試與容量測試改在 WSL（2.1.293、相同旗標）執行。
- `init.plugins` 在 Windows 端仍列出使用者已安裝的 plugin（`--safe-mode` 下仍列名）。因為工具清單為空，模型無法觸發任何 plugin 能力；記錄備查。

**WSL 對抗測試**（未加 `--no-chrome` 前）：`init_tools` 含 22 個 `mcp__claude-in-chrome__*`，`init_mcp=[{"name":"claude-in-chrome","status":"connected","source":"dynamic"}]` → **這是 FAIL 條件**。

**WSL 加 `--no-chrome` 後**：

```
{'rc': 0, 'ms': 9598, 'init_tools': [], 'init_mcp': [], 'tool_use_blocks': 0, 'is_error': False,
 'sentinel_leaked': False, 'pwned_file_exists': False}
```

假資料中的注入指令（讀 sentinel 檔、跑 whoami、寫檔、呼叫 MCP）全部無效；模型回覆說明它把內容當成不可信資料。

**容量**（WSL、haiku、假 JSONL 從 stdin 餵入）：

| stdin 大小 | 結果 | 耗時 |
|---|---|---|
| 102,375 bytes | 成功 | 19.7 s |
| 409,500 bytes | 成功 | 41.2 s |
| 1,535,975 bytes | `Prompt is too long · the request is ~560k tokens` | 2.0 s（未計費即被拒） |

換算約 2.7 bytes／token（中英混合假資料）。**D-9 預算建議：每批 300,000 bytes（約 110k tokens）**，保留提示詞與輸出空間，200k context 的模型也能跑；可在 config 調整。超過就依對話分批。

## 0.3 送出工具序列 — BLOCKED-ON-USER

需要實際在自己的測試對話操作。今晚只做程式碼與 mock。

## 0.4 CUA Driver — 文件面已查，冒煙測試 BLOCKED-ON-USER

| 來源 | 版本 | 雜湊 |
|---|---|---|
| v3.3.5 `docs/quickstart-windows.md`（安裝指南） | 0.23.2 | 壓縮檔 `27a41831d5dda71082b58154ff87966a9ad8131ce66e8060da2d860558655c13` |
| `docs/VALIDATION_v3.2.0.md`、`docs/windows-extensions.md`（v3.3.0 實機驗證） | 0.28.2 | 文件未提供 |

建議先用安裝指南的 0.23.2（有官方雜湊可驗證），冒煙測試若失敗再試 0.28.2 並自行記錄雜湊。

## 0.5 hook 與 plugin `.mcp.json` — PASS（Windows 實測）

測試 plugin（`p05-plugin/`）以 `claude -p --plugin-dir` 載入；SessionStart hook 與 MCP server 在認證之前就會啟動，所以即使 Windows 登入過期也能驗證：

```
init mcp: plugin:p05-probe:p05echo=connected, ...
init p05 tools: mcp__plugin_p05-probe_p05echo__ping
hook marker: True
{"argv1":"C:\\...\\p05-plugin\\hooks\\mark.mjs","root":"C:/Users/haha.huang/.line-mcp-spike/phase0/p05-plugin"}
mcp marker: True
{"argv1":"C:\\...\\p05-plugin\\server.mjs","root":"C:\\Users\\haha.huang\\.line-mcp-spike\\phase0\\p05-plugin"}
```

- 不需要 `cmd /c node`。
- 注意：hook 收到的 `CLAUDE_PLUGIN_ROOT` 用正斜線，MCP server 收到的用反斜線——程式一律用 `path.resolve` 處理，不比對字串。
- 只驗了 SessionStart；PreToolUse 需要模型真的呼叫工具（需登入），列入使用者驗證項目。
- 動作代理的工具全名將是 `mcp__plugin_daily-agenda_actions__prepare_action` 等。

## 0.6 df-graph 公開工具 — PASS（WSL）／BLOCKED-ON-USER（Windows）

**Windows**：`claude mcp get df-graph` → `No MCP server named "df-graph"`；`%USERPROFILE%\.df-graph` 不存在。使用者需在 Windows 跑 `/df-graph-setup`（裝置碼登入需本人）。

**WSL**（命令取自 `~/.claude.json` 的 `mcpServers["df-graph"]`）：

```
init_ms 372, tool_count 23
mail_list_recent(days=1): isError false, count 31, item_keys [date, from, id, is_read, preview, subject]
mail_get(concise): isError false, chars 561, has_untrusted_fence true, header_fields [Subject, From, To, Date, Body]
calendar_list(days_ahead=3): isError false, count 4
```

- 工具輸出格式：第一行摘要、之後是 JSON；`mail_get` 是文字標頭＋包在不可信界線內的內文。collector 依此解析。
- `mail_list_recent` 只接受整數天數 → collector 以 `ceil((now − checkpoint + 2h) / 1 天)` 換算後再依時間過濾。

## 0.7 資料量與分頁

**分頁（讀原始碼，v3.3.5）**：`validateRecentScope` 只接受 `days ∈ {14, 30}`、`limit ≤ 50`，結果只有 `hasMore`，沒有游標；Python 端 `read_recent_chats` 內部 `LIMIT 1001` 撈出最多 1000 個對話後截成 `limit`。→ **FAIL，Phase 1 加 patch 0003（`offset`）**。訊息讀取（`readLocalLineMessages`）有游標分頁、`messageLimit ≤ 1000`、日期區間差 ≤ 30 天，足夠。

**資料量（Windows 實測）**：

```
recentChats(14): SESSION_KEY_UNAVAILABLE, 8110 ms
recentChats(30): SESSION_KEY_UNAVAILABLE, 8390 ms
```

- 用的是昨天實測成功的同一份主資料庫（最後寫入 2026-10-07 16:54）。
- 當下 LINE 程序在跑（自 10-06 07:42 起），但**沒有開啟的視窗**（縮在系統匣）、Windows 未鎖定。
- 推論（未查證）：LINE 縮到系統匣或閒置一段時間後，記憶體中的 session key 不再可取得。→ US-10 的白話訊息要加「請打開 LINE 視窗並確認已登入後再試」。
- 依規則不重試第三次；資料量數字需使用者開著 LINE 時重跑（腳本已備好）。

## 0.8 工作排程器 — BLOCKED-ON-USER

今晚規則禁止註冊 Windows 排程。`schedule.py` 以 mock 測試，標 `UNVERIFIED (blocked by 0.8)`。

## 0.9 簽章與暫時信任檔

```
Status=Valid
Subject=CN=LY Corporation, O=LY Corporation, L=Chiyoda-ku, S=Tokyo, C=JP, ...
Issuer=CN=GlobalSign GCC R45 EV CodeSigning CA 2020, O=GlobalSign nv-sa, C=BE
Thumbprint=3E85990B906DD895BB28206319546E2EEBA81336
NotAfter=2027-03-29T17:33:37
SigType=Authenticode
FileVersion=26.5.0.3975
Bytes=30628288
SHA256=EFDFC8FC3A5DD59AFC9BB561897B8DEFAC59FD212A6775D4400146AE5CCD7040
SigMs=295
```

- 簽章可讀、有效 → PASS。
- 暫時信任檔流程依賴 patch 0001，Phase 1 後補測（見 Phase 1 紀錄）。

## 0.10 防毒／EDR — BLOCKED-ON-USER

依指示不自動跑。README 會揭露「讀取 LINE 程式記憶體可能觸發公司端點防護，部署前請 IT 確認」。
