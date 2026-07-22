---
name: deep-research-ryan
description: "多階段深度研究引擎：自動調度 subagent 完成廣度掃描→深度搜索→多輪辯論→報告合成→引用驗證→三閘門品質檢查，產出分析報告＋行動手冊。支援 8 大研究類型，跨平台（Windows/macOS/Linux）。大量消耗額度，建議額度充足時執行。觸發詞：『deep-research』『深度研究 [主題]』『幫我研究 [主題]』『分析可行性』。"
---

# Deep Research v2

基於 upstream v.260625 跨平台版。

## 跨平台相容性

本技能已用 Claude Code 內建工具（Grep/Glob/Read/Write/WebFetch）取代 POSIX-only shell 命令，Windows/macOS/Linux 皆可執行。

- **前置需求**：
  1. **Claude Code CLI**（Windows 10 1809+ / macOS / Linux）— 用 `npm install -g @anthropic-ai/claude-code`（Anthropic 官方 npm 分發），或依 [Claude Code 官方 setup 文件](https://code.claude.com/docs/en/setup) 指示安裝
  2. **Node.js LTS**（含 npx，跑 Exa/Tavily MCP 用）
  3. **Python 3.9+**（部分內建腳本用；Windows 標準命令名為 `python` 或 `py -3`，macOS/Linux 用 `python3`）
  4. **Git**（強烈建議但非必需）
- **路徑表示**：本文件範例路徑用 `~/.claude/...`（macOS/Linux 樣式）**與** `%USERPROFILE%\.claude\...`（Windows 樣式）示意兩平台對應位置。**實際派 subagent / 呼叫 Claude Code 內建工具（Read/Grep/Glob）時必須先展開為絕對路徑**（`~` / `%USERPROFILE%` 是 shell 展開符號、Read/Grep/Glob 工具契約不展開；Bash tool 對 `~` 通常會展開但 Windows PowerShell / CMD 未必）——展開方法見 `references/agent-config.md` §5（用 `python -c "import os; print(os.path.expanduser('~'))"` 拿 `{home}` 再拼路徑）
- **subagent 自律規則**：本技能已消除主流程 POSIX 命令，但 subagent LLM 若自發想跑 `grep`/`awk`/`sed`，PowerShell 環境會失敗。subagent 應**優先用 Claude Code 內建工具**（Grep/Glob/Read）；prompt 模板已改寫，主對話發任務時遵守即可

**環境診斷**：跑 `python <本技能目錄>\scripts\doctor.py`（Windows）／`python3 <本技能目錄>/scripts/doctor.py`（macOS/Linux）驗環境。腳本會檢查 Python 版本、Node.js、Claude Code CLI，並印本次驗到什麼、缺什麼。

---

## 概覽

啟動深度研究引擎：與用戶完成一次對話收集所有細節後，完全自動調度 subagents 執行多階段研究，最終產出**分析報告 + 行動手冊**，包含所有資料來源、信心評級、決策建議、供應商優先度、分階段路線圖，無需用戶在過程中確認任何步驟。研究完成後自動執行 Quality Gate 自檢，確保報告品質達標。

### 版本沿革摘要

本版（v2）整合了多輪辯論＋Judge 仲裁、資料點級評級、LLM-as-judge 三閘門品質檢查、Workflow 編排模式、投資決策/GTM 行銷維度等全部歷史演進。
完整版本史見 `references/CHANGELOG.md`。

---

## 啟動提示（每次啟動時第一步）

技能觸發後，**在任何檢查或提問之前**，先用 AskUserQuestion 顯示以下提示。

> ⚠️ AskUserQuestion 的 question 欄位**不支援換行**，所有文字會渲染為單一段落。
> 下方文字已針對此限制優化，直接複製使用即可。
> ⚠️ AskUserQuestion 單題 options **嚴格 ≤ 4**（工具 zod schema 上限，超過直接 InputValidationError 整題重來）；選項超過 4 個必須拆成兩題問（實測兩次踩中此上限）。
> ⚠️ 引用的 URL 欄必須是**完整可點 inline URL**，不是 domain / 出處名 / L1-L6 等級標籤——LLM rubric 的 completeness 只認 inline URL 可點擊性（實測案例：曾發生 L 標齊全但 inline URL 僅 1 條導致 completeness 跌至 0.55，補齊 URL 後回 0.78）；QG C 閘門 URL 覆蓋率須計「inline 完整 URL」而非 L 標籤。

AskUserQuestion 參數：

```
header: "Deep Research"
question: "🔬 Deep Research v2 — 多階段深度研究引擎，自動調度 subagent 完成廣度掃描→深度搜索→多輪辯論→報告合成→引用驗證→三閘門品質檢查。⚠️ Token 用量提醒：此流程會大量消耗額度，建議在額度充足時執行（技能支援斷點續作，但一次跑完體驗最佳）。是否開始？"
options:
  - label: "了解，開始研究"
    description: "進入前置需求檢查，確認搜索工具可用後開始研究流程"
  - label: "退出"
    description: "結束技能，不產生任何檔案"
```

若用戶選擇退出，直接結束，不執行後續步驟。

---

## 前置需求檢查（每次啟動時自動執行）

技能啟動後、進入 Phase 0 之前，**必須先執行以下檢查**。

### 工具清單

本技能使用的所有工具。核心原則：**不依賴單一來源**，每個維度至少跨 2 個不同來源（內建 + MCP），確保任一 MCP 失效時研究仍可完成。

#### 內建工具（永遠可用，基礎保障）

| 工具 | 來源 | 說明 |
|------|------|------|
| WebSearch | Claude Code 內建 | 最即時的通用搜尋，無限制 |
| WebFetch | Claude Code 內建 | URL 內容擷取，無限制 |
| Task (subagent) | Claude Code 內建 | 並行任務調度 |
| Write / Read / Edit | Claude Code 內建 | 檔案讀寫 |
| Bash (curl) | Claude Code 內建 | Registry API 調用、GitHub Raw 取得 |
| r.jina.ai | 免費公開服務（透過 WebFetch） | WebFetch 失敗時的備援爬取 |

#### Playwright MCP Server（瀏覽器自動化，終極爬取手段）

| 工具 | 用途 |
|------|------|
| **browser_navigate** | 導航到 URL（能渲染 JS、繞過 bot check） |
| **browser_snapshot** | 取得頁面可存取性快照（比截圖更有用） |
| **browser_evaluate** | 在頁面執行 JavaScript 提取資料 |
| **browser_run_code** | 執行 Playwright 腳本（批量操作） |
| **browser_close** | 關閉瀏覽器（**用完必關**） |

#### Exa MCP Server（語意搜尋 + 專項搜尋，全域併發預算：/search 10 QPS / /contents 100 QPS，為 API key 層級共用額度）

| 工具 | 用途 |
|------|------|
| **web_search_exa** | 基本語意搜尋 |
| **web_search_advanced_exa** | 進階搜尋（日期/域名/分類篩選、高亮、摘要） |
| **company_research_exa** | 公司結構化資料（財務、人力、競爭者） |
| **people_search_exa** | 人物結構化資料（職涯、教育） |
| **get_code_context_exa** | 程式碼/技術文件搜尋（GitHub、SO、官方文件） |
| **crawling_exa** | 單一 URL 內容擷取（WebFetch 備援） |
| **deep_researcher_start** | 啟動 AI 深度預研究（回傳 researchId） |
| **deep_researcher_check** | 查詢深度預研究結果（需輪詢至 completed） |

#### Tavily MCP Server（第三搜尋引擎 + 批次爬取，需 API Key）

| 工具 | 用途 |
|------|------|
| **tavily_search** | 網頁搜尋（交叉驗證用第三引擎） |
| **tavily_extract** | 批次 URL 擷取（支援 LinkedIn/受保護網站） |
| **tavily_crawl** | 網站多頁爬取（文件站完整爬取） |
| **tavily_map** | 網站結構映射（取得 URL 列表） |
| **tavily_research** | 同步深度研究（mini/pro/auto） |

### 檢查步驟

確認以下搜索工具可用，記錄各 MCP 狀態：

1. **檢查 `WebSearch` 是否可用**（內建，必須可用）
2. **檢查 `web_search_exa` 是否可用**（確認 Exa MCP Server 已連線）
3. **檢查 `tavily_search` 是否可用**（確認 Tavily MCP Server 已連線）
4. **檢查 `browser_navigate` 是否可用**（確認 Playwright MCP Server 已連線）

**四組全部可用** → 直接進入 Phase 0。

**任一 MCP 不可用** → 向用戶顯示以下提示，並用 AskUserQuestion 詢問：

```
⚠️ MCP 可用性檢查結果

Exa MCP：{✅ 可用 / ❌ 不可用}
Tavily MCP：{✅ 可用 / ❌ 不可用}
Playwright MCP：{✅ 可用 / ❌ 不可用}

此技能使用 4 組資料來源（內建 + Exa + Tavily + Playwright）進行多層次擷取，
確保任一來源失效時研究仍可完成。

缺少 MCP 時的影響：
- 搜尋引擎從 3 組降為 {N} 組，交叉驗證能力下降
- 若 Exa 缺失：無語意搜索、公司/人物結構化資料、程式碼搜尋
- 若 Tavily 缺失：無批次 URL 擷取、網站爬取、第三引擎交叉驗證
- 若 Playwright 缺失：無法爬取 SPA/受保護頁面（Vercel、Cloudflare 等 bot check）
- 技能仍可運行（內建降級邏輯），但報告品質與韌性下降
```

AskUserQuestion 選項：
- **繼續研究（不安裝）**：「以現有工具執行，接受降級」
- **幫我安裝缺少的 MCP**：「引導我完成安裝」
- **停止，我自己處理**：「結束技能，讓我自行安裝」

### 若用戶選擇安裝

#### 安裝 Exa（免費，不需 API Key，建議必裝）

```bash
claude mcp add --scope user --transport stdio exa npx -y mcp-remote https://mcp.exa.ai/mcp
```

#### 安裝 Tavily（需要免費 API Key）

**Step 1**：前往 https://app.tavily.com/sign-in 註冊，取得 API Key
**Step 2**：安裝（將 `tvly-xxxxx` 替換為實際 API Key）
```bash
claude mcp add --scope user tavily -e TAVILY_API_KEY=tvly-xxxxx -- npx -y tavily-mcp@latest
```

安裝後需**關閉並重新開啟 Claude Code**，重新觸發本技能（說『深度研究』或用 /deep-research-ryan 技能名）。

### 若用戶選擇不安裝

在 MANIFEST 中記錄各 MCP 狀態，正常進入 Phase 0。後續 subagent 的工具策略自動根據可用工具調整降級鏈。

---

## SKILL_DIR 解析

主對話啟動技能時解析一次 `SKILL_DIR`（絕對路徑），寫入 MANIFEST，後續所有 subagent prompt / Workflow args 用解析後的絕對路徑。解析鏈：

1. **首選 `${CLAUDE_SKILL_DIR}`**（Claude Code v2.1.169+ 官方提供，skill 層級、SKILL.md 內可用，plugin 與 standalone 兩種安裝形態皆直接指向本 skill 目錄）
2. **次選 `${CLAUDE_PLUGIN_ROOT}/skills/deep-research-ryan`**（plugin 安裝形態、較舊版本 Claude Code；CLAUDE_PLUGIN_ROOT 指向 plugin 安裝根目錄）
3. **Standalone fallback**（~/.claude/skills/ 手動安裝且無上述變數）：跑 `python ${skill}/scripts/find_skill_dir.py` 語意排序取最新
4. 全部失敗 → 問使用者要路徑

解析後的絕對路徑用於所有 `references/` 引用、scripts/ 呼叫、Workflow scriptPath。

---

## 編排模式自動切換

前置需求檢查完成後、進入 Phase 0 之前，**主對話必須執行一次模式偵測**並將結果寫入 MANIFEST，後續 Synthesis 階段依此分流。

### 偵測規則

主對話讀取**當前對話的最近 system-reminder 訊息**，搜尋是否存在精確字串 `Ultracode is on`：

| 偵測結果 | 啟用模式 | 寫入 MANIFEST |
|---|---|---|
| 找到 `Ultracode is on` | Workflow 模式 | `orchestration_mode: workflow` |
| 找不到 | Task 模式（預設） | `orchestration_mode: task` |
| Workflow tool 不可用（環境無此工具） | 強制 Task 模式 | `orchestration_mode: task`、加註 `workflow_unavailable: true` |

**偵測時機**：技能啟動後第一次有機會分流前（前置檢查完成後立即執行），結果鎖定整個研究流程不再變更（避免中途切換造成狀態不一致）。

### 兩種模式對照

| 項目 | Task 模式（預設） | Workflow 模式（ultracode） |
|---|---|---|
| **Phase 1 Discovery** | 主對話 Task tool 派 4-8 個 subagent | **同左**（這段不換） |
| **Gap Analysis** | 主對話 Task tool 派 1 個 subagent | **同左** |
| **Phase 2 Deep Search** | 主對話 Task tool 派 3-6 個 subagent | **同左** |
| **Devil's Advocate 多輪 + Judge** | 主對話 Task tool 編排 | **同左**（streaming 對辯論可見性有幫助） |
| **Conflict Detection + Resolution** | 主對話 Task tool 派 subagent | **同左** |
| **Synthesis（2-3 並行）** | 主對話 Task tool 編排 | **Workflow `parallel()` + schema 強制** |
| **Citation Verify** | 主對話讀 PASS/WARN/FAIL 決定下一步 | **Workflow `while` loop 自動重跑（最多 2 輪）** |
| **Quality Gate 三閘門** | 主對話 prose 編排 | **Workflow `agent()` + schema 強制** |
| **PASS_WITH_WARNINGS 補查 + 重跑 QG** | prose「最多 2 輪」 | **Workflow `while` loop + budget guard** |
| **流程確定性** | 中（依賴主對話自律遵守 prose） | **高（JS 程式碼硬性保證）** |
| **中間過程可見性** | 高（subagent streaming） | 低（只看 `/workflows` 進度樹） |
| **Token 用量** | 基準 | ±10%（schema retry 偶發增加） |

### Workflow 模式啟用方式

當 `orchestration_mode: workflow` 時，主對話完成 Phase 2 + Devil's Advocate + Conflict Resolution 後，**不直接進入 Synthesis prose 編排**，改呼叫：

```
Workflow({
  scriptPath: "${SKILL_DIR}/references/synthesis-pipeline.workflow.js",
  args: {
    runDir: "<絕對路徑>",
    skillDir: "<SKILL_DIR 解析後的絕對路徑>",
    researchType: "<研究類型>",
    depth: "quick" | "standard" | "deep",
    digestFile: "<runDir>/research-digest_{YYYYMMDD}.md 絕對路徑",
    gapFile: "<runDir>/gap-analysis_{YYYYMMDD}.md 絕對路徑",
    s3: true,  // 選填，standard 模式且爭議結論 ≥2 時設 true 強制啟用 S-3
    finalReportFile: "<runDir>/report/{主題}_{研究類型}_{YYYYMMDD}.md 絕對路徑"
  }
})
```

除 `s3`（選填 boolean，僅 standard 模式生效，quick 模式即使傳 true 也忽略）外，其餘 args 均為必填絕對路徑或必填字串；缺任一 throw，researchType / depth 有 enum 驗證。

腳本完成後產出：
- `finalReportFile`（Merge phase 合併後的最終報告，Citation/QG 驗證對象）
- `{runDir}/citation-verify.md`（引用驗證結果 + rubric 5 項評分）
- `{runDir}/qg-result.md`（QG 三閘門結果）
- 回傳 `{ finalStatus, citation: {status, rate, rounds, rubric, metrics}, qg: {status, rounds, gates, warningCount}, citationBlocked, correctionLog, nextStep }` 給主對話

主對話依 `finalStatus` 分流：
  - `DONE` → 生成 README.md → 更新 MANIFEST 為 DONE
  - `DONE_WITH_WARNINGS` → 先 Read qg-result.md：**無** high-severity warning → README + DONE（README 標註 warnings）；**有** → 依下方「必看 #3」手動補完＋定向重驗後才可 DONE
  - `NEED_MANUAL_REVIEW` / `QG_AGENT_FAILED` → Read qg-result.md 與 citation-verify.md 後人工審視，**不得標 DONE**

#### ⚠️ 三條呼叫前必看

**1. `args` 在 transport 層會被序列化成 string**——與 Workflow tool 文件描述「Pass arrays/objects as actual JSON values」不一致。`synthesis-pipeline.workflow.js` 已加 `argsObj = typeof args === 'string' ? JSON.parse(args) : (args || {})` 雙保險。**未來改 workflow.js 時禁止刪除這段防呆**（刪了會在 args 解構階段立刻 throw `args 必填`，本身錯訊已經很清楚是 args 問題，但仍是不必要的中斷）。

**2. Citation Verify / QG subagent 的 status 欄位有 post-validation 由 counts 機械重算三指標（retrievability / support_rate / strict_support_coverage）與閘門**——背景：實測案例曾發生 Citation Verify subagent 在 rate=0.815（明顯 <0.85，按 prompt 明文應 FAIL）情況下，用「接近下限」軟性語言把 status 寫成 PASS_WITH_WARNINGS，繞過 FAIL → repair 硬閘。

   **治本**：`synthesis-pipeline.workflow.js` 在 Citation Verify subagent 返回後、進 while loop 判定前，加 post-validation block 機械重算 status（依 §0 三指標規則），不一致就強制覆寫並 `log()` 紀錄；QG 同樣加 post-validation（任一閘門 fail → 強制 FAIL）。

   **未來改 workflow.js 時禁止刪除這兩段 POST-VALIDATION 區塊**（會被軟化規則繞過、走不到 repair 路徑）。若要改 status 判定規則，**同時改 prompt 規則 + post-validation 程式碼**，保證兩者一致。

**3. 若 Workflow 回傳 `finalStatus = "DONE_WITH_WARNINGS"` 且 qg-result.md 內列有 high-severity warning（例如「data_self_consistency 跨章節數字不一致」），主對話必須接手做下列補完才能標 DONE**：
   - Step A：Read `${runDir}/qg-result.md`，定位每個 high-severity warning 提到的具體字串
   - Step B：對每個字串用 **Claude Code 內建 Grep tool**（`Grep({ pattern: 'XXX', path: '${runDir}/report', output_mode: 'content', '-n': true })`）列出全部出現處，逐處 Edit 修補（樂觀情境 / 未來預測語境的合理保留除外，需在 MANIFEST 註記）。**禁用**：shell `grep -n` 命令（跨平台不可靠）
   - Step C：再跑一次 Grep tool（同上參數）確認殘留 = 0；Grep tool 回 `No files found` **不代表殘留 0**（多視窗環境絕不可用「取最新目錄」定位自己的產物），須換 `output_mode: 'files_with_matches'` 或直接 Read 檔案二次驗證
   - Step D：MANIFEST 寫入「主對話手動補修：N 處修補 / M 處保留與理由」
   - 然後才寫 README + 標 DONE
   - 補修後至少重跑受影響驗證（citation 受影響 URL 子集或對應 QG 層）；無法定向重驗時維持 DONE_WITH_WARNINGS，不得改標 DONE

   **背景**：實測案例曾發生第 1 輪 QG 標出跨章節數字不一致、第 1 輪 repair 只修部分處、第 2 輪 QG 重檢仍標 high warning（達 QG 上限 2 輪不再強制補查）→ 主對話接手 grep 補修剩餘處才結案。workflow.js `qgRepairPrompt` 與 `citationRepairPrompt` 已加「修補完整性鐵則」要求 grep 找全部出現處，但仍可能因 LLM 漂移修不到 100%，主對話接手是最後安全網。

### Task 模式（未偵測到 Ultracode）

沿用 Task-based prose 編排，本檔後續所有「Synthesis」「Citation Verify」「Quality Gate」章節皆適用此模式。Workflow 模式啟用時，這些章節的編排細節改由 `synthesis-pipeline.workflow.js` 內 prompt 模板繼承實現。

### 為什麼只用 Workflow 跑 Synthesis → QG 這段？

- **這段最容易漂移**：實測案例曾出現「補查只跑 1 輪就結案」「重跑 QG 被略過」等違反規則的情況（規則寫著卻未遵守，需在回報前先查證規則確實存在於 SKILL.md 或 references 中）
- **這段最不依賴 streaming**：Phase 1/2 的 subagent streaming 對人類觀察很有幫助（看搜索方向對不對），但 Synthesis → QG 都是「結構化驗證」性質，streaming 反而沒太多 debug 價值
- **這段最容易程式碼化**：迴圈邏輯（補查最多 2 輪、重跑 QG 強制閉環）天生適合 `while` + budget guard，比 prose 約束穩定

---

## Phase 0：初始對話（唯一需要用戶參與的環節）

用 AskUserQuestion 一次收集所有必要資訊（合併成 3-4 題，不分多輪）：

**必問**：
1. 研究主題（確認理解是否正確）
2. 特別關注點（用戶想深挖的方向，例如：供應鏈風險、技術壁壘、創辦人背景、成本結構）
3. 研究深度：快速掃描（1-2 Phase）/ 標準研究（3 Phase）/ 深度分析（4+ Phase 含衝突驗證）
4. 輸出目錄：請用戶提供研究專案的根目錄路徑

**自動判斷**（不需問用戶）：
- 研究類型：依主題自動識別（公司/產品/技術/產業/人物/地區/商業模式/社會議題）
- 查詢語言：依主題自動決定（見 `references/query-strategy.md`）

**收集完畢後**：讀取 `references/dimensions.md` 和 `references/agent-config.md`，生成 MANIFEST，立即自動開始執行。

### 斷點恢復（自動偵測）

Phase 0 開始前，若用戶提供了輸出目錄，自動檢查是否有未完成的 MANIFEST。偵測到時提示用戶選擇繼續或重新開始。詳細恢復邏輯見 `references/agent-config.md` §7。

---

## 輸出路徑結構

```
{用戶指定的專案根目錄}/
└── {主題}_{YYYYMMDD}_{NONCE}/          ← 每個 run 加 8 字 hex 唯一後綴（多視窗隔離）
    ├── .run-meta                        ← 身分證（落地前核對；防多視窗誤恢復）
    ├── README.md                          ← 使用指南（怎麼讀這些檔案）
    ├── {主題}_MANIFEST_{YYYYMMDD}.md    ← 進度存檔
    ├── phase1/                           ← Discovery 搜索結果
    │   └── {維度名稱}_{YYYYMMDD}.md
    ├── phase2/                           ← Deep Search 搜索結果
    │   └── {任務名稱}_{YYYYMMDD}.md
    ├── gap-analysis_{YYYYMMDD}.md        ← Gap Analysis 精簡結果
    ├── research-digest_{YYYYMMDD}.md     ← Phase 1 精華摘要（供 Synthesis 用）
    ├── conflicts_{YYYYMMDD}.md           ← 衝突偵測結果（如有）
    ├── resolution_{YYYYMMDD}.md          ← 衝突解決結果（如有）
    ├── qg-result_{YYYYMMDD}.md           ← Quality Gate 結果
    └── report/
        └── {主題}_{研究類型}_{YYYYMMDD}.md  ← 最終報告
```

### 多視窗並發隔離

⚠️ **多視窗同時開 Claude Code 時**，若輸出路徑只有 `{主題}_{YYYYMMDD}/`，同主題同天會互相覆蓋檔案、斷點恢復誤接別人的進度。

**治本機制**：

1. **唯一後綴**：Phase 0 建目錄前生成 8 字 16 進位 `NONCE`（低碰撞機率隔離），目錄改為 `{主題}_{YYYYMMDD}_{NONCE}/`。建目錄用 `exist_ok=False` 語意，碰撞即重生成。生成方式（依序降級）：
   - **首選**：Bash tool 跑 `python -c "import secrets; print(secrets.token_hex(4))"`（Windows 標準 `python`；macOS/Linux 可用 `python` 或 `python3`）
   - **備援 1**：跑 `${SKILL_DIR}/scripts/nonce.py`（跨平台，內建 fallback 到 `os.urandom(4)`）
   - **備援 2**：Bash tool 跑 `node -e "console.log(require('crypto').randomBytes(4).toString('hex'))"`（若 Python 不可用但 Node.js 已裝，Claude Code 前置條件必有 Node.js）
   - **禁用**：`openssl rand -hex 4`（Windows 原生 cmd/PowerShell 無 openssl）
2. **身分證 `.run-meta`**：建目錄後**第一個**寫入的檔案，內容：
   ```
   run_id: {NONCE}
   topic: {主題}
   created_at: {YYYY-MM-DDTHH:MM:SS}
   session_hint: {主對話可填的辨識線索，如 cwd / pid / 對話首句}
   ```
3. **絕對路徑釘對話**：Phase 0 完成後，主對話**在對話中釘住** `RUN_DIR={絕對路徑}`，後續所有 subagent prompt、Write/Read 都用此絕對路徑，**禁止**用「取最新目錄」策略（多視窗環境絕不可用「取最新目錄」定位自己的產物——macOS/Linux 的 `ls -dt | head -1`、Windows PowerShell 的 `Get-ChildItem | Sort LastWriteTime | Select -Last 1` 都算）
4. **斷點恢復必核對身分證**：偵測到既有 MANIFEST 時，**先 Read `.run-meta`** 確認 `run_id` / `session_hint` 與當前對話匹配，不匹配就**列候選讓用戶選**，絕不默默挑「最新」
5. **subagent 落地前再核對**：每個 Phase 1/2 subagent 在寫入前先 Read `.run-meta` 比對 `run_id`，不符就 abort 並回報主對話（防止別視窗的 subagent 寫進本視窗目錄）

---

## 執行流程

```
啟動提示: 展示技能簡介 + Token 用量提醒 → 用戶確認繼續
    ↓
前置檢查: MCP 可用性確認（Exa / Tavily）
    ↓
Phase 0: 對話 → MANIFEST → 自動執行（或偵測既有 MANIFEST → 斷點續作）
    ↓
Phase 1: Discovery（廣度，4-8 個並行 subagents，分批啟動）
    ↓
Gap Analysis Subagent（1 個獨立 subagent）                    ← 不佔主窗口
  讀取所有 Phase 1 檔案 → 產出 gap-analysis.md + research-digest.md
  （含引用追蹤識別：標記高價值 GitHub/HN/Reddit URL）
    ↓
主對話讀取 gap-analysis.md（精簡版），編排 Phase 2 任務
    ↓
Phase 2: Deep Search（深度，3-6 個並行 subagents，含擴展維度 + 引用追蹤）
    ↓
Devil's Advocate 多輪辯論：
    Round 1（反論）→ Round 2（reflexion 反駁）→ Round 3（強化反論）
    ↓
Judge Agent（獨立仲裁多輪辯論結果，sonnet）
    ↓
Conflict Detection（1 個衝突偵測 subagent）
    ├─ 無衝突 → Synthesis
    └─ 有衝突 → Phase 3: Resolution Search → Synthesis
    ↓
Synthesis（2-3 個並行 subagents，各讀 research-digest.md）+ 每資料點強制 4 項標註
    ↓
Merge（合併章節片段為最終報告，Citation/QG 驗證對象）
    ↓
Citation Verification Subagent                                ← 不佔主窗口
  重抓最終報告所有 URL 比對引文 → 產出 citation-verify.md
  → 產 rubric 5 項 0-1 分供 QG C 閘門用
  判定（三指標機械重算）：
  ├─ PASS（support_rate ≥0.95 且 unreachable_rate ≤0.10）→ Quality Gate
  ├─ PASS_WITH_WARNINGS（support_rate ≥0.85 且 unreachable_rate ≤0.25）→ QG 標警告 → Quality Gate
  └─ FAIL（support_rate <0.85 或 unreachable_rate >0.25）→ 派 1 個補查 subagent 修正 → 重跑（最多 2 輪）
    ↓
Quality Gate Subagent（1 個獨立 subagent）                    ← 不佔主窗口
  讀取最終報告 + citation-verify.md → 產出 qg-result.md
  三閘門：A 分佈 + B 加權聚合 max(depth,type) + C LLM-as-judge
    ↓
主對話讀取 qg-result.md（精簡結論）
    ├─ PASS → 生成 README.md → 更新 MANIFEST 為 DONE，告知用戶
    ├─ PASS_WITH_WARNINGS → 補查 + **重跑 QG**（強制閉環）→
    │   ├─ 重跑後 PASS → README.md → DONE
    │   ├─ 重跑後仍 WARNINGS → 標記寫入 README → DONE_WITH_WARNINGS
    │   └─ 重跑 2 輪仍 FAIL → 標 FAIL，告知用戶手動審視
    └─ FAIL → 人工審視 qg-result.md 與 citation-verify.md，**不得標 DONE**
```

---

## Phase 1：Discovery

根據研究類型，將必要維度（見 `references/dimensions.md`）分組，每組 2-3 個維度交給一個 subagent（4-8 個並行，分批啟動）。Subagent prompt 模板見 `references/agent-config.md` §5。

**分批啟動規則**：超過 6 個 subagent 時，分兩批啟動（每批最多 6 個），第一批全部返回後再啟動第二批。避免 MCP Rate Limit 撞車導致大量重試浪費（Exa 全域併發預算：/search 10 QPS、/contents 100 QPS 為 API key 層級共用額度；Tavily 依方案）。

每個 subagent 必須：
- 使用至少 2 種不同工具查詢同一維度（互相驗證）
- 每個資料點立即附來源 URL + 採集工具名稱
- 邊查邊寫入輸出檔，不等全部完成後再寫
- 遇到工具失敗時執行重試邏輯（見 `references/agent-config.md` §2）
- 返回精簡摘要（見 `references/agent-config.md` §6）

---

## Gap Analysis（由 Subagent 執行）

Phase 1 完成後，啟動 **1 個 Gap Analysis subagent**（prompt 模板見 `references/agent-config.md` §8）。

此 subagent 讀取所有 Phase 1 輸出，執行 **9 步**分析，產出兩份檔案：

1. **gap-analysis.md**：缺口清單、假說清單、擴展任務清單、引用追蹤候選、假設審計預備
   - 陌生詞抓取
   - 空白識別 + 補強優先級
   - 衝突初步標記
   - 路徑決策
   - 代理資料策略
   - 假說形成（3-5 個可驗證假說）
   - 意圖驅動擴展（詳細規則見 `references/intent-expansion.md`）
   - 引用追蹤識別（詳見 `references/agent-config.md` §8 第 8 步）
   - **假設審計預備**：對核心發現初步識別隱含假設，供 Synthesis 假設審計框架使用

2. **research-digest.md**：Phase 1 精華摘要（原始輸出的 30-40%），供 Synthesis subagents 使用

**主對話只讀 gap-analysis.md**（~100-200 行），根據其內容編排 Phase 2 任務。

---

## Phase 2：Deep Search

根據 Gap Analysis 結果動態分配 3-6 個並行 subagent。

**必含的通用 Subagent**（所有研究類型）：

- **風險評估 Subagent**：財務/市場/技術/法規/地緣風險 + Pre-mortem 分析
- **成本結構 Subagent**：成本拆解/驅動因素/競品對比/優化空間

**條件觸發的 Subagent**：

- **引用追蹤 Subagent**（當 Gap Analysis 識別出高價值追蹤 URL 時）：GitHub issue/PR 引用鏈、社群討論串（HN/Reddit）深抓、學術引用追蹤。最大追蹤深度 2 層，每層最多 10 個引用，自動過濾低相關節點。Prompt 模板見 `references/agent-config.md` §10。
- **Devil's Advocate Subagent**：主動搜尋否定研究主流結論的證據。深度分析時強制啟動，標準研究時在 ≥2 個爭議性結論時啟動。採用 3 輪 reflexion（Round 1 反論 → Round 2 結論方反駁 → Round 3 強化反論），由獨立 Judge Agent（§15）仲裁，不由 Devil's Advocate 自評。Prompt 模板見 `references/agent-config.md` §11 §15。
- **投資專項 Subagent**：**研究類型為公司研究 + dimensions.md 包含「投資決策評估」維度時必觸發**（深度模式強制、標準模式條件觸發、快速模式跳過）。整理第三方 DCF 三情境、可比公司倍數矩陣、市場錯誤定價信號、ROIIC 資本配置效率、投資論點壓力測試。**只整理既有第三方分析，不自行建模**。Prompt 模板見 `references/agent-config.md` §12，框架見 `references/frameworks.md` §12.1-§12.4。
- **GTM 行銷專項 Subagent**：**研究類型為產品研究 + dimensions.md 包含「GTM 行銷策略」維度時必觸發**（深度模式強制、標準模式條件觸發、快速模式跳過）。執行 JTBD 痛點優先級、Bullseye 19 渠道對比、April Dunford 5 點定位、競品 4P 對標、AARRR 漏斗。Prompt 模板見 `references/agent-config.md` §13，框架見 `references/frameworks.md` §13.1-§13.4。

**Phase 2 subagents 的雙重任務**：除了補缺口，每個 subagent 同時負責驗證 1-2 個相關假說（來自 gap-analysis.md），標記為 ✅ 已驗證 / ❌ 已否證 / ⚠️ 證據不足。

---

## Conflict Detection + Resolution

Phase 2 完成後啟動 1 個衝突偵測 subagent，讀取所有 phase1/ + phase2/ 輸出，依 `references/verification.md` 的邏輯執行。

- 無衝突 → 直接進入 Synthesis
- 有衝突 → 啟動 Resolution Search subagent → 完成後進入 Synthesis

---

## Synthesis（2-3 個並行 Subagents）

各 Synthesis subagent 讀取 **research-digest.md ＋ phase2/*.md 原始輸出**，加上各自的 reference：

| Subagent | 負責 | 讀取 |
|----------|------|------|
| S-1 分析報告 | 整合數據、橫向對標、信心評級 | research-digest + phase2/*.md + `output-template.md` + `frameworks.md` |
| S-2 行動手冊 | 供應商排序、路線圖、成本表、行動清單 | research-digest + phase2/*.md + `output-template.md` |
| S-3 前瞻分析（深度時） | 三情境展望、假說驗證、Pre-mortem | research-digest + phase2/*.md + `frameworks.md` |

分工細節見 `references/synthesis-spec.md`。S-1 + S-2（+ S-3）合併為最終報告。

---

## Quality Gate（由 Subagent 執行）

Synthesis 產出報告後，啟動 **1 個 Quality Gate subagent**（prompt 模板見 `references/agent-config.md` §9）。

此 subagent 讀取最終報告 + `references/quality-gate.md`，執行**三閘門品質檢查**：

- **閘門 A（資料點信心分佈）**：依 depth 門檻判定 🟢/🟡 比例是否達標
- **閘門 B（加權聚合）**：門檻 = max(depth 門檻, type 門檻)——company/商業決策類 0.80；person/region/social 0.60；其他 0.70
- **閘門 C（LLM-as-judge rubric_avg）**：deep ≥0.75 / standard ≥0.65 / quick ≥0.55

附帶 6 層自檢：
1. 數據自洽（data_self_consistency 7 項 canonical checklist，見 quality-gate.md）
2. 維度覆蓋（對照 dimensions.md）
3. 信心門檻（動態門檻：依研究類型 60-80%）
4. 行動手冊完整度
5. 時效性檢查：>20% 數據過時觸發警告
6. 邏輯一致性檢查：Steel-man 反論 ↔ 決策建議、假設審計 ↔ 結論的一致性

產出 **qg-result.md**，主對話只讀此精簡結論：
- **PASS** → 更新 MANIFEST 為 DONE
- **PASS_WITH_WARNINGS** → 補查 + 重跑 QG（強制閉環，最多 2 輪）→ 最終 PASS 則 DONE，否則 DONE_WITH_WARNINGS
- **FAIL** → 人工審視 qg-result.md 與 citation-verify.md，**不得標 DONE**

---

## README 生成（QG 通過後自動執行）

Quality Gate 通過後、更新 MANIFEST 為 DONE 之前，主對話在研究專案根目錄生成 `README.md`。

此 README 是研究成果的**使用指南**，幫助用戶（包括未來的自己）快速理解：
- 這個資料夾是什麼、研究了什麼主題
- 每個檔案的用途和閱讀順序
- 報告中的符號和評級代表什麼意思
- 怎麼根據不同需求快速找到想看的內容

README 模板見 `references/output-template.md` 末尾的「README 模板」章節。主對話根據實際研究參數填入模板後寫入 `{專案根目錄}/README.md`。

---

## Context 管理

基於階段計數的硬規則（不依賴無法測量的百分比）：

| 規則 | 限制 |
|------|------|
| Phase 1 並行 subagent 上限 | **8 個**（分批啟動，每批最多 6 個） |
| Phase 2 並行 subagent 上限 | **6 個**（含引用追蹤 + Devil's Advocate，每批最多 5 個） |
| 主對話單次 Read 檔案上限 | **3 個**（優先讀精簡版） |
| Synthesis 並行 subagent 上限 | **3 個** |
| 補查 subagent 上限 | **3 個** |

**主對話不直接讀取的檔案**（由 subagent 處理）：
- Phase 1 原始輸出（由 Gap Analysis subagent 讀）
- 最終報告全文（由 Quality Gate subagent 讀）

**若 context 仍接近上限**（系統提示壓縮）：
1. 停止新批次，確保當前結果已寫入磁碟
2. 更新 MANIFEST 到最新狀態
3. 輸出：「⚠️ Context 接近上限。請執行 /compact 後告訴我『繼續研究 {主題}』」
4. 恢復邏輯見 `references/agent-config.md` §7

---

## 安全備註

Workflow/Task 派生 subagent 的工具權限由宿主環境決定，prompt 白名單是行為約束非硬邊界。技能已內建反 prompt-injection 前言與 run_id 落地核對。處理不可信網頁內容時建議在支援的環境配置受限 agent。

---

## 參考文件

| 文件 | 內容 | 何時讀取 | 由誰讀取 |
|------|------|---------|---------|
| `references/dimensions.md` | 8 種研究類型的必要維度 | Phase 0 確認研究類型後 | 主對話 |
| `references/agent-config.md` | Subagent 模板、Rate Limit、MANIFEST 格式、重試邏輯、斷點恢復 | Phase 0 生成 MANIFEST 時 | 主對話 |
| `references/query-strategy.md` | 多語言查詢、語意擴展、社交平台規則 | Subagent 啟動後 | 各 Subagent |
| `references/tool-reference.md` | 13 個 MCP 工具的參數格式與調用範例 | Subagent 啟動後 | 各 Subagent |
| `references/intent-expansion.md` | 意圖驅動關鍵字擴展規則 | Gap Analysis 時 | Gap Analysis Subagent |
| `references/verification.md` | 衝突偵測與解決邏輯 | Conflict Detection 時 | 衝突偵測 Subagent |
| `references/synthesis-spec.md` | Synthesis 分工規範（S-1/S-2/S-3） | Synthesis 時 | 各 Synthesis Subagent |
| `references/output-template.md` | 報告格式模板 | Synthesis 時 | S-1 / S-2 Subagent |
| `references/frameworks.md` | 分析框架（PESTEL、Moat 等） | Synthesis 時 | S-1 / S-3 Subagent |
| `references/quality-gate.md` | Quality Gate 三閘門品質檢查規範（含動態門檻、7 項 data_self_consistency checklist） | Quality Gate 時 | QG Subagent |
| `references/CHANGELOG.md` | 完整版本沿革（v.260313 → v.260625） | 需查歷史演進時 | 按需 |