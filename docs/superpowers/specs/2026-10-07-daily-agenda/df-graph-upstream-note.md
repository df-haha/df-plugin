# df-graph 修改說明（草稿，尚未寄出）

宗霖你好，

我們在做一個新的小工具（daily-agenda），需要在 df-graph 上加三個小功能，都是選用參數，預設值不加就完全不影響現有行為，想跟你說明一下改了什麼、為什麼，之後方便你看要不要合回主線。

## 為什麼需要這三個改動

1. **信件穩定識別**：daily-agenda 會追蹤同一封信（例如主管的日報信），但信件如果被搬到別的資料夾，Graph 原本給的 id 會跟著變，程式會認不出是同一封信、造成重複處理。Graph 另外提供一種「immutable id（不會變的 id）」機制，搬資料夾也不會變；同時每封信都有一個全球唯一的 Internet-Message-Id（就是信件標頭裡的 Message-ID），也可以拿來當防重複的依據。
2. **行事曆建立防重複**：daily-agenda 在幫忙建行事曆活動時，如果網路逾時、程式重跑等情況，可能會不小心把同一個活動建兩次。Graph 的事件資料本身就有一個 `transactionId` 欄位，專門給呼叫端設一個自訂值，重複送同一個值時 Graph 會認得是同一次操作、不會重複建立。

## 改了什麼（三處）

**1. 信件工具加 `immutable_ids` 選用參數**（`mail_list_recent`、`mail_get`、`mail_search`）
- 預設 `False`，行為跟現在完全一樣。
- 設成 `True` 時：
  - 呼叫 Graph 時會多帶一個 `Prefer: IdType="ImmutableId"` 的請求表頭，告訴 Graph 之後都用不會變的 id 格式。
  - 回傳的清單（`mail_list_recent`／`mail_search`）每一項會多一個 `internet_message_id` 欄位；`mail_get` 的文字輸出會在 `Date:` 那行下面多一行 `Internet-Message-Id: <值>`。
  - 翻頁（信很多時 Graph 會分好幾頁回）的每一頁都會確實帶上同樣的表頭。
  - `mail_get` 在讀有附件的信、要另外去查附件清單時，也會帶上同樣的表頭，確保附件查詢用的 id 格式一致。

**2. 行事曆建立加 `transaction_id` 選用參數**（`calendar_create`）
- 預設空字串，不帶這個欄位，請求內容跟現在完全一樣。
- 給了值的話，會把這個值放進建立活動的請求裡（`transactionId` 欄位），重送同一個值 Graph 不會重複建立。

## 測試涵蓋範圍

新增 14 項測試（原本 88 項全部維持通過，其中 1 項既有測試會因為本機 `~/.ssh/authorized_keys` 真的存在而失敗，跟這次改動無關，之前就是這樣）：

- 三個信件工具在**預設呼叫**時：確認請求表頭不含 `IdType`、回傳內容不含新欄位，其中一項直接整段文字比對輸出，確保一字不差。
- `immutable_ids=True` 時：表頭正確、`$select` 有多帶欄位、回傳內容含新欄位，翻頁的每一頁都有正確表頭，附件查詢也帶表頭。
- `calendar_create` 預設不帶 `transactionId`、給值時正確帶入、空字串時維持原樣。
- 伺服器端（MCP 工具定義）的參數簽名確認新參數存在且預設值正確。

## 修改的檔案

- `plugins/df-graph/server/mailtools.py`
- `plugins/df-graph/server/caltools.py`
- `plugins/df-graph/server/server.py`
- `plugins/df-graph/server/test_tools.py`
- `plugins/df-graph/.claude-plugin/plugin.json`（版本號 0.1.0 → 0.2.0）

## 建議的合併方式

這幾處都是加選用參數、沒有動到既有的函式簽名順序或預設行為，應該可以直接合進主線；合併前麻煩重新跑一次 `test_tools.py` 確認在你那邊環境也全綠。
