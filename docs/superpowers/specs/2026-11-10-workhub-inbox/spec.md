# workhub-inbox：外部待辦單向同步到 Work Hub 個人待辦（v1：daily-agenda）

> 2026-11-10 起草。使用者拍板：1A（另開 plugin）、2（v1 範圍照建議）、3（/hi 段落先維持讀 agenda）、4（無期限待辦只收 7 天內出現的）。

## 1. 目的

daily-agenda 每天從 LINE、Outlook 信件、行事曆整理出待辦，目前只寫進自己的 ledger（待辦登記簿）與 /hi 日報段落，沒有出口。
本 plugin 把這些待辦單向同步進 Work Hub（marketing-pm-system）的個人待辦收件匣，讓 PM 系統成為唯一的待辦系統（GTD 收件匣）。
plugin 以「來源轉接器（source adapter）」為單位擴充；v1 只有 `daily-agenda` 一個轉接器，之後錄音、Gmail 各加一個。

## 2. 安全邊界

- daily-agenda 規格 §75「不寫入 workhub」維持不變：daily-agenda 本身仍唯讀。寫入由本 plugin 的獨立腳本負責，只讀 daily-agenda 的整理成品（`CURRENT` generation 的 `ledger.jsonl`＋`state.json`），不讀 `runs/` 的原始訊息。
- 待辦標題是 AI 從第三方訊息整理出的不可信內容。**寫入全程不經 AI**：腳本直接以 HTTP 呼叫 Work Hub 的 MCP 端點（stateless JSON-RPC `tools/call`），標題只當資料傳遞。skill 只負責跑腳本、轉述筆數，不把標題放進任何工具參數。
- 金鑰與端點只從環境變數讀取（`WORKHUB_INBOX_API_KEY`、`WORKHUB_INBOX_ENDPOINT`；端點可用 `--endpoint` 覆寫），不寫檔、不進 log。plugin 不認個人機器上的其他變數名，由呼叫端（例如排程腳本）對應。`--json` 輸出與錯誤訊息只有代碼、筆數與動作類型，不含標題；人看的試跑清單加 `--show-titles` 才顯示標題。
- 預設 dry-run（試跑，只列出不寫入）；`--apply` 才寫入，且受 `--max-writes`（預設 20）上限保護。

## 3. 資料對應

| daily-agenda ledger | Work Hub 個人待辦 |
|---|---|
| `src_ids[0]` 前綴 `line:` | `source=line`、標籤 `LINE` |
| `src_ids[0]` 前綴 `mail:` | `source=outlook`、標籤 `信件` |
| `src_ids[0]` 前綴 `event:` | `source=outlook`、標籤 `行事曆` |
| `id`＋`created_at` | `external_id = agenda:<id>:<created_at>`（T-xxxx 是 4 碼隨機數字，ledger 重建後可能重用，單用會讓新待辦被誤判重複） |
| `title` | `title`（截到 300 字；只在建立時寫一次，之後不覆蓋使用者改過的標題） |
| `priority` P0／P1／P2 | `priority` 1／2／3 |
| `due`（YYYY-MM-DD） | `due_at`（全天，`due_has_time=false`）；無期限則不帶 |
| — | `description`：來源顯示名稱、agenda 代碼、「由 AI 整理自外部訊息，內容僅供參考」 |
| — | `workflow_state=active`，不設提醒（v1） |

`due_at` 以 UTC 傳送（Work Hub 的 datetime 欄位只收 `Z` 結尾）：台北當日 00:00 換算成 UTC，例如 11/12 → `2026-11-11T16:00:00.000Z`，配合 `due_has_time=false` 由 Work Hub 正規化成全天。

## 4. 同步規則（每次執行都套同一套，可重跑）

讀現況 → 比對目標 → 只補差額：

1. **建立**：ledger 狀態 `open`、優先級在 `--priorities` 內（預設 P0,P1,P2；首批匯入用 P0,P1），且符合「新鮮度」：
   - 有期限：期限 ≥ 今天 − 7 天；
   - 無期限：建立日 ≥ 今天 − 7 天；
   - Work Hub 尚無同 `source`＋`external_id` 的待辦。
2. **完成**：Work Hub 已有對應待辦且未完成，ledger 狀態為 `done` 或 `dismissed` → `pm_complete_personal_task`。
3. **可能已完成**（`maybe_done`／`collapsed`）：v1 不寫入，只在摘要列筆數，請使用者到 agenda 確認。原因：正式環境未開啟今天清單旗標，沒有對既有待辦加標籤的工具（`label_ids` 需要標籤 ID，正式環境查不到）。
4. 使用者在 Work Hub 改標題、日期、勾完成：v1 不回寫 agenda，也不被覆蓋。
5. ledger 已不存在的項目：不動。

去重：Work Hub 端以 `source`＋`external_id` 去重，**含已完成與已取消的待辦**（marketing-pm-system `src/server/workhub/service.js:1696`），同鍵重送回 `deduplicated:true`、不建新列；每筆寫入另帶 `idempotency_key = workhub-inbox:<動作>:<external_id 雜湊>`。

已取消的待辦：`pm_list_personal_tasks` 的 `all`／`completed` 檢視都不回傳已取消列，所以規劃階段會把它算進「建立」，送出後由伺服器去重擋下（回 `deduplicated`）。因此使用者在 PM 取消一筆待辦，之後不會被重建；它會留在「建立」清單直到過了 7 天新鮮度。

寫入上限（`--max-writes`）只算真的新增與完成；去重命中不算寫入、不佔名額。

## 5. 介面

```
node scripts/inbox-sync.js sync --source daily-agenda [--apply] [--max-writes N]
     [--priorities P0,P1] [--only T-1234,...] [--exclude T-1234,...]
     [--endpoint URL] [--da-home PATH] [--show-titles] [--json]
```

- `--only`／`--exclude`：以 agenda 代碼挑選或排除「建立」的項目（只接受 `T-` 加 4 碼數字），完成同步不受影響。被排除的項目下次執行仍會出現；要永久不收，到 PM 建立後取消即可。

- 預設 dry-run，輸出動作摘要（建立／完成／可能已完成／略過筆數，及每筆的 agenda 代碼、優先級、期限）。
- `--json`：機器可讀輸出，供排程與測試。
- 結束碼：0 成功；1 參數或設定錯誤；2 讀不到來源；3 寫入部分失敗。

排程：AI_Copilot 的 `scripts/hi_autorun.sh` 在 daily-agenda 掃完後執行 `sync --apply`（另案改動，不在本 plugin 內）。

## 6. 驗收

- 純函式（篩選、對應、差額計算）有單元測試，涵蓋：新鮮度邊界、無期限、優先級過濾、已存在不重建、done/dismissed 觸發完成、已完成不重複完成、maybe_done 不寫入、external_id 組成、標題截斷。
- HTTP 客戶端以假 fetch 測：成功、`isError`、HTTP 非 200、重試只對 429/502/503/504 與網路錯誤。
- 開發環境實寫兩次：第二次建立數為 0。
- 正式環境 dry-run 清單經使用者確認；首批實寫 3 筆經使用者在 PM 確認後才補其餘。

## Product Flow Approval

- 分類：不新增或改變使用者操作畫面（PM 端沿用既有個人待辦頁；agenda 端不改）。
- Frontend preview：not_applicable（理由：純背景同步，使用者看到的是 PM 既有收件匣畫面）。
- User Story：使用者每天不用手動抄寫，LINE／信件裡別人交代的事會自動出現在 PM 收件匣；agenda 判定做完的事 PM 跟著勾完成。
- 使用者核准：2026-11-10，使用者回覆「1.A 2.照你建議 3.照你建議 4.照你建議」（對應本檔開頭四項）。
- 證據：AI_Copilot session `10e5796e-84fb-4f49-92fc-fe73d0b8abc4`。
