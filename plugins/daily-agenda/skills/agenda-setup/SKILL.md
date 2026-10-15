---
name: agenda-setup
description: daily-agenda 首次設定（第 0–8 步）：資料外送說明與同意、環境檢查、LINE 試讀、自動判定 LINE 帳號資料、串 Outlook、選擇不讀的 LINE 對話、個人資料夾索引、輸出偏好與排程、掃描過去資料建立判斷準則。也處理修改排除對話、重設、解除安裝。觸發：「/agenda-setup」「設定每日彙整」「daily agenda 設定」「改排除的 LINE 對話」「解除安裝 daily-agenda」。
allowed-tools: Bash, Read, Write, AskUserQuestion, Monitor
---

# daily-agenda 首次設定

本 skill 帶使用者完成設定。所有本機動作都透過下面這個指令執行（以下簡稱 `DA`）：

```bash
uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}/scripts" python -m daily_agenda <子指令>
```

每個子指令輸出一個 JSON 物件：`ok`、`code`、`message`（白話）。**失敗時只把 `message` 轉述給使用者，不要貼代碼或原始 JSON。**

## 鐵則

1. **第 0 步同意之前，不得執行任何會讀取 LINE、信件、行事曆的指令**（`probe`、`outlook-preview`、`chats list`、`line-detect`、`trust probe`、`criteria draft`）。
2. 使用者看不懂的技術細節不要問（例如版本雜湊、資料庫檔名）；只在真的需要使用者決定時才問。
3. 每個 AskUserQuestion 卡片要自足：寫清楚背景、選項代表什麼、預設是哪個。
4. 不要自己讀 `runs/` 裡的 bundle 或任何收集到的原始資料；只看指令的摘要輸出。唯一例外：`runs/progress/*.jsonl` 可透過 `DA progress show` 或 `DA progress watch` 讀取（進度檔不含原始資料）。
5. 本 plugin **不會**把 LINE 註冊成你的 MCP 工具，也不會在設定中送出任何訊息。

## 長時間指令的執行模式

以下指令可能執行超過 2 分鐘：`runtime install`（下載 ~100MB）、`trust probe`（試讀 LINE ~11 秒/次）、`criteria draft`。對這些指令：

1. 先建立 progress file（進度檔）：`DA progress new` → 取得 `progress_file` 路徑。
2. 用 Bash 的 `run_in_background: true` 啟動指令，加上 `--progress-file "<路徑>"`：
   ```bash
   uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}/scripts" python -m daily_agenda <子指令> --progress-file "<路徑>"
   ```
3. 用 Monitor 工具追蹤進度（每行一則繁中進度訊息，完成或失敗時自動結束）：
   ```bash
   uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}/scripts" python -m daily_agenda progress watch --file "<路徑>"
   ```
4. 背景指令完成時（harness 通知），**停止 Monitor**（如果還在跑），以背景指令的 stdout JSON 為權威結果（涵蓋 crash 或 kill 後無 terminal event 的情況）。
5. **如果 Monitor 不可用**（fallback，備援）：前景執行 `DA progress watch --file "<路徑>" --max-s 100`（`--max-s` 指定總秒數上限，到期自動結束 exit 3）。若 exit 3，重跑 watch（直到 exit 0 或 1 表示完成/失敗）。背景 Bash 完成通知到達時，停止 watch 並以 stdout JSON 為準。

向使用者轉述進度時，只在階段變化時簡短說一句（例如「正在讀取 LINE 聊天室 35/170」「AI 分析中」），不要每個事件都說。

## 開始前

先跑 `DA consent --status` 與 `DA config get`。如果已經完成過設定，問使用者要「從頭重設」「只改排除的 LINE 對話（跳到第 4 步）」「只改輸出或排程（跳到第 6 步）」「重建判斷準則（跳到第 7 步）」「解除安裝」。

## 第 0 步：資料外送說明與同意（US-0）

先告訴使用者預估時間：「互動設定大約 10 分鐘；第一次會下載約 100MB 的執行元件，視網路速度另需幾分鐘。」

然後用 AskUserQuestion 顯示以下說明（原文照貼，不要刪減）並請使用者選擇：

> 這個工具每天會讀取你的 Outlook 信件、Outlook 行事曆、LINE 電腦版對話，交給 Claude（雲端 AI）分析後整理出待辦與行程。
> - 信件、行事曆、LINE 的內容（包含客戶名稱、業務內容）會**原樣**送到 Claude 分析，不做遮蔽。
> - 你可以在下一步之後選擇「不讀」某些 LINE 對話（例如家人群組）；被排除的對話完全不會被讀取。
> - 回 LINE、建立行事曆事件這類對外動作，每一次都會先給你看完整內容，你按允許才會執行。
> - 請依公司使用的 Claude 方案條款，確認可以這樣使用。

選項：「同意，繼續設定」／「不同意，結束」。

- 同意 → `DA consent --accept`。
- 不同意 → 告訴使用者「已結束設定，沒有讀取任何資料。之後想用再執行 /agenda-setup」，**結束 skill**。

## 第 1 步：環境檢查（US-1）

1. `DA envcheck`。逐項把 `ok:false` 的 `message` 告訴使用者（裡面已有補救指令）。
   - `NOT_WINDOWS` → 說明只支援 Windows 原生 Claude Code，結束。
   - `CLAUDE_FLAGS_MISSING` → 請使用者更新 Claude Code 後重跑，結束。
   - 缺 Node／uv → 給指令，請使用者裝好後說「好了」，再重跑 `DA envcheck`。
   - `LINE_NOT_RUNNING` → 請使用者打開 LINE 並登入；若使用者說不用 LINE，記下「只用 Outlook」，跳到第 3 步。
2. `DA runtime install`（下載並驗證執行元件；可能要幾分鐘）。**使用長時間指令的執行模式**：先 `DA progress new`，再背景執行 + Monitor 追蹤，告訴使用者「正在下載約 100MB 的執行元件，請稍候」。失敗時轉述 `message`；雜湊不符一律停止，不要嘗試繞過。

## 第 2 步：LINE 試讀與帳號偵測（US-1, US-2）

用 AskUserQuestion 問一句：「要試讀一次 LINE，確認讀得到嗎？（會檢查 LINE 程式的數位簽章，再試讀一則對話資訊，不會送出任何東西）」選項「好（建議）」／「先跳過 LINE，只用 Outlook」。

- 好 → `DA trust probe`。**使用長時間指令的執行模式**（trust probe 每個 LINE 資料庫試讀約 11 秒）。
  - `trusted`／`already_trusted` → 告訴使用者「已自動找到你目前登入的 LINE 資料，並確認讀得到」，繼續第 3 步。
  - `MULTIPLE_DATABASES_OK` → 這種情況少見。用 AskUserQuestion 列出 `candidates`（顯示「最後更新時間」，不要顯示檔名以外的技術細節），說明「這台電腦有多份 LINE 資料都讀得到，請選最近在用的那一份（通常是最後更新時間最新的）」，然後 `DA line-select --database <候選的 name>`，再重跑一次 `DA trust probe`。
  - `LINE_SIGNATURE_INVALID` → 轉述；LINE 改為不使用，跳到第 3 步。
  - 其他代碼（例如 `SESSION_KEY_UNAVAILABLE`）→ 轉述 `message`（例如「請打開 LINE 視窗並確認已登入」），請使用者處理後說「好了」再試一次；第二次仍失敗就問要不要先只用 Outlook。

## 第 3 步：串 Outlook（US-3）

1. 先確認使用者已安裝並登入 `df-graph`：`DA probe`，看 `sources.mail`。
   - `DF_GRAPH_NOT_REGISTERED`／`DF_GRAPH_AUTH` → 請使用者執行 `/df-graph-setup`（那是另一個 plugin 的設定流程，會開瀏覽器登入一次），完成後回來說「好了」，再跑一次 `DA probe`（本工具直接啟動 df-graph，不需要重啟 Claude Code）。
   - `DF_GRAPH_TOO_OLD` → 請使用者更新 df-graph plugin。
2. 用白話告訴使用者權限：「df-graph 登入時取得的權限包含讀信、寄信、修改行事曆；本工具平常只讀取，只有在你按允許後才會建立行事曆事件。」
3. `DA outlook-preview`，把最近 3 封信的主旨與寄件者、今天的行程列給使用者看。
4. 用 AskUserQuestion：「上面是你最近的信與今天的行程，看起來正確嗎？」選項「對（預設，直接選這個）」／「不對」。不對 → 請使用者描述，協助排查（通常是登入了別的帳號 → 重跑 `/df-graph-setup`）。

## 第 4 步：選擇不讀的 LINE 對話（US-12）

（沒有使用 LINE 就跳過。）

1. `DA chats list --days 30`。
2. 告訴使用者：「以下是最近 30 天有訊息的 LINE 對話（共 N 個）。勾選你**不想被讀取**的對話（例如家人群組、私人對話）。之後新出現的對話預設會讀，並會在彙整檔開頭提醒你；隨時可以再改。」
3. 對話多時，用多個 AskUserQuestion（multiSelect，每張最多 4 個選項不夠用時）——建議做法：先把清單用編號列在回覆正文，再請使用者直接打編號（例如「3, 7, 12」或「都不排除」），比卡片更快。
4. `DA exclude --set <選到的 ref ...>`（沒選就 `DA exclude --set`）。回報「已排除 N 個對話」。

## 第 5 步：個人資料夾（US-4）

AskUserQuestion：「你有沒有放個人工作背景的資料夾（例如筆記、專案說明）？有的話我會建立索引（只記錄檔名、標題、摘要，不會搬動或複製原檔），讓 AI 判斷待辦時更了解你的工作。」選項「有，我告訴你路徑」／「沒有，跳過」。

- 有 → 請使用者給路徑 → `DA ctx index --dir "<路徑>"`，回報檔案數。
- 沒有 → `DA ctx index --skip`。

## 第 6 步：輸出偏好與排程（US-5）

依序問（每張卡片自足）：

1. **格式**：「彙整檔要哪一種格式？」選項「網頁 html（建議，排版較好讀）」「Markdown md」「兩種都要」。
2. **存放位置**：先用 `pwd` 取得目前資料夾，預設是 `<目前資料夾>\daily-agenda`，在卡片中顯示**完整路徑**。選項「用這個位置（建議）」「我要指定別的資料夾」。
3. **判斷準則修正頻率**：先解釋「判斷準則＝AI 決定哪些訊息算待辦、排多優先、哪些略過的規則；用一陣子後根據你的回饋調整會更準。」選項「每 10 次或 14 天問我一次（建議）」「每 5 次或 7 天」「每 20 次或 30 天」。
4. **排程**：「要不要每天自動產生彙整？（Windows 排程；只在你登入電腦時執行；需要你回答的事會留到你手動執行時處理）」選項「不用，我自己打 /agenda（建議先這樣）」「要，每天早上 8:30」「要，我指定時間」。

寫入：`DA config set output.formats=<…> output.dir="<…>" refine.every_runs=<…> refine.every_days=<…>`；要排程則 `DA schedule install --time HH:MM`。

## 第 7 步：建立判斷準則第一版（US-6）

1. AskUserQuestion：「要掃描多久以前的資料來建立你的判斷準則？」選項「7 天（最快）」「14 天（建議）」「30 天（LINE 一次最多可讀取的天數，較完整但較慢）」。
2. 告訴使用者「正在掃描過去 N 天的資料，由 AI 分析並建立判斷準則。14 天大約需要 3–5 分鐘，30 天可能更久。」
3. **使用長時間指令的執行模式**：先 `DA progress new`，再背景執行 `DA criteria draft --days N --progress-file "<路徑>"`，用 Monitor 追蹤 `DA progress watch --file "<路徑>"`。
4. 讀取輸出的 `draft_path`（這是 AI 整理出的準則草稿，不是原始資料），把全文給使用者看，問「要直接採用、還是要修改哪裡？」。
5. 依使用者意見修改草稿（用 Write 寫到同一個 draft_path），直到使用者說同意。
6. `DA criteria save --file "<draft_path>"` → 回報「判斷準則第 1 版已儲存」。

## 完成

告訴使用者：
- 「設定完成。之後每天打 `/agenda` 就會產生彙整檔，存在 <output.dir>。」
- 「想改排除的對話、輸出位置或排程，再執行 `/agenda-setup` 即可。」

## 解除安裝

使用者要求解除安裝時：說明「會移除排程；你的彙整檔不會被刪」，再問「要不要一併刪除本工具在你電腦上保存的設定與紀錄（`%USERPROFILE%\.daily-agenda`）？」→ `DA uninstall` 或 `DA uninstall --purge`。
