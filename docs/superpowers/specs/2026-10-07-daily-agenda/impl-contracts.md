# daily-agenda — 實作介面契約（內部）

> 2026-10-08 夜間實作用。讓並行開發的模組對齊資料格式與 API。與 spec/plan rev5 衝突時以 spec/plan 為準，並回頭修本檔。
> 本檔是內部開發文件，不是對外文件。

## 0. 共通

- 目錄：`plugins/daily-agenda/`（下稱 `<P>`）。Python 套件 `<P>/scripts/daily_agenda/`，測試 `<P>/scripts/tests/`，uv 專案根 `<P>/scripts/`（`pyproject.toml`）。
- 測試：`cd <P>/scripts && uv run pytest -q`（WSL/Linux 也要能跑；Windows 專屬行為一律透過可注入的介面，測試用 fake）。
- Node：`node --test <P>/**/*.test.mjs`（Node 24；不得依賴 npm 套件，除非是 vendor 已有的）。
- 程式風格：Python 3.11+、type hints、`pathlib`、f-string、開檔明確 `encoding="utf-8"`；不 mutate 參數；不留 debug print。JS：`const`、named exports、async/await。
- 時區：`config.tz`（預設 `Asia/Taipei`）；所有時間存 ISO-8601 含時差（`2026-10-08T09:00:00+08:00`）。
- **不可信資料**：所有外部可控字串（LINE 對話名／發言者／內文、信件寄件者／主旨／內文、行事曆主旨／地點／內文）一律放在記錄的 `untrusted` 物件裡。log 與 stdout 摘要**只能**有筆數、狀態碼、耗時、ID。
- 原子寫檔：`write tmp（同目錄）→ flush → os.fsync → os.replace`。共用 `daily_agenda.fsutil.atomic_write_text/bytes/json`。
- 錯誤代碼 → 白話訊息：`daily_agenda.messages.humanize(code) -> str`（中文、可行動）。

## 1. 使用者狀態目錄（`state.home()`）

`DAILY_AGENDA_HOME` 環境變數，否則 `Path.home() / ".daily-agenda"`。

```
config.json  CURRENT  gen/<n>/  gen/<n>.staging/  criteria/v<N>.md  ctx-index.jsonl
trusted_builds.json  actions/{pending,committing,done}/  runs/<run_id>/  runs/lock  runtime/
```

### config.json（schema_version 1）

```json
{
  "schema_version": 1,
  "tz": "Asia/Taipei",
  "consent": { "accepted_at": "2026-10-08T09:00:00+08:00", "text_version": 1 },
  "line": { "enabled": true, "main_database": "qw0b9...729.edb",
            "excluded_chats": [{ "ref": "chat:0123456789abcdef01234567", "name": "家人群組" }] },
  "mail": { "enabled": true, "folders": ["inbox"] },
  "output": { "dir": "D:\\work\\daily-agenda", "formats": ["html"] },
  "refine": { "every_runs": 10, "every_days": 14 },
  "ctx": { "dir": null },
  "schedule": { "enabled": false, "time": null },
  "analysis": { "batch_bytes": 300000, "model": null, "timeout_s": 600 }
}
```

`consent` 為 null 或缺 → 任何讀取指令（collect、run、chats list、setup 第 8 步）一律回 `CONSENT_REQUIRED` 並結束，**不得在檢查前讀取任何資料**。

### gen/<n>/ 世代（唯一提交點＝`CURRENT` 檔內容）

```
state.json      { "schema_version":1, "generation":n, "created_at",
                  "checkpoints": { "line": iso|null, "mail": iso|null },
                  "known_chats": [{ "ref","name","first_seen" }],
                  "mail_index": { "<internetMessageId>": "mail:<immutableId>" },   // 保留 60 天
                  "runs_since_refine": int, "last_refine_at": iso|null, "criteria_version": int,
                  "report_code": "R-7K2"|null, "report_index": { "1": "T-4821", ... },
                  "last_run": { "run_id", "at", "sources": {...摘要...} } }
ledger.jsonl    每行一個待辦（見 §4）
agenda.md / agenda.html   （依 config.output.formats）
publish.json    { "targets": [{ "path": "<output.dir>\\agenda-2026-10-08.html", "sha256": "..." }] }
published       發布回執（JSON：{ "at", "targets":[{path, sha256}] }）
```

### state.py API

```python
def home() -> Path
def load_config(h: Path) -> dict            # 缺檔回預設（consent=None）
def save_config(h: Path, cfg: dict) -> None # 原子
def current_generation(h: Path) -> int | None
def load_state(h: Path) -> dict             # CURRENT 世代的 state.json；無世代回初始 state
def load_ledger(h: Path) -> list[dict]
def stage_generation(h: Path, *, state: dict, ledger: list[dict], outputs: dict[str, str],
                     publish_targets: list[dict]) -> Path    # 寫 gen/<n+1>.staging/ 全部檔案並 fsync
def commit_generation(h: Path, staging: Path) -> int         # rename staging→gen/<n>，原子替換 CURRENT
def publish(h: Path, gen: int) -> dict                       # 依 publish.json 以 tmp+rename 寫到輸出資料夾、驗雜湊、寫 published
def ensure_published(h: Path) -> dict                        # 執行開始前呼叫；CURRENT 世代無 published 就補發；回 {"status":"ok|republished|failed", ...}
def cleanup_uncommitted(h: Path) -> None                     # 刪 *.staging 與編號 > CURRENT 的世代
def output_filename(out_dir: Path, date: str, fmt: str) -> Path   # agenda-YYYY-MM-DD.fmt，同日已存在加 -2、-3
```

測試注入：`state._fail_point(name)`（預設 no-op），測試 monkeypatch 讓它在指定點 raise，名稱至少：
`staged`、`before_current_replace`、`after_current_replace`、`before_publish_rename`、`after_publish_rename`、`before_receipt`。

## 2. bundle.jsonl（收集程序 → 分析程序）

每行一筆 JSON。第一行一定是 `meta`。

```json
{"kind":"meta","run_id":"20261008-0900-ab12","generated_at":"...","tz":"Asia/Taipei",
 "sources":{"line":{"status":"ok|skipped|failed","code":null,"from":"iso","to":"iso","chats":12,"messages":340},
            "mail":{"status":"ok","code":null,"from":"iso","to":"iso","messages":31},
            "calendar":{"status":"ok","code":null,"from":"iso","to":"iso","events":4}},
 "new_chats":[{"ref":"chat:…"}], "batch":{"index":1,"total":1}}
{"kind":"criteria","version":1,"text":"…判斷準則全文…"}
{"kind":"open_todo","todo_id":"T-4821","status":"open|maybe_done","priority":"P1","due":null,
 "created_at":"iso","src_ids":["mail:…"],"untrusted":{"title":"…"}}
{"kind":"line_message","src_id":"line:chat:<24hex>:message:<24hex>","chat_ref":"chat:<24hex>",
 "chat_type":"group|direct","ts":"iso","is_self":false,
 "untrusted":{"chat_name":"…","sender":"…","text":"…"}}
{"kind":"mail","src_id":"mail:<immutableId>","internet_message_id":"<…>","received_at":"iso","folder":"inbox",
 "untrusted":{"from":"…","to":"…","subject":"…","body":"…"}}
{"kind":"event","src_id":"event:<id>","start":"iso","end":"iso","all_day":false,"web_link":"https://…",
 "untrusted":{"subject":"…","location":"…","organizer":"…","preview":"…"}}
{"kind":"ctx","path":"relative/path.md","untrusted":{"title":"…","summary":"…"}}
```

- `src_id` 是全域唯一、穩定的來源 ID；validate.py 以 bundle 內出現過的 `src_id` 集合驗證模型輸出。
- 排除的對話：在呼叫 LINE 讀取**之前**就過濾（不讀它的訊息）；bundle 中不得出現其 `chat_ref`、名稱、任何訊息（SC-3 測試以此檢查）。
- 新對話 = `recentChats` 回來、不在 `known_chats` 也不在 `excluded_chats` 的對話。預設讀取；`meta.new_chats` 列出 ref，名稱放 view model 的檔頭。

## 3. model.json（分析程序輸出，validate.py 驗證）

```json
{
  "schema_version": 1,
  "items": [
    {"action":"new","title":"…≤120字","priority":"P0|P1|P2","due":"YYYY-MM-DD"|null,
     "src_ids":["…"],"reason":"…≤200字"},
    {"action":"update","todo_id":"T-4821","priority":"P1","due":null,"src_ids":["…"],"reason":"…"},
    {"action":"maybe_done","todo_id":"T-4821","src_ids":["…證據…"],"reason":"…"},
    {"action":"skip","src_ids":["…"],"reason":"依準則略過的理由"}
  ]
}
```

validate.py：

```python
def validate(model: dict, bundle_src_ids: set[str], open_todo_ids: set[str]) -> ValidationResult
# ValidationResult: items (通過的), rejected: list[{"index","code"}]
# code: SCHEMA_INVALID / UNKNOWN_SRC_ID / UNKNOWN_TODO_ID / FIELD_TOO_LONG / EMPTY_SRC_IDS
```

- 每項至少 1 個 `src_id`（`update` 可為空陣列）；任何一個 `src_id` 不存在 → 整項拒絕（SC-8）。
- 字串長度上限：title 120、reason 200；超過 → 拒絕該項（不截斷）。
- 未知欄位 → 拒絕該項。頂層不是物件或缺 `items` → 整份拒絕（`MODEL_OUTPUT_INVALID`）。
- 模型輸出永遠不會產生「待送動作」——validate 沒有任何動作相關欄位，AS-14 以此為不變量。

## 4. 待辦登記簿 ledger（ledger.py）

```json
{"id":"T-4821","title":"…","priority":"P1","due":null,
 "status":"open|maybe_done|done|dismissed|collapsed",
 "src_ids":["mail:…"],"created_at":"iso","updated_at":"iso",
 "maybe_done_since":null,"maybe_done_reason":null,
 "history":[{"at":"iso","from":"open","to":"maybe_done","by":"model|user|system"}]}
```

轉移：`open→maybe_done`（model）、`maybe_done→done`（user 確認）、`maybe_done→open`（user 否認）、`open→done`（user）、`open→dismissed`（user）、`maybe_done→collapsed`（system，maybe_done_since 起 ≥7 天）、`collapsed→open`（user「恢復 T-xxxx」）、`collapsed→done`（user）。其他轉移一律拒絕。

```python
def apply_model(ledger: list[dict], items: list[dict], *, now: datetime, id_factory=None) -> list[dict]
    # new：若其 src_ids 與某 open/maybe_done 待辦重疊 → 視為 update（防重疊讀取造成重複）
def auto_collapse(ledger, *, now) -> list[dict]
def mark_done(ledger, state, *, report_code: str | None, numbers: list[int] = (), todo_ids: list[str] = (), now) -> tuple[list[dict], dict]
    # 以編號標記：report_code 必須等於 state.report_code，否則 raise StaleReport（呼叫端重新列出請使用者確認）
def restore(ledger, todo_id, *, now) -> list[dict]
def new_todo_id(existing: set[str]) -> str     # T- + 4 位數字，不重複
def new_report_code() -> str                   # R- + 3 碼（去掉 0/O/1/I/L）
def carry_days(todo, *, now) -> int
```

## 5. view model → render.py

```python
def build_view(*, state, ledger, bundle_meta, events, new_chat_names, notices, now, report_code, report_index) -> dict
def render(view: dict, fmt: Literal["md","html"]) -> str
```

view：

```json
{"report_code":"R-7K2","date":"2026-10-08","generated_at":"iso",
 "sources":[{"name":"LINE","status":"ok","message":"白話","coverage":"10/07 09:00 – 10/08 09:00"}],
 "new_chats":["…名稱…"], "notices":["…待你手動處理…"],
 "today":[{"no":1,"todo_id":"T-4821","priority":"P0","title":"…","due":"…","carry_days":2,
           "sources":[{"label":"Mail · 10/07 · 寄件者","href":null}]}],
 "maybe_done":[{"no":5,…,"reason":"…"}], "collapsed":[…], 
 "events":[{"start":"…","end":"…","subject":"…","location":"…","href":"https://…"}],
 "skipped":[{"summary":"…","reason":"…","sources":[…]}],
 "coverage":{"batched":false,"batches":1}}
```

html 安全規則（D-7）：Jinja2 `autoescape=True`、模板不用 `|safe`；`href` 只允許 `https:`、`mailto:`（其他一律變 null）；`<meta http-equiv="Content-Security-Policy" content="default-src 'none'; style-src 'unsafe-inline'; img-src data:; base-uri 'none'; form-action 'none'">`；摺疊區用 `<details>`（不用 JS）。md 輸出要跳脫 Markdown 連結／HTML（`<`、`>`、`[`、`]`、`(`、`)`、`` ` ``、`!`）。

## 6. 收集（collect.py、graph_client.py、line_collect.mjs）

```python
class LineSource(Protocol):
    def probe(self) -> dict                                   # {"ok":bool,"code":str|None,"ms":int}
    def recent_chats(self, *, days: int, offset: int, limit: int) -> dict   # 上游 recentChats 結果（含 patch 0003 offset）
    def messages(self, *, chat_name: str, chat_ref: str, date_from: str, date_to: str, cursor: str | None) -> dict
class GraphSource(Protocol):
    def mail_list(self, *, days: int, folder: str) -> list[dict]
    def mail_get(self, message_id: str) -> dict               # {"id","internet_message_id","from","to","subject","received_at","body"}
    def calendar_list(self, *, days_ahead: int, days_back: int) -> list[dict]

def collect(h, cfg, state, *, now, line: LineSource | None, graph: GraphSource | None, run_id) -> CollectResult
# CollectResult(records, source_status, new_checkpoints, known_chats, new_chats, mail_index)
```

- 各來源獨立：任一來源失敗 → `status:"failed"`、`code`，不推進該來源 checkpoint，其他照常。
- 讀取起點 = checkpoint − 2 小時（無 checkpoint 時用 setup 選的天數）；LINE 單次 ≤31 天，超過切段；依 `src_id` 去重。
- 信件一律 `immutable_ids=True`；以 `mail_index[internetMessageId]` 正規化 src_id（SC-11）。
- 行事曆每次重抓今天＋3 天，不用 checkpoint。
- `line_collect.mjs`：`node line_collect.mjs --vendor <dir>`，stdin 一個 JSON 請求 `{"op":"probe"|"recentChats"|"messages", ...}`，stdout 一個 JSON 結果；只 import vendor 的唯讀 reader 函式，**不得 import 任何送出相關模組**；環境變數由 Python 端帶入（`LINE_MCP_PYTHON`、`LINE_MCP_SQLITE3MC_DLL`、`LINE_MCP_MAIN_DATABASE`、`LINE_MCP_TRUSTED_BUILDS`），**不設 `LINE_MCP_EXTENSIONS`**。

## 7. CLI（`python -m daily_agenda <cmd>`）

呼叫方式（skill 內）：`uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}/scripts" python -m daily_agenda <cmd>`。
所有指令 stdout 輸出**一個 JSON 物件**（`{"ok":bool,"code":str|null,"message":白話,...}`），exit code 0＝ok、1＝可預期失敗、2＝參數錯。
除了標 ★ 的指令，輸出**不得含任何原文**（只有筆數、狀態、代碼、路徑、ID）。★ 指令是 setup／互動流程中刻意顯示給使用者看的內容，只在同意之後可執行。

| 指令 | 用途 | 輸出重點 |
|---|---|---|
| `consent --status｜--accept｜--revoke` | setup 第 0 步 | `accepted_at` |
| `envcheck` | setup 第 1 步 | checks[] |
| `runtime install [--with-cua]`／`runtime status` | setup 第 1 步、`/agenda` 前檢查 | 各元件狀態、`RUNTIME_OUTDATED` |
| `trust probe` | setup 第 2 步／`LINE_BUILD_UNVERIFIED` 時 | `trusted｜already_trusted｜skipped_noninteractive｜<code>` |
| `line-detect` | setup 第 3 步 | `status: ok｜multiple｜failed`、`database`、`candidates[{name,mtime}]`、`codes` |
| `line-select --database NAME` | 多份都成功時使用者選 | |
| `probe` | setup 第 2–4 步試讀 | 各來源 status／筆數 |
| ★`outlook-preview` | setup 第 4 步 | 最近 3 封信主旨＋寄件者、今天行程（給使用者確認） |
| ★`chats list --days 30` | setup 第 5 步 | `[{ref,name,type,last_at,excluded}]` |
| `exclude --set REF...｜--add REF｜--remove REF` | setup 第 5 步／之後修改 | excluded 數量 |
| `ctx index --dir D｜--skip` | setup 第 6 步 | 檔案數 |
| `config get`／`config set KEY=VALUE...` | setup 第 7 步（output.dir、output.formats、refine.every_runs、refine.every_days、analysis.model） | 設定摘要 |
| `criteria draft --days 7｜14｜30` | setup 第 8 步：收集 N 天＋無工具分析 → 草稿檔 | `draft_path` |
| ★`criteria show [--version N]`／`criteria save --file F`／`criteria list`／`criteria restore --version N` | 準則管理 | `version` |
| `run [--noninteractive]` | `/agenda` 全流程（補發檢查→鎖→收集→分析→驗證→登記簿→輸出→提交→發布） | `report_code`、`outputs[]`、`sources{}`、`counts{}`、`new_chats`（名稱★）、`needs{line_trust,refine,runtime_sync}`、`rejected_items` 數 |
| ★`show --report R --items 3,5｜--todo T-1,T-2` | 標記前重新列出確認 | 項目標題 |
| `mark-done --report R --items 3,5｜--todo T-1` | US-13；`maybe_done` 項目確認移除也用它 | 成功／`STALE_REPORT` |
| `reject-done --todo T-1` | 可能已完成→其實沒做完 | |
| `dismiss --todo T-1` | 不再追蹤 | |
| `restore T-xxxx` | 摺疊→移回待辦 | |
| `refine status`／`refine draft`／`refine apply --file F`／`refine skip` | 修正準則（US-9） | `due`、`reason`、`draft_path` |
| `schedule install --time HH:MM｜remove｜status` | setup 第 7 步 | |
| `uninstall [--purge]` | 回滾 | |

## 8. 動作代理（actions/server.mjs）

- stdio MCP（手寫最小 JSON-RPC，不依賴 npm 套件），工具 `prepare_action`、`commit_action`、`action_status`。
- `commit_action` 的 tool 定義帶 `_meta: {"anthropic/requiresUserInteraction": true}`；輸入 `{id, target_display, content_display}`。
- 狀態目錄 `<home>/actions/{pending,committing,done}/<id>.json`；所有權＝`rename(pending/<id>, committing/<id>)`。
- 啟動時 `committing/` 殘留 → 轉為 `done/<id>.json` 且 `status:"uncertain"`；`uncertain` 只能經 `action_status` 查詢改成 `sent|failed`，不得重送。
- 送出由代理內部以 MCP client 啟動子程序：LINE（vendored server，`LINE_MCP_EXTENSIONS=1` 只設在該子程序）、df-graph（`calendar_create` 帶 `transaction_id`）。
