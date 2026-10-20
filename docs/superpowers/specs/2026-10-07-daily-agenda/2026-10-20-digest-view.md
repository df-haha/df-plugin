# 彙整頁改版：LINE 對話整理＋信件整理＋就地確認待辦（2026-10-20）

> 補充 `spec.md`／`impl-contracts.md`。本文件的「契約」段是實作的唯一依據；與舊文件衝突時以本文件為準。

## 背景

使用者在 WSL 實跑後說：「呈現的 html 我覺得不行」。原頁面只有一整串待辦＋行事曆＋略過清單，看不出各群組在聊什麼，沒變成待辦的訊息等於看不到。

## AS-IS

`/agenda` → 一頁 HTML：來源狀態 → 混合待辦（P0/P1/P2、可能已完成）→ 行事曆 → 略過。確認待辦要在對話打「R-xxx 的 3 做完了」。

## TO-BE

頁面三區：① LINE 對話整理（本期有新訊息的每個對話一張卡；閒聊類收合但可展開看統整）② 信件整理（依資料夾分組、需回覆置頂）③ 其他待辦（之前累積的未完成、AI 認為可能已完成）。建議待辦就放在所屬對話卡右側／所屬信件下方，就地選保留／完成／刪除／改優先；最下方確認列彙總全頁變更，產生確認文字貼回 `/agenda`。每次分析後，各對話的 AI 整理存進本機 SQLite（不存原文），下次分析時餵給 AI 當脈絡，卡片上可展開看過往重點。版面以桌機為主，加寬並自適應。

## User Stories（已核准）

- US-1 群組簡報：一張卡看到某 LINE 對話這段期間講了什麼（3–5 行）。**補充**：沒有需要處理的對話也要有簡短統整，預設收合、可展開。
- US-2 不漏訊息：卡上標出可能需要我回覆／處理的訊息（誰、何時、要回什麼）；沒有待處理的卡明確標「已看過，無需處理」。
- US-3（改）就地確認：卡片右側是這個對話的建議待辦，每項可直接選保留／完成／刪除、改優先序。
- US-4 群組脈絡：卡片可展開看該群組過去幾次整理；AI 分析時參考這些歷史。
- US-5 信件整理：依資料夾分組，每封信顯示寄件人、主旨、一兩行摘要、是否需回覆；需回覆排前面。
- US-6（改）確認流程：信件的建議待辦附在該信下方確認；③ 只放之前累積的未完成待辦與「可能已完成」；確認列彙總全頁變更產生確認文字，貼回 `/agenda` 生效。
- US-7 隱私：SQLite 只存 AI 整理後文字與訊息時間範圍，不存 LINE／信件原文；被排除的對話不出現在任何地方。

## Product Flow Approval

- User Stories status: approved
- User Stories approved by: user
- User Stories approved at: 2026-10-20
- User Stories approval evidence: 使用者回「核准」（US-1..7）；US-3/US-6 依「每則訊息的建議待辦 這樣調整還要跳來跳去 直接在每個群組旁邊能做相關確認吧」改寫；US-1 補充依「閒聊的內容 雖然收闔 但是也可以打開 看統整」
- Frontend preview: required
- Preview artifact: docs/superpowers/specs/2026-10-07-daily-agenda/previews/2026-10-20-digest-view.html
- Flow approval status: approved
- Flow approval evidence: 「可以 直接執行一次看看」；附帶「版面寬度可以再寬一點 做自適應」「基本上應該都是在電腦上跑與檢視」（桌機優先）

## 拍板決定（使用者 2026-10-20）

| 題目 | 決定 |
|---|---|
| 哪些對話做卡 | 本期有新訊息的都做（群組＋一對一） |
| 確認方式 | 頁面勾選 → 產生文字 → 貼回 /agenda 對話 |
| SQLite 內容 | 只存 AI 整理，不存原文 |
| 歷史用途 | 餵給 AI 當脈絡，頁面也可看 |
| 預設一併做 | 單批分析輸出無效時自動重試一次；版本 0.0.1-dev → 0.0.2-dev（v1/v2 同版號導致市集快取不更新） |

## 契約

### C1 模型輸出 v2（`templates/analyze-prompt.md`）

```json
{
  "schema_version": 2,
  "items": [ /* 與 v1 相同：new / update / maybe_done / skip */ ],
  "chat_digests": [
    {"chat_ref": "chat:…", "bullets": ["…", "…"], "needs_reply": [{"src_id": "…", "ask": "…"}], "quiet": false}
  ],
  "mail_digests": [
    {"src_id": "…", "category": "reply|action|fyi", "summary": "…"}
  ]
}
```

- 模型**不輸出**名稱、寄件人、時間；render 依 `chat_ref`／`src_id` 從 bundle 記錄查。
- `chat_digests`：本批每個有 `line_message` 的 `chat_ref` 都要一筆（含閒聊，`quiet: true`）。`bullets` 1–5 條、每條 ≤ 80 字。`needs_reply` 的 `ask` ≤ 80 字。
- `mail_digests`：只列值得注意的信（reply／action／fyi）；其餘不列＝視為略過。`summary` ≤ 120 字。
- 驗證（`validate.py`）：`items` 規則不變（結構錯誤仍整批無效）。digest 層**寬鬆**：`chat_ref` 不在本批、`src_id` 不在本批、`needs_reply` 指向 `is_self` 訊息、類別不合法、超長 → 丟掉該筆（或截斷字串），不讓整次失敗；`chat_digests`／`mail_digests` 缺少或不是陣列＝當作空陣列。
- 單批重試：某批 `run_analyzer` 拋 `MODEL_OUTPUT_INVALID`（JSON 解析不了或 `items` 不是陣列）→ 同批重跑一次；第二次仍失敗才整次失敗。

### C2 跨批合併

同 `chat_ref` 出現在多批（只在單一聊天室超過批次大小時發生）：`bullets` 依批次順序串接後截到 5 條；`needs_reply` 依 `src_id` 去重聯集；`quiet` = 全部為 true 才 true。`mail_digests` 依 `src_id` 去重（後者覆蓋前者）。

### C3 view model（`render.build_view`）

**只增不減**：保留所有舊 key（`today`、`maybe_done`、`collapsed`、`events`、`skipped`、`sources`、`report_index`…），`agenda.md.j2` 照舊可渲染。新增：

- `chats`：需要處理或有建議待辦的對話卡，排序＝待回數多→有建議待辦→其他；每卡 `{name, kind_label, msg_count, needs_reply:[{who, when, ask}], bullets, todos:[todo_view], history:[{date, bullets}], pulse:{dots:[{x: float, reply: bool}], from, to}, state: needs|todo|seen}`。
- `quiet_chats`：`quiet: true` 且無建議待辦的對話 `{name, kind_label, msg_count, summary}`（summary＝bullets 串接）。
- `mail_folders`：`[{folder_label, mails:[{from, when, subject, category, summary, todos:[todo_view]}]}]`，資料夾內 reply→action→fyi 排序。
- `other_todos`：open 待辦中不屬於本次任何對話卡或信件的；`maybe_done` 照舊另列。
- 待辦歸屬：`new`／`update` 待辦的 `src_ids` 第一個能對應到本次 `line_message` 的 `chat_ref` → 歸該卡；對應到本次 `mail` → 歸該信；都不行 → `other_todos`。編號沿用 `report_index`（全頁唯一）。
- `pulse.dots[].x` 一律 Python 算出的 float（0–800），**任何不可信字串都不能進 `style=`、`data-*`、SVG 屬性**；`aria-label` 只用數字組成的固定句型。

### C4 歷史紀錄 SQLite（`<home>/history.sqlite`）

- 表 `chat_digest(chat_ref TEXT, run_id TEXT, created_at TEXT, period_from TEXT, period_to TEXT, chat_name TEXT, bullets_json TEXT, needs_reply_json TEXT, quiet INTEGER, PRIMARY KEY(chat_ref, run_id))`。只存 C1 的 AI 整理＋名稱＋期間，**不存原文**。
- 寫入時機：`state.commit_generation` 成功之後（失敗的 run 不寫）。非 Windows 檔案權限 0600。
- 讀取：view 的 `history` 取該 `chat_ref` 最近 3 筆（不含本次）。B 段：寫 bundle 時，對本批出現的每個 `chat_ref` 加一筆 `{"kind": "chat_history", "chat_ref": …, "untrusted": {"entries": [{"date", "bullets"}]}}`（最多 3 筆 × 5 條），`analyze-prompt.md` 說明它是過往 AI 整理、屬不可信資料。
- `exclude --add <ref>` 刪除該 `chat_ref` 所有列；`uninstall` 刪除整個檔案。
- SQL 一律參數化。

### C5 確認指令（C 段）

- 頁面產生的文字格式（固定）：
  ```
  確認待辦 R-XXX
  完成：1、8
  刪除：6
  改優先：3→P0
  其餘保留
  ```
- `DA confirm --from-stdin`：Python 決定性解析（`：`/`:`、`、`/`,`、`→`/`->` 都接受），報表代碼不是最新 → `STALE_REPORT`（同 `mark-done`）；完成＝mark-done、刪除＝dismiss、改優先＝新的 ledger 操作 `set_priority`。輸出每項結果（編號、待辦代碼、標題、動作）。
- `/agenda` skill B 段：使用者貼上以「確認待辦」開頭的文字 → 原文交給 `DA confirm --from-stdin`，不得自行拆解。

### C6 頁面（`templates/agenda.html.j2`）

- 依預覽 `previews/2026-10-20-digest-view.html` 的結構與 tokens；桌機優先：外框最大寬度約 1560px，文字欄每行上限約 40 個中文字，多出的寬度給待辦欄（`clamp(300px, 26%, 400px)`）與信件區（寬螢幕資料夾可兩欄）；手機僅保證不破版。
- CSP：`script-src 'sha256-…'`，script 是 Python 常數（單行或固定內容），每次的資料（報表代碼）用 `data-*` 屬性傳入（值只能是 `R-[A-Z0-9]{3}`）。測試：讀回 publish 後的檔案 **bytes** 重算雜湊，與 CSP 一致；輸出中只有這一個 `<script`。
- 測試必含惡意名稱：`</script>`、`"onerror=`、`{{ }}`、`<svg onload=` 出現在對話名稱／寄件人／摘要時，輸出仍只有一個 `<script` 且被正確跳脫。

## 不做

- 不改 LINE／Outlook 收集邏輯、不改 vendor、不改上鎖機制（C 段若 `confirm` 需要進白名單再個別處理）。
- 不存原文、不開本機伺服器。
