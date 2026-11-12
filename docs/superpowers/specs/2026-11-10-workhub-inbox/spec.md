# workhub-inbox：外部待辦單向同步到 Work Hub 個人待辦（v1：daily-agenda）

> 2026-11-10 起草。使用者拍板：1A（另開 plugin）、2（v1 範圍照建議）、3（/hi 段落先維持讀 agenda）、4（無期限待辦只收 7 天內出現的）。
>
> **2026-11-13 使用者裁決（v0.2.0）**：使用者覺得同一批待辦要確認兩次，改成「在 daily-agenda 彙整頁確認一次」——貼回「確認待辦 R-XXX」、daily-agenda 處理成功後，同一個對話直接跑 `sync --apply --report R-XXX`。拿掉三道保護：(1) 同步要另開新對話；(2) 先試跑、看另一份清單檔才實寫；(3) 正式環境第一次只寫 3 筆。同日再裁決：報表模式的舊待辦「讓使用者自己選，做得有彈性一點」——預設只建立 7 天內的，超過的列出筆數由使用者選這次補不補、要不要存成以後的偏好（`--fresh-days`、`prefs`）。「可能已完成」與收合項目維持不寫入。下文標「2026-11-13 改」的段落以此為準；未標的舊段落保留作為沿革。

## 1. 目的

daily-agenda 每天從 LINE、Outlook 信件、行事曆整理出待辦，目前只寫進自己的 ledger（待辦登記簿）與 /hi 日報段落，沒有出口。
本 plugin 把這些待辦單向同步進 Work Hub（marketing-pm-system）的個人待辦收件匣，讓 PM 系統成為唯一的待辦系統（GTD 收件匣）。
plugin 以「來源轉接器（source adapter）」為單位擴充；v1 只有 `daily-agenda` 一個轉接器，之後錄音、Gmail 各加一個。

## 2. 安全邊界

- daily-agenda 規格 §75「不寫入 workhub」維持不變：daily-agenda 本身仍唯讀。寫入由本 plugin 的獨立腳本負責，只讀 daily-agenda 的整理成品（`CURRENT` generation 的 `ledger.jsonl`＋`state.json`），不讀 `runs/` 的原始訊息。
- 待辦標題是 AI 從第三方訊息整理出的不可信內容。**寫入全程不經 AI**：腳本直接以 HTTP 呼叫 Work Hub 的 MCP 端點（stateless JSON-RPC `tools/call`），標題只當資料傳遞。skill 只負責跑腳本、轉述筆數，不把標題放進任何工具參數。
- 金鑰與端點只從環境變數讀取（`WORKHUB_INBOX_API_KEY`、`WORKHUB_INBOX_ENDPOINT`；命令列不提供端點參數，避免不可信的標題內容誘導 AI 改掉金鑰的去處），不寫檔、不進 log。端點送出前先驗證：只接受 `https:`，`http:` 只放行 127.0.0.1、localhost、[::1]，網址不得含帳號密碼；不跟隨轉址。錯誤訊息只帶錯誤代碼，不帶底層訊息、伺服器的錯誤文字與端點原字串：Work Hub 工具錯誤取 `structuredContent.error.code`（只接受 `^[A-Z][A-Z0-9_]{1,63}$`），JSON-RPC 錯誤取數字代碼，HTTP 錯誤取狀態碼，連線錯誤取錯誤代碼，都沒有時輸出固定字串 `SERVER_ERROR_TEXT_HIDDEN`；輸出前另以金鑰與驗證不過的端點原值做保底遮蔽。plugin 不認個人機器上的其他變數名，由呼叫端（例如排程腳本）對應。標題與來源名稱（含 LINE 對話名稱）不得出現在任何 stdout、stderr 或 `--json` 輸出：Claude 的 Bash 工具讀得到環境變數裡的金鑰，腳本擋不住被說動的模型自己連線，所以要守的邊界是「不把不可信標題送進拿得到金鑰的對話」。人看的試跑清單用 `--review-file` 寫成本機的自足 HTML（`<狀態資料夾>/inbox-review.html`，狀態資料夾預設 `~/.workhub-inbox`、可用 `WORKHUB_INBOX_STATE_DIR` 改；每次覆蓋、先寫暫存檔再改名、權限比照對照檔；內容全部 HTML 跳脫、沒有 script 與外部資源，並以 CSP 禁止載入任何東西），stdout 只印筆數、代碼與檔案路徑。清單檔在送出任何寫入前產生，寫不出來就停止（結束碼 2）。伺服器錯誤文字可能回印送出的標題或說明，所以一律不輸出（上一條的代碼規則），不靠比對標題決定隱藏與否。skill 不得讀清單檔與 daily-agenda 資料夾，由使用者自己打開後用代碼回覆。
- 預設 dry-run（試跑，只列出不寫入）；`--apply` 才寫入，且受 `--max-writes`（預設 20）上限保護。
- **2026-11-13 改**：上一條「不把不可信標題送進拿得到金鑰的對話」的邊界，在 skill 層已依使用者裁決放寬——同步改在看得到彙整內容的同一個對話執行。腳本本身的輸出規則不變（stdout、stderr、`--json` 仍不含標題）。skill 的預設流程直接帶 `--apply --report`，不試跑、不產清單檔、不帶 `--max-writes`；剩下的防線是 skill 規則（標題只當資料、端點只認環境變數）與權限確認視窗。`--review-file` 與試跑保留為使用者要求先看清單時的選用做法，該做法下 skill 仍不得讀清單檔。

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

寫入上限（`--max-writes`）：每個送出的寫入都算一筆，**失敗也算**（回應中斷時伺服器可能已寫入）；只有確認是去重命中才不算。寫入時遇到 401／403 立即停止，結束碼 1：包含 HTTP 401／403，以及工具層回的 `MCP_UNAUTHENTICATED`、`MCP_FORBIDDEN`（金鑰無效或權限不足；Work Hub 把這類錯誤放在 HTTP 200 的 `structuredContent.error`）。其他代碼的單筆拒絕只算該筆失敗。超過上限的項目下次執行再補，摘要會提醒；若持續補不完，超過 7 天新鮮度的會被略過（不做續跑進度，首批大量匯入時調高上限）。

### 本機對照檔

`pm_list_personal_tasks` 每個檢視最多回 500 筆（marketing-pm-system `service.js:1372`），累積久了會查不到舊待辦，勾完成就找不到 `task_id`。因此 `--apply` 時在本機記一份「去重鍵 → 待辦 ID／是否已完成」對照檔：

- 位置：`$WORKHUB_INBOX_STATE_DIR`（預設 `~/.workhub-inbox`）底下的 `scope-<雜湊>/agenda-map.json`，雜湊＝sha256(端點＋金鑰) 前 16 碼（不存金鑰本身）。依「端點＋金鑰」分資料夾，不同環境、不同使用者的待辦 ID 不會混用；換金鑰會從空表開始，伺服器去重仍防重複建立。資料夾 700、檔案 600。
- 清單看得到的待辦每次 `--apply` 都覆寫對照檔（ID 與是否已完成以清單為準）。
- 摘要分開「超過上限延後」（`deferred`）與「金鑰問題中止後未送出」（`notAttempted`）；只有前者會提示 7 天老化。
- 寫法：先寫暫存檔再改名，中途掛掉不留半個檔案；檔案損壞時報錯停止（結束碼 2），不默默當成空表。
- 合併規則：清單查得到的以清單為準；查不到的用對照檔補。
- 已知限制：使用者在 PM 取消、而且已掉出清單範圍的待辦，對照檔仍記為未完成；若 agenda 之後標 done，會對它送一次完成。
- dry-run 只讀不寫對照檔。

### 來源鍵的比對

本機只比對 `external_id` 以 `agenda:` 開頭的待辦，不加 `source`。理由：`agenda:` 命名空間只有本 plugin 寫入；同一筆 agenda 待辦的 `source` 由 `src_ids[0]` 推得，若 agenda 合併來源造成順序改變，用「來源＋鍵」比對反而會判成新的。伺服器端仍以 `source`＋`external_id` 去重。

## 5. 介面

```
node scripts/inbox-sync.js sync --source daily-agenda [--apply] [--max-writes N]
     [--report R-XXX|latest] [--fresh-days N|all] [--priorities P0,P1] [--only T-1234,...|--no-create]
     [--exclude T-1234,...] [--da-home PATH] [--review-file] [--json]
node scripts/inbox-sync.js prefs show [--json] | prefs set fresh-days <N|all> | prefs clear
```

- `--report R-XXX|latest`（2026-11-13 新增）：只建立該報表列出、而且登記簿現在仍是 `open` 的待辦；勾完成不受限。報表對照讀 daily-agenda CURRENT generation 的 `state.json`：`report_code`（`R-` 加 3 碼）與 `report_index`（`{"項次": "T-xxxx"}`，值再以 `^T-\d{4}$` 過濾）。daily-agenda 只保留最新一份報表的對照，所以只認最新一份；格式不符是參數錯誤（結束碼 1），不是最新一份或尚無報表是結束碼 2。彙整頁列出登記簿全部未完成項目，所以報表範圍本身只擋掉舊報表代碼；舊待辦由天數決定（見 `--fresh-days`）。與其他參數：`--only` 取交集、`--exclude` 再扣、`--priorities` 照樣收窄、`--no-create` 不能併用。
- `--fresh-days N|all`（2026-11-13 新增）：只建立期限（沒期限看出現日期）在 N 天內的，`all` 不限；只收 1–3650 的整數或 `all`，否則結束碼 1。優先序：旗標 ＞ 本機偏好（只在報表模式採用）＞ 預設 7 天；不帶 `--report` 時維持原本的 7 天。摘要（文字與 `--json`）列出這次的天數與來源（`freshDays`、`freshDaysSource`＝flag／prefs／default、`prefsSaved`），以及「超過天數沒建立」筆數（`counts.stale`），不含標題。
- `prefs`（2026-11-13 新增）：偏好存 `<狀態資料夾>/prefs.json`（`{"version":1,"freshDays":N|"all"}`，先寫 .tmp 再改名，可重跑）。`show`／`set fresh-days <N|all>`／`clear`。偏好檔損壞時同步退回預設 7 天、照常執行並提示一句（`--json` 的 `prefsError: true`）。skill 在「超過天數沒建立」大於 0 且沒存過偏好時問使用者一次：這次也寫、這次不用、以後都寫、以後都只收 7 天。
- `--no-create`：只勾完成、不建立任何待辦；不能和 `--only`、`--exclude`、`--report` 併用。用來取代「`--only` 給空值」：空值在部分 shell 會被丟掉或引號出錯，萬一變成沒帶 `--only` 就會全部建立，所以 `--only`／`--exclude` 缺值或值為空一律是參數錯誤（結束碼 1、不送任何請求）。
- `--only`／`--exclude`：以 agenda 代碼挑選或排除「建立」的項目（只接受 `T-` 加 4 碼數字），完成同步不受影響。被排除的項目下次執行仍會出現；要永久不收，到 PM 建立後取消即可。

- 預設 dry-run，輸出動作摘要（建立／完成／可能已完成／略過筆數，及每筆的 agenda 代碼、優先級、期限）。
- `--json`：機器可讀輸出，供排程與測試。
- `--review-file`：把會建立與勾完成的項目（動作、代碼、優先級、期限、來源、標題）寫進 `<狀態資料夾>/inbox-review.html`，stdout 只多印這個檔案的路徑；`--json` 則多一個 `reviewFile` 欄位放路徑。可與 `--apply` 併用，清單檔一定在寫入前產生。
- 結束碼：0 成功；1 參數或設定錯誤；2 讀不到來源，或本機對照檔、清單檔讀寫失敗；3 寫入部分失敗。

排程：AI_Copilot 的 `scripts/hi_autorun.sh` 在 daily-agenda 掃完後執行 `sync --apply`（另案改動，不在本 plugin 內）。

## 6. 驗收

- 純函式（篩選、對應、差額計算）有單元測試，涵蓋：新鮮度邊界、無期限、優先級過濾、已存在不重建、done/dismissed 觸發完成、已完成不重複完成、maybe_done 不寫入、external_id 組成、標題截斷。
- HTTP 客戶端以假 fetch 測：成功、`isError`、HTTP 非 200、重試只對 429/502/503/504 與網路錯誤。
- 開發環境實寫兩次：第二次建立數為 0。
- 正式環境 dry-run 清單經使用者確認；首批實寫 3 筆經使用者在 PM 確認後才補其餘。（2026-11-13 改：使用者裁決拿掉這條；確認改在 daily-agenda 彙整頁做一次。）

## Product Flow Approval

- 分類：不新增或改變使用者操作畫面（PM 端沿用既有個人待辦頁；agenda 端不改）。
- Frontend preview：not_applicable（理由：純背景同步，使用者看到的是 PM 既有收件匣畫面）。
- User Story：使用者每天不用手動抄寫，LINE／信件裡別人交代的事會自動出現在 PM 收件匣；agenda 判定做完的事 PM 跟著勾完成。
- 使用者核准：2026-11-10，使用者回覆「1.A 2.照你建議 3.照你建議 4.照你建議」（對應本檔開頭四項）。
- 證據：AI_Copilot session `10e5796e-84fb-4f49-92fc-fe73d0b8abc4`。
- **待使用者確認（實作中新增或偏離原核准）**：
  1. 「可能已完成」由「貼標籤」改為「不寫入、只列筆數」（§4 第 3 條，原因：正式環境沒有替既有待辦加標籤的工具）。
  2. 新增 `--only`／`--exclude`，供首批挑選與排除已過去的事（只限制「建立」）。
  2-1. 2026-11-13 使用者裁決：新增 `--report`，skill 預設流程改為彙整頁確認後直接實寫（見檔首裁決）。「可能已完成」與收合項目仍不寫入；agenda 改優先不同步到已在 PM 的待辦（沒有更新路徑）。報表模式的舊待辦：預設只收 7 天內，由使用者選擇是否補寫與存成偏好（同日裁決）。
  3. `--max-writes` 定義改為「送出的寫入都算、去重命中不算」，並新增本機對照檔（§4）。
