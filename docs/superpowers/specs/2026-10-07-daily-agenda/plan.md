# daily-agenda — Plan

> rev5（2026-10-07）。使用者決定不做遮罩與代號化，資料保護＝外送說明與同意＋排除對話；保留並修正 Codex 第 1–4 輪中仍適用的意見。
> Phase 劃分：Phase 0 實測 → 1 vendor LINE MCP＋df-graph 小改 → 2 setup 0–5 → 3 收集 → 4 分析＋彙整 → 5 對外動作 → 6 修正迴圈＋排程。
> **Gate 0 未過不匯入 vendor。最弱環節：D-3 使用者本人確認的保證（0.1）、D-12 無工具分析程序（0.2）、D-8 提交與發布協定。**

## Architecture

```
                         ┌──────────── 讀取（原文只在本機收集程序） ────────────┐
/agenda ─▶ collect.py ─┬─▶ line_collect.mjs ─▶ vendored LINE 本機讀取器（recentChats／messages）
                       └─▶ MCP client ─▶ df-graph server（公開工具，immutable_ids=True）
                                 │  排除對話在讀取前過濾；結構化 JSONL，外部內容標記為不可信欄位
                                 ▼
                       runs/<id>/bundle.jsonl
                                 │  stdin
                                 ▼
                       分析程序：claude -p --tools "" --strict-mcp-config --safe-mode --no-session-persistence
                                 │  stdout（JSON）
                                 ▼
                       validate.py（schema＋來源 ID 存在性）
                                 ▼
                       ledger.py（待辦登記簿，固定代碼 T-xxxx）
                                 ▼
                       render.py（autoescape、CSP）
                                 ▼
                       gen/<n>/（彙整檔、ledger、state、known_chats、發布目標與雜湊）
                                 ─▶ 原子替換 CURRENT（唯一提交點）─▶ 發布到輸出資料夾 ─▶ 寫發布回執

寫入（Phase 5）：
模型 ─▶ mcp__plugin_daily-agenda_actions__prepare_action（目標、內容）
        └─ 代理：存完整 payload 與雜湊到 pending/<id>.json、回傳完整預覽
模型 ─▶ commit_action(id, target_display, content_display)  ← requiresUserInteraction：使用者本人在系統提示按允許
        └─ 代理：原子 rename pending/<id> → committing/<id>（只有一個請求能成功）
                 → 比對顯示欄位與 payload 完全一致、雜湊、期限 → 內部 MCP client 送出
                 → committing/<id> → done/<id>（sent／failed）；當機殘留 committing/ → uncertain（只查詢）
```

### 決策（依可能被改的程度排序）

- **D-3 動作代理（寫入安全的核心）。**
  plugin 透過自己的 `.mcp.json` 提供 `actions` MCP server（Node，沿用 vendored 的 `@modelcontextprotocol/sdk`），只有 `prepare_action`、`commit_action`、`action_status` 三個工具。
  - `pending/<id>.json`（僅本人可讀）：工具、完整目標（LINE 對話穩定 ID＋顯示名稱／行事曆時段與主旨）、完整 payload、payload SHA-256、建立時間、期限 10 分鐘、冪等鍵（行事曆即 `transaction_id`）。
  - **本人確認**：`commit_action` 標記 `_meta["anthropic/requiresUserInteraction"]`，其輸入含 `target_display`、`content_display`，讓 Claude Code 的系統確認提示直接顯示要核准的內容；代理比對這兩欄與待送檔完全一致才繼續。
  - **Phase 0 實測 0.1 是硬性關卡**：若此提示在任一權限模式不會出現或不顯示內容，Phase 5 不開工（不採用「使用者輸入確認碼」這類模型也能代打的備案），改回使用者決定。
  - **原子所有權**：commit 以 `rename(pending/<id>, committing/<id>)` 取得所有權，失敗即代表已被處理；送出後才移到 `done/<id>`（`sent`／`failed`）。代理啟動時，任何殘留在 `committing/` 的動作一律標 `uncertain`，只能經 `action_status` 查詢轉為 `sent`／`failed`，不得重送。
  - **LINE MCP 不註冊為使用者層級 MCP**；GUI 送出工具只存在於代理程序內部的子程序（`LINE_MCP_EXTENSIONS=1` 只設在該子程序）。plugin 未啟用時沒有任何送出路徑。
  - 送出工具序列（普通訊息 vs 引用回覆、既有草稿衝突、取消、不確定）依 Phase 0 實測定案。
  - `guard.mjs`（PreToolUse）攔下任何非代理路徑的送出類工具，只作縱深防護。
- **D-12 分析程序沒有工具。**
  `/agenda` 與 setup 第 8 步的分析由 `analyze.py` 啟動 `claude -p --tools "" --strict-mcp-config --safe-mode --no-session-persistence`，在獨立空目錄執行，bundle 從 stdin 餵入、JSON 從 stdout 取回；不讀寫任何檔案、不載入任何 MCP、hook、plugin。外部內容裡的指令沒有任何工具可以執行。
  主對話只執行腳本、看摘要（筆數、狀態），不讀 bundle 內容。
  Phase 0 驗證：工具清單確實為空；對抗測試（要求讀取外部 sentinel 檔、要求呼叫工具）全部無效。
  替代：plugin 子代理限制 Read／Write——只限制工具種類、不限制路徑，已否決。
- **D-1 讀取走本機收集程序；df-graph 走公開 MCP 工具介面。**
  `collect.py` 以 MCP client 啟動 df-graph server（命令取自 `claude mcp get df-graph`），呼叫 `mail_list_recent`（清單）、`mail_get`（全文；清單只有 200 字預覽）、`calendar_list`（行程），一律帶 `immutable_ids=True`。信件以固定 ID 為主鍵、`internetMessageId` 為輔助去重鍵。LINE 經 `line_collect.mjs` 匯入 vendored 本機讀取器。
- **D-13 df-graph 小改（使用者同意；選用參數，預設行為不變）。**
  (1) `mail_list_recent`、`mail_get`、`mail_search` 新增選用參數 `immutable_ids: bool = False`，為 True 時加 `Prefer: IdType="ImmutableId"`；(2) 回傳新增 `internetMessageId` 欄位（不改既有欄位）；(3) `calendar_create` 新增選用參數 `transaction_id: str = ""`，有值時放進 Graph 請求的 `transactionId`。測試同時覆蓋預設舊行為與新選用行為。bump df-graph 版本；依其 README 擬給原作者的說明（由使用者決定何時送出）。
- **D-8 狀態、待辦登記簿與提交／發布。**
  - `checkpoints`：`line`、`mail` 各自的高水位；讀取往前重疊 2 小時，依來源 ID 去重；只隨提交點推進。`calendar` 每次重抓今天＋3 天。
  - `ledger.jsonl`：固定代碼 `T-xxxx`；`open → maybe_done → done`、`open → dismissed`、`maybe_done (7 天) → collapsed`、`collapsed → open`；`collapsed` 仍在彙整檔摺疊區顯示。
  - 報表代碼：每份彙整檔有隨機短碼 `R-xxx`。以編號標記完成必須帶報表代碼且為最新一份；否則只接受 `T-xxxx`，或重新列出請使用者確認。
  - **提交**：寫入 `gen/<n>/`（彙整檔、ledger、state、`known_chats`、發布目標路徑與輸出雜湊）並 fsync → 原子替換 `CURRENT`。這是唯一提交點。
  - **發布（可恢復，不宣稱跨目錄原子）**：提交後把彙整檔以「暫存檔＋rename」寫到輸出資料夾，驗證雜湊後寫 `gen/<n>/published`。每次執行開始前先檢查 `CURRENT` 指向的世代是否已有 `published`，沒有就先補發；補發失敗則本次不收集新資料並告知使用者。
- **D-10 對話選擇。** `config.line.excluded_chats`（穩定對話 ID＋顯示名稱）；collector 讀取前過濾。新對話預設讀取、列在檔頭，`known_chats` 存在世代 state 中隨提交點生效。
- **D-9 資料量預算。** 依 Phase 0 實測定每次分析的輸入上限；超過時依對話分批分析再彙整；檔頭顯示涵蓋範圍，禁止靜默截斷。
- **D-7 輸出安全。** 分析程序只輸出 JSON（schema 驗證）；每項必須引用 bundle 中存在的來源 ID；renderer 做唯一一次、依輸出格式的跳脫；Jinja autoescape、禁止 raw HTML、只允許 `https:`／`mailto:`／本機 Outlook／LINE 連結、CSP 禁止所有網路資源；不可信欄位一律純文字。
- **D-14 排程無互動（C-10）。** 排程以 `claude -p` 執行並設 `DAILY_AGENDA_NONINTERACTIVE=1`；需要使用者回答的分支（D-11 信任啟動、修正準則）一律跳過或延後、不推進該來源進度、不寫信任清單，檔頭列「待你手動處理」。執行鎖 `runs/lock`（含 pid）防重疊；逾時 20 分鐘中止且不提交。
- **D-11 信任啟動。** 讀取執行中 `LINE.exe` 的實際路徑、版本、大小、SHA-256、Authenticode 簽章（發行者需為 LINE 公司）→ 寫入只供 probe 子程序使用的暫時信任檔（`LINE_MCP_TRUSTED_BUILDS` 指向它）→ 讀最小無副作用資料 → 刪除暫時檔 → 原子寫入正式信任清單。簽章無法驗證時不信任（Phase 0 0.10 為阻擋項）。
- **D-5 vendor 方式。** plugin 內附已修改的 v3.3.5（含 LICENSE、THIRD_PARTY）、`patches/*.patch`、`UPSTREAM.md`（基準 tag／commit、工具清單快照、patch 清單、升版安全差異審查步驟）；setup 複製到使用者 runtime 目錄並 `npm ci --ignore-scripts`。

## Dependencies

| 元件 | 版本 | 取得方式 | 驗證 |
|---|---|---|---|
| Claude Code | ≥ 2.1.292（`--tools ""`、`--strict-mcp-config`、`--safe-mode`） | 使用者自備 | setup 檢查版本 |
| Node.js | ≥ 24 | 使用者自備（缺則給 winget 指令） | `node --version` |
| uv | 任意 | 缺則給官方安裝指令 | `uv --version` |
| Python | 3.12 x64 | `uv venv --python 3.12`（runtime 目錄內） | `platform.architecture()` |
| cryptography / Pillow | 上游 requirements | `uv.lock` 鎖定 | import |
| SQLite3MultipleCiphers | 2.5.1 | 官方 release zip | archive＋DLL SHA-256 |
| CUA Driver | **待定**（上游文件同時出現 0.23.2 與 0.28.2） | 官方 release zip | Phase 0 核實後凍結 |
| line-desktop-mcp | v3.3.5＋2 patches | vendored | 上游測試＋patch 測試 |
| df-graph plugin | 本次修改後版本 | marketplace | setup 檢查版本；MCP client 呼叫公開工具 |

## Data Model

使用者狀態目錄 `%USERPROFILE%\.daily-agenda\`（可用 `DAILY_AGENDA_HOME` 覆寫）：

```
config.json          設定（schema_version、consent、line、output、refine、ctx、schedule）
CURRENT              目前生效的世代編號（唯一提交點）
gen/<n>/             state.json（checkpoints、known_chats、runs_since_refine、last_refine_at、criteria_version、report_code）、
                     ledger.jsonl、agenda.(md|html)、publish.json（目標路徑、雜湊）、published（發布回執）
criteria/v<N>.md     判斷準則各版（只增不改）
ctx-index.jsonl      個人 ctx 索引
trusted_builds.json  本機信任的 LINE 版本（version、bytes、sha256、signer、trusted_at）
actions/pending|committing|done/   待送動作狀態目錄
runs/<id>/           bundle.jsonl、model.json、run.log（不含原文）
runs/lock            執行鎖
runtime/             .venv、sqlite3mc、cua-driver、line-desktop-mcp（含 node_modules）
```

`config.json` 範例：

```json
{
  "schema_version": 1,
  "consent": { "accepted_at": "2026-10-07T10:00:00+08:00" },
  "line": { "enabled": true, "main_database": "qw0b9...729.edb",
            "excluded_chats": [{ "ref": "chat:…", "name": "家人群組" }] },
  "output": { "dir": "D:\\work\\daily-agenda", "formats": ["html"] },
  "refine": { "every_runs": 10, "every_days": 14 },
  "ctx": { "dir": "D:\\notes\\me" },
  "schedule": { "enabled": false, "time": null }
}
```

彙整輸出：`<output.dir>\agenda-YYYY-MM-DD.(md|html)`；同日重跑加 `-2`、`-3`。

## Canonical Project Identity

不適用（無後端、無多租戶）。本機穩定識別：LINE 對話＝上游 `chatRef`（`chat:<24 hex>`）；信件＝Graph immutable message id，輔以 `internetMessageId`；事件＝Graph event id；待辦＝`T-xxxx`；報表＝`R-xxx`。

## Writer Attribution

不適用（無共享資料寫入）。對外動作記錄於 `actions/done/<id>.json`（時間、工具、目標 ID、內容雜湊、冪等鍵、回執；不含原文）。

## Environment Variables

| 變數 | 用途 | 設定者 |
|---|---|---|
| `LINE_MCP_EXTENSIONS=1` | 只設在動作代理啟動的 LINE 子程序 | 動作代理 |
| `LINE_MCP_PYTHON` / `LINE_MCP_SQLITE3MC_DLL` / `LINE_MCP_CUA_DRIVER` | runtime 絕對路徑 | collector／動作代理從 config 帶入 |
| `LINE_MCP_MAIN_DATABASE` | **新增**：主資料庫檔名 | 同上 |
| `LINE_MCP_TRUSTED_BUILDS` | **新增**：信任清單路徑（probe 時指向暫時檔） | 同上 |
| `DAILY_AGENDA_HOME` | 覆寫狀態目錄 | 選用 |
| `DAILY_AGENDA_NONINTERACTIVE` | 排程模式 | 排程 |

## Testing Strategy

- **TDD**：每個模組先寫測試（pytest／`node:test`）。
- **測試邊界**：
  1. Collector：分頁、局部失敗、各來源 checkpoint、重疊去重、排除對話、新對話、immutable ID。
  2. 資料外送：未同意不讀取；排除對話在送往模型的內容中零殘留（SC-3）。
  3. 動作代理：過期、重放、內容改動、顯示欄位不符、目標不符、並行 commit 只有一個成功、各當機點恢復為 `uncertain`、`transaction_id` 重試沿用。
  4. 分析程序：工具清單為空、對抗 fixture（要求讀檔／呼叫工具）、非法 JSON、虛構來源 ID、超長欄位。
  5. 提交與發布：每個中斷點注入失敗（含 CURRENT 切換後、發布 rename 前後），重跑後一致且最終發布成功（SC-10）。
  6. 輸出安全：XSS fixture 含藏在原文中的惡意字串（SC-7）。
  7. df-graph：預設舊行為不變、新選用參數行為。
- **上游回歸**：套 patches 後跑上游 `npm test`、`npm run test:python`。
- **CI**：可 mock 的單元測試在 GitHub Actions `windows-latest` 跑；記憶體讀金鑰與 CUA 送出只在真機驗收。
- **Windows 實機**：Phase 0、2、5 與端對端。

## Service Layer

不適用（無常駐服務；動作代理是 Claude Code 依需求啟動的 stdio MCP）。模組：

| 模組 | 職責 |
|---|---|
| `envcheck.py` | Claude Code／Node／uv 版本、LINE 執行中、版本、簽章 |
| `runtime.py` | venv、DLL／CUA 下載驗證、vendor 複製、`npm ci`、版本戳記（暫存目錄→rename） |
| `line_detect.py` | 逐一試解主資料庫 |
| `trust.py` | D-11 信任啟動 |
| `graph_client.py` | MCP client 呼叫 df-graph 公開工具 |
| `collect.py` | 各來源讀取、checkpoint、對話過濾、`--probe` |
| `ctx_index.py` | 掃 ctx 資料夾產索引（支援格式、略過 symlink／junction） |
| `analyze.py` | D-12 無工具分析程序 |
| `state.py` | config、世代目錄、CURRENT、發布與補發 |
| `ledger.py` | 待辦登記簿轉移、報表代碼 |
| `validate.py` | model.json schema＋來源 ID 驗證 |
| `render.py` | 安全輸出 |
| `schedule.py` | `schtasks`、執行鎖 |
| `uninstall.py` | 回滾 |
| `actions/server.mjs` | D-3 動作代理 |
| `hooks/guard.mjs` | 縱深防護（攔下非代理路徑的送出類工具） |

## HTTP REST API

不適用（無 HTTP 介面）。

## Telegram Bot

不適用。

## Deployment

- marketplace：`.claude-plugin/marketplace.json` 新增 `daily-agenda`。
  - `0.1.0`（Phase 4 後）：真唯讀——不含 `actions` MCP、不啟動任何 LINE GUI 元件。
  - `0.2.0`（Phase 5 後）：加入動作代理。
  - `1.0.0`（Phase 6 後）。
- 只有 `.claude-plugin/plugin.json`（Windows 原生 Claude Code 專用）。
- README：資料外送說明（同 US-0）；df-graph 登入取得的 Graph 權限（Mail.ReadWrite、Mail.Send、Calendars.ReadWrite）大於讀取所需，本 plugin 只在使用者確認後使用寫入權限；讀取 LINE 程式記憶體可能觸發公司端點防護，部署前請 IT 確認；「請依公司使用的方案條款確認可否使用」（不代為詮釋條款）。
- plugin 更新後 `/agenda` 偵測 vendor 版本戳記不同時，提示重跑 runtime 同步。

## Files to Create

```
plugins/daily-agenda/
  .claude-plugin/plugin.json
  .mcp.json                         （Phase 5 起：actions server）
  README.md
  commands/agenda.md  commands/agenda-setup.md
  skills/agenda-setup/SKILL.md
  skills/agenda/SKILL.md            （含修正準則、標記完成子流程）
  skills/agenda-reply/SKILL.md      （Phase 5）
  hooks/hooks.json  hooks/guard.mjs  hooks/*.test.mjs
  actions/server.mjs  actions/*.test.mjs
  scripts/line_collect.mjs
  scripts/daily_agenda/*.py         （見 Service Layer）
  scripts/tests/test_*.py  scripts/tests/fixtures/*
  scripts/pyproject.toml  scripts/uv.lock
  templates/agenda.html.j2  templates/agenda.md.j2  templates/criteria-seed.md  templates/analyze-prompt.md
  vendor/line-desktop-mcp/          （v3.3.5，已套 patches，含 LICENSE）
  vendor/patches/0001-trusted-builds.patch  vendor/patches/0002-main-database.patch
  vendor/UPSTREAM.md
plugins/df-graph/server/mailtools.py、caltools.py、test_tools.py、.claude-plugin/plugin.json（D-13）
.claude-plugin/marketplace.json     （新增條目、df-graph 版本）
.github/workflows/daily-agenda.yml  （windows-latest 單元測試）
docs/superpowers/specs/2026-10-07-daily-agenda/df-graph-upstream-note.md（給原作者的說明草稿）
```

## Rollout Order

1. **Phase 0 實測**（不產出正式程式碼，結果寫 `phase0-findings.md`）。
2. Phase 1 vendor＋patches＋上游回歸＋security 審查；df-graph 小改＋測試＋上游說明草稿。
3. Phase 2 setup 0–5。
4. Phase 3 collector＋ctx＋輸出設定。
5. Phase 4 分析程序＋準則＋`/agenda`＋登記簿＋提交發布＋安全輸出 → 0.1.0。
6. Phase 5 動作代理＋回覆＋事件＋security 審查 → 0.2.0。
7. Phase 6 修正迴圈＋排程 → 端對端 → 1.0.0。

## Risks & Open Questions

| # | 風險／未知 | 影響 | 對策 |
|---|---|---|---|
| R-1 | `requiresUserInteraction` 在各權限模式的實際行為與顯示內容 | 本人確認無法保證 | Phase 0 硬性關卡；不成立則 Phase 5 不開工、回報使用者 |
| R-2 | `claude -p --tools ""` 在 Windows 的行為與輸入上限 | 分析程序不可用 | Phase 0 實測；定 D-9 預算 |
| R-3 | 公司端點防護對「讀 LINE 程式記憶體」的反應 | 被封鎖或告警 | Phase 0 觀察；README 揭露；部署前 IT 確認 |
| R-4 | LINE 自動更新 → 信任清單不符 | LINE 那半失效 | `/agenda` 遇到時就地走 D-11（排程依 D-14 跳過） |
| R-5 | 上游快速改版 → vendor 落後 | 錯過修正 | UPSTREAM.md 工具快照＋patch 重套測試＋升版安全差異審查 |
| R-6 | `recentChats` 上限 50 對話 | 漏讀 | Phase 0 驗分頁；無分頁則擴充 vendor 分頁介面後才進 Phase 3（阻擋項） |
| R-7 | 信件、LINE 原文（含客戶名稱與業務內容）送到雲端模型 | 營業資訊外送 | 使用者決定不遮罩；setup 第 0 步揭露並取得同意；README 請公司依方案條款確認；排除對話機制 |
| R-8 | 排程：LINE 未開、電腦鎖定、重疊執行 | 排程失敗或卡住 | Phase 0 驗 `claude -p` 無互動執行；D-14 |
| R-9 | 送出工具序列（普通 vs 引用回覆、草稿衝突） | 送錯或送不出 | Phase 0 實測後定案 |
| R-10 | `mail_get` 全文量大 | 超出預算 | D-9 分批 |
| Q-1 | 行事曆去重的「相似」判準 | 誤判 | 同日、時段重疊 ±60 分、主題正規化後相似度門檻；Phase 5 fixture 校準；回應遺失的重試由 `transactionId` 保證 |

## 回滾策略

- 使用者端：`/agenda-setup --uninstall` → 刪除排程 → 詢問後刪除 `%USERPROFILE%\.daily-agenda\`（預設保留輸出資料夾）。本 plugin 不註冊任何使用者層級 MCP，不動既有 MCP 註冊。
- 安裝中斷：runtime 先裝到暫存目錄，驗證成功才 rename 為正式目錄；失敗保留舊版。
- 發布端：marketplace 條目降回前一版或移除；plugin 為新增目錄，不影響其他 plugin。df-graph 修改皆為選用參數，回退只需還原該版本。
- vendor patch 出問題：不設新增的兩個環境變數即回到上游原行為（fail-closed）。
