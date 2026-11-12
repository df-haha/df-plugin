# workhub-inbox

把外部待辦單向同步進 Work Hub（PM 系統）的個人待辦收件匣，讓 PM 系統成為唯一的待辦系統。
以「來源轉接器」擴充；目前只接 `daily-agenda`（LINE、Outlook 信件、行事曆整理出的待辦）。規格見 `docs/superpowers/specs/2026-11-10-workhub-inbox/spec.md`。

## 需求

- Node.js 22 以上（用內建 `fetch`，不需安裝套件；daily-agenda 已要求 Node.js 24，裝好 daily-agenda 就符合）
- 已安裝並跑過 daily-agenda（要有 `%USERPROFILE%\.daily-agenda\CURRENT`）。資料夾會自動找：Windows 用 `%USERPROFILE%\.daily-agenda`，有設 `DAILY_AGENDA_HOME` 就用它，WSL 上會換算到 Windows 的使用者資料夾
- Work Hub 個人 API 金鑰：用公司微軟帳號登入 PM 系統，在左側「AI 串接」頁產生（完整金鑰只顯示一次）

## 安裝

在 Claude Code 外的終端機（PowerShell 或 bash 都可以）執行：

```
claude plugin marketplace add df-haha/df-plugin
claude plugin install workhub-inbox@df-haha-plugins
```

## 設定

需要兩樣設定：Work Hub 的 MCP 端點網址（`WORKHUB_INBOX_ENDPOINT`）、你的個人 API 金鑰（`WORKHUB_INBOX_API_KEY`）。金鑰只放環境變數，不要寫進任何檔案或貼到對話裡。

已經照 PM 系統「AI 串接」頁設好 `DF_WORKHUB_API_KEY` 的話，只要再設 `WORKHUB_INBOX_ENDPOINT`。沒設 `WORKHUB_INBOX_API_KEY` 時會自動改用 `DF_WORKHUB_API_KEY`，同一把金鑰不用設兩次；兩個都有設時，以 `WORKHUB_INBOX_API_KEY` 為準。

**Windows（PowerShell）**，長期有效（建議）。先設端點：

```powershell
[Environment]::SetEnvironmentVariable("WORKHUB_INBOX_ENDPOINT", "https://<Work Hub 網址>/mcp", "User")
```

還沒設過 `DF_WORKHUB_API_KEY` 的人，再設金鑰（已經設過就跳過這行，不要把範例文字貼進去）：

```powershell
[Environment]::SetEnvironmentVariable("WORKHUB_INBOX_API_KEY", "<個人 API 金鑰>", "User")
```

設完要把 Claude Code 和開著的終端機視窗全部關掉再重開，新的設定才會生效。

只在目前這個 PowerShell 視窗有效（關掉就沒了，適合先試）；第二行同樣只有還沒設 `DF_WORKHUB_API_KEY` 的人才需要：

```powershell
$env:WORKHUB_INBOX_ENDPOINT = "https://<Work Hub 網址>/mcp"
```

```powershell
$env:WORKHUB_INBOX_API_KEY = "<個人 API 金鑰>"
```

要在 Claude Code 裡用，得在同一個視窗裡用 `claude` 指令啟動 Claude Code。

確認有設到（只顯示有沒有值，不印出金鑰）：

```powershell
[bool]$env:WORKHUB_INBOX_ENDPOINT; ($env:WORKHUB_INBOX_API_KEY -or $env:DF_WORKHUB_API_KEY)
```

兩行都顯示 `True` 才算設好（第二行看的是兩個金鑰變數有沒有任一個有值）。

**macOS／Linux／WSL（bash）**：

```bash
export WORKHUB_INBOX_ENDPOINT="https://<Work Hub 網址>/mcp"
```

還沒設過 `DF_WORKHUB_API_KEY` 的人才需要：

```bash
export WORKHUB_INBOX_API_KEY="<個人 API 金鑰>"
```

## 使用

**一般使用者**：在 daily-agenda 的彙整頁勾完成、刪除、改優先後，照常複製「確認待辦 R-XXX…」那段文字貼回 Claude。daily-agenda 更新完待辦登記簿後，Claude 會在同一個對話直接把這份彙整頁保留下來的待辦寫進 PM，你標完成或刪除的，PM 對應那筆會勾完成。沒有剛確認過的彙整頁時，說「同步待辦到 PM」就會同步最新一份。

確認只在彙整頁做一次：不用另開對話、不另外試跑、不另看一份清單、也不分批先寫 3 筆（2026-11-13 決定拿掉這三道確認）。Claude 會告訴你建立幾筆、勾完成幾筆、失敗幾筆。

想先看清單再寫的話，直接跟 Claude 說「先給我看清單」：它會先試跑，把標題寫進本機的清單檔（`inbox-review.html`，Windows 通常在 `C:\Users\<你的帳號>\.workhub-inbox\`）讓你自己打開看，你回覆要哪些代碼後才寫入。

**不要開「略過權限確認」模式。** 金鑰放在環境變數，這台電腦上的 Claude 對話都讀得到。Claude 要執行連線或寄送類的指令時會跳出確認視窗，這是最後一道防線：看到不是你要的動作，就按拒絕。

**開發者**：在本 repo 根目錄可以直接跑命令列（PowerShell 與 bash 寫法相同）。從市集安裝的人電腦裡沒有這個資料夾，不適用。

```
node plugins/workhub-inbox/scripts/inbox-sync.js sync --source daily-agenda --apply --report R-7KQ
node plugins/workhub-inbox/scripts/inbox-sync.js sync --source daily-agenda --report latest --review-file
node plugins/workhub-inbox/scripts/inbox-sync.js sync --source daily-agenda --apply --report latest --only T-1234,T-5678
```

第一行是預設流程：把彙整頁 R-7KQ 列出、登記簿現在仍未完成的待辦寫進 PM，並把 agenda 已完成或不處理的在 PM 勾完成。第二行是試跑，只列出、不寫入，標題寫進清單檔。第三行只寫最新彙整頁裡你挑的代碼。

| 參數 | 說明 | 預設 |
|---|---|---|
| `--apply` | 實際寫入；沒帶就是試跑 | 試跑 |
| `--report R-XXX` | 只建立這份彙整頁列出、登記簿現在仍未完成的待辦（`R-` 加 3 碼大寫英數；`latest`＝最新一份）。daily-agenda 只保留最新一份的對照，舊代碼會回結束碼 2 | 不限報表 |
| `--max-writes N` | 單次最多送出幾筆寫入（失敗也算，已存在的不算），超過的用同一行重跑補；不帶 `--report` 時一直補不完，超過 7 天的會被略過 | 20 |
| `--only T-1234,T-5678` | 只建立這些 agenda 代碼（格式是 `T-` 加四碼數字，至少一個）；和 `--report` 一起用時取交集 | 全部 |
| `--exclude T-9012` | 這些代碼這次不建立 | — |
| `--no-create` | 只勾完成，不建立任何待辦（不能和 `--report`、`--only`、`--exclude` 一起用） | — |
| `--priorities P0,P1` | 只收這些優先級 | P0,P1,P2 |
| `--da-home PATH` | daily-agenda 資料夾 | 自動找 |
| `--review-file` | 把清單（含標題與來源）寫進 `~/.workhub-inbox/inbox-review.html`，每次覆蓋；畫面上只顯示代碼與檔案位置 | 不寫 |
| `--json` | 機器可讀輸出（不含標題），給排程用 | — |

## 同步規則

| agenda 狀態 | PM 動作 |
|---|---|
| 未完成，列在 `--report` 指定的彙整頁上，PM 還沒有（不看出現多久） | 建立（同下） |
| 未完成，不帶 `--report`，期限或出現日期在 7 天內，PM 還沒有 | 建立（P0→優先 1、P1→2、P2→3；期限填全天到期日；來源 `line`／`outlook`；標籤 LINE／信件／行事曆） |
| 已完成、不處理 | PM 對應待辦勾完成（不限報表） |
| 可能已完成 | 不寫入，只列筆數 |

使用者在 PM 改過的內容不會被覆蓋；PM 勾完成不回寫 agenda；在 PM 取消的待辦不會被重建（伺服器去重含已取消）。去重鍵是 `agenda:<代碼>:<建立時間>`（agenda 代碼是 4 碼隨機數字，單用可能重複）。

## 本機對照檔

`--apply` 會在 `~/.workhub-inbox/scope-<雜湊>/agenda-map.json`（可用 `WORKHUB_INBOX_STATE_DIR` 改位置）記下「去重鍵 → PM 待辦 ID」。Work Hub 的清單每次最多回 500 筆，靠這份對照檔，舊待辦掉出清單後 agenda 標完成時仍能勾完成。雜湊由端點＋金鑰算出（不存金鑰本身），不同環境、不同使用者各一份，不會混用；換金鑰會從空表重新開始。刪掉它不會造成重複建立（伺服器會去重），只是較舊的待辦可能漏勾完成。

## 安全

- 寫入全程不經 AI，待辦標題只當資料傳遞（它是 AI 從第三方訊息整理出來的不可信內容）。
- 只讀 daily-agenda 的整理成品，不讀原始訊息；daily-agenda 本身維持唯讀。
- 金鑰與端點都只從環境變數讀，命令列不能指定端點。
- 端點只接受 `https://` 開頭的網址（`http://` 只限本機測試用的 127.0.0.1、localhost）；網址裡不能帶帳號密碼。不符合就在送出任何請求前停止。
- 不跟隨轉址：端點若回轉址就停止，不把金鑰與資料帶到別的網址。
- 畫面輸出、錯誤訊息與 `--json` 都不含標題、來源名稱、金鑰與端點設定值。寫入失敗時只顯示錯誤代碼（例如 `VALIDATION_ERROR`、`HTTP 503`），不顯示伺服器回的文字；看到 `SERVER_ERROR_TEXT_HIDDEN` 表示伺服器沒給代碼，請到 PM 系統查那一筆。萬一訊息裡出現金鑰或填錯的端點設定值，整段會改成固定的提示文字。標題與來源只寫進本機的清單檔，清單檔不含任何程式碼或外部連結。

## 測試

```bash
node --test 'plugins/workhub-inbox/**/*.test.js'
```
