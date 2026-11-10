# workhub-inbox

把外部待辦單向同步進 Work Hub（marketing-pm-system）的個人待辦收件匣，讓 PM 系統成為唯一的待辦系統。
以「來源轉接器」擴充；v1 只有 `daily-agenda`（LINE、Outlook 信件、行事曆整理出的待辦）。規格見 `docs/superpowers/specs/2026-11-10-workhub-inbox/spec.md`。

## 需求

- Node.js 22 以上（用內建 `fetch`，不需安裝套件）
- 已安裝並跑過 daily-agenda（要有 `~/.daily-agenda/CURRENT`）。WSL 上會自動換算 Windows 的 `%USERPROFILE%\.daily-agenda`
- Work Hub 個人 API 金鑰（PM 系統「MCP 連線」頁產生）

## 設定

```bash
export WORKHUB_INBOX_ENDPOINT="https://<你的 Work Hub 網址>/mcp"
export WORKHUB_INBOX_API_KEY="<個人 API 金鑰>"   # 只放環境變數，不要寫進 repo
```

## 使用

```bash
node plugins/workhub-inbox/scripts/inbox-sync.js sync --source daily-agenda --show-titles   # 試跑
node plugins/workhub-inbox/scripts/inbox-sync.js sync --source daily-agenda --apply          # 實寫
```

在 Claude Code 裡說「同步待辦到 PM」也會觸發 skill，流程一樣先試跑再確認。

| 參數 | 說明 | 預設 |
|---|---|---|
| `--apply` | 實際寫入；沒帶就是試跑 | 試跑 |
| `--max-writes N` | 單次最多送出幾筆寫入（失敗也算，已存在的不算），超過的下次再補；一直補不完時超過 7 天的會被略過 | 20 |
| `--only T-1,T-2` | 只建立這些 agenda 代碼 | 全部 |
| `--exclude T-3` | 這些代碼這次不建立 | — |
| `--priorities P0,P1` | 只收這些優先級 | P0,P1,P2 |
| `--da-home PATH` | daily-agenda 資料夾 | 自動找 |
| `--endpoint URL` | Work Hub MCP 端點 | `WORKHUB_INBOX_ENDPOINT` |
| `--show-titles` | 試跑清單顯示標題 | 不顯示 |
| `--json` | 機器可讀輸出（不含標題），給排程用 | — |

## 同步規則

| agenda 狀態 | PM 動作 |
|---|---|
| 未完成，期限或出現日期在 7 天內，PM 還沒有 | 建立（P0→優先 1、P1→2、P2→3；期限填全天到期日；來源 `line`／`outlook`；標籤 LINE／信件／行事曆） |
| 已完成、不處理 | PM 對應待辦勾完成 |
| 可能已完成 | 不寫入，只列筆數 |

使用者在 PM 改過的內容不會被覆蓋；PM 勾完成不回寫 agenda；在 PM 取消的待辦不會被重建（伺服器去重含已取消）。去重鍵是 `agenda:<代碼>:<建立時間>`（agenda 代碼是 4 碼隨機數字，單用可能重複）。

## 本機對照檔

`--apply` 會在 `~/.workhub-inbox/scope-<雜湊>/agenda-map.json`（可用 `WORKHUB_INBOX_STATE_DIR` 改位置）記下「去重鍵 → PM 待辦 ID」。Work Hub 的清單每次最多回 500 筆，靠這份對照檔，舊待辦掉出清單後 agenda 標完成時仍能勾完成。雜湊由端點＋金鑰算出（不存金鑰本身），不同環境、不同使用者各一份，不會混用；換金鑰會從空表重新開始。刪掉它不會造成重複建立（伺服器會去重），只是較舊的待辦可能漏勾完成。

## 安全

- 寫入全程不經 AI，待辦標題只當資料傳遞（它是 AI 從第三方訊息整理出來的不可信內容）。
- 只讀 daily-agenda 的整理成品，不讀原始訊息；daily-agenda 本身維持唯讀。
- 金鑰只從環境變數讀；`--json` 與錯誤訊息不含標題與金鑰。

## 測試

```bash
node --test 'plugins/workhub-inbox/**/*.test.js'
```
