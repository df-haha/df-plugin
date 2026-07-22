# MCP 工具調用參考（Subagent 必讀）

本文件提供所有 MCP 工具的**參數格式、預設值、回傳結構與調用範例**，供 subagent 正確調用工具。

> **命名規則**：工具全名為 `mcp__exa__web_search_exa` 等，但在本文件中省略 `mcp__exa__` / `mcp__tavily__` 前綴，僅寫工具短名。

---

## Exa 工具（8 個，全域併發預算：/search 10 QPS / /contents 100 QPS / /answer 10 QPS，間隔 ≥ 100ms）

> Exa rate limit 為 **API key 層級的全域併發預算**（官方文件 https://docs.exa.ai/reference/rate-limits）。/search 預設 10 QPS / 600 RPM，6 個並行 subagent 各自間隔 ≥ 100ms 即可，不需主對話統一排程。429 分類重試機制不變。

> **⚠️ 工具可用性**：Exa MCP 支援兩種安裝方式，啟用的工具清單不同：
> - **HTTP 方式**（`"type": "http"` + URL 帶 `?tools=...`）：URL 中列出的工具全部可用，本技能預設所有 8 個工具均已啟用。
> - **stdio 方式**（`npx -y mcp-remote`）：預設只啟用 3 個（`web_search_exa`、`get_code_context_exa`、`company_research_exa`），其餘 5 個需在安裝後手動設定。
> 若呼叫工具時收到 `tool not found` 錯誤，檢查 Claude Code 用戶配置檔的 Exa MCP 設定（macOS/Linux：`~/.claude.json`；Windows：`%USERPROFILE%\.claude.json`），確認工具已在清單中，並改用可用工具或 `tavily_search` 替代。

### 1. web_search_exa

**用途**：通用網頁語意搜尋

**回傳**：Title + Published Date + URL + 內文摘要

| 參數 | 類型 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `query` | string | ✅ | — | 搜尋關鍵字 |
| `numResults` | number | | 8 | 回傳結果數 |
| `contextMaxCharacters` | number | | 10000 | 上下文最大字元數 |
| `type` | enum | | `"auto"` | `"auto"` / `"fast"` |
| `livecrawl` | enum | | `"fallback"` | `"fallback"` / `"preferred"` |

```json
{ "query": "TOMRA recycling technology 2025", "numResults": 5 }
```

---

### 2. web_search_advanced_exa

**用途**：精確篩選搜尋（日期/域名/分類/高亮/摘要）

**回傳**：搜尋結果 + 可選的高亮片段、摘要、子頁內容

#### 基本搜尋

| 參數 | 類型 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `query` | string | ✅ | — | 搜尋關鍵字 |
| `additionalQueries` | string[] | | — | 額外查詢，擴展搜尋覆蓋 |
| `numResults` | number | | 10 | 結果數（1-100） |
| `type` | enum | | `"auto"` | `"auto"` / `"fast"` / `"neural"`（語意搜尋） |

#### 日期篩選

| 參數 | 類型 | 說明 |
|------|------|------|
| `startPublishedDate` | string | 發布日期起始（YYYY-MM-DD） |
| `endPublishedDate` | string | 發布日期結束 |
| `startCrawlDate` | string | 爬取日期起始 |
| `endCrawlDate` | string | 爬取日期結束 |

#### 域名與文字篩選

| 參數 | 類型 | 說明 |
|------|------|------|
| `includeDomains` | string[] | 限定域名（如 `["arxiv.org", "github.com"]`） |
| `excludeDomains` | string[] | 排除域名 |
| `includeText` | string[] | 結果必須包含**全部**這些文字 |
| `excludeText` | string[] | 排除包含**任一**這些文字的結果 |

#### 分類篩選

| 參數 | 類型 | 選項 |
|------|------|------|
| `category` | enum | `company` / `research paper` / `news` / `pdf` / `github` / `tweet` / `personal site` / `people` / `financial report` |

#### 即時爬取

| 參數 | 類型 | 預設 | 說明 |
|------|------|------|------|
| `livecrawl` | enum | `"fallback"` | `"never"` / `"fallback"` / `"always"` / `"preferred"` |
| `livecrawlTimeout` | number | — | 即時爬取超時（毫秒） |

#### 內容控制

| 參數 | 類型 | 說明 |
|------|------|------|
| `textMaxCharacters` | number | 每筆結果的文字最大字元數 |
| `contextMaxCharacters` | number | LLM 上下文最大字元數 |

#### 摘要與高亮

| 參數 | 類型 | 說明 |
|------|------|------|
| `enableSummary` | boolean | 啟用摘要 |
| `summaryQuery` | string | 摘要聚焦查詢 |
| `enableHighlights` | boolean | 啟用高亮 |
| `highlightsQuery` | string | 高亮相關性查詢 |
| `highlightsNumSentences` | number | 每段高亮句數 |
| `highlightsPerUrl` | number | 每個 URL 高亮數 |

#### 子頁爬取

| 參數 | 類型 | 說明 |
|------|------|------|
| `subpages` | number | 每個結果爬取子頁數（1-10） |
| `subpageTarget` | string[] | 子頁選擇的目標關鍵字 |

#### 其他

| 參數 | 類型 | 說明 |
|------|------|------|
| `userLocation` | string | ISO 國碼（如 `"TW"`） |
| `moderation` | boolean | 過濾不當內容 |

```json
{
  "query": "公司名 revenue growth",
  "category": "news",
  "startPublishedDate": "2025-09-01",
  "numResults": 10,
  "enableSummary": true,
  "summaryQuery": "financial performance"
}
```

#### 常用場景速查

| 場景 | 建議參數 |
|------|---------|
| 最新動態（近 3 個月） | `startPublishedDate: "YYYY-MM-DD"` |
| 學術論文 | `category: "research paper"`, `includeDomains: ["arxiv.org"]` |
| 財報/財務資料 | `category: "financial report"` |
| GitHub 專案 | `category: "github"` |
| 新聞報導 | `category: "news"` |
| 語意搜索模式 | `type: "neural"` |

---

### 3. company_research_exa

**用途**：公司結構化資料（財務、人力、競爭者、技術棧）

**回傳**：結構化公司資訊，包含：
- 基本資料（總部、員工數、產業）
- 財務數據（年營收、融資歷程、收購紀錄）
- 人力分析（國家/部門/資歷分布、人才來源、離職去向）
- 職缺資訊、網站流量、雇主評價
- 競爭者清單、近期新聞、技術堆疊
- 結構化 `entities` 物件（適合程式化處理）

| 參數 | 類型 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `companyName` | string | ✅ | — | 公司名稱 |
| `numResults` | number | | 3 | 結果數 |

```json
{ "companyName": "TOMRA Systems", "numResults": 2 }
```

---

### 4. crawling_exa

**用途**：已知 URL，擷取頁面完整內容（WebFetch 備援）

**回傳**：頁面完整文字內容 + metadata

| 參數 | 類型 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `url` | string | ✅ | — | 目標網址 |
| `maxCharacters` | number | | 3000 | 最大擷取字元數 |

```json
{ "url": "https://example.com/annual-report", "maxCharacters": 10000 }
```

---

### 5. people_search_exa

**用途**：人物結構化資料（職涯、教育）

**回傳**：結構化人物資訊，包含：
- 基本資料（姓名、所在地）
- 完整職涯歷程（`workHistory` 陣列：職稱、公司、日期）
- 教育經歷（`educationHistory` 陣列：學位、學校、日期）
- 結構化 `entities` 物件

| 參數 | 類型 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `query` | string | ✅ | — | 搜尋描述（如 `"Anthropic CEO"`） |
| `numResults` | number | | 5 | 結果數 |

```json
{ "query": "Dario Amodei Anthropic CEO", "numResults": 2 }
```

---

### 6. deep_researcher_start

**用途**：啟動 AI 深度研究，自動搜尋 + 閱讀 + 撰寫報告

**耗時**：15 秒 ~ 3 分鐘（依模式）

**回傳**：`researchId`（必須用 `deep_researcher_check` 輪詢取得結果）

| 參數 | 類型 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `instructions` | string | ✅ | — | 研究指令，越具體越好 |
| `model` | enum | | `"exa-research-fast"` | 見下方模式說明 |
| `outputSchema` | object | | — | JSON Schema，讓輸出結構化（回傳含 `parsed` 欄位） |

**研究模式**：

| 模式 | 耗時 | 適用場景 |
|------|------|---------|
| `exa-research-fast` | ~15 秒 | 簡單查詢（**預設**） |
| `exa-research` | 15-45 秒 | 多數場景適用 |
| `exa-research-pro` | 45 秒-3 分鐘 | 最全面深入，複雜主題 |

```json
{
  "instructions": "Research TOMRA Systems' competitive position in the recycling technology market",
  "model": "exa-research"
}
```

---

### 7. deep_researcher_check

**用途**：查詢深度研究結果（需**持續輪詢**至 `completed`）

**回傳**：
- `status: "completed"` → 完整研究報告（+ 可選的結構化 `parsed` 資料）
- `status` 非 completed → 進度狀態，需繼續輪詢

| 參數 | 類型 | 必填 | 說明 |
|------|------|------|------|
| `researchId` | string | ✅ | 從 `deep_researcher_start` 取得的 ID |

**⚠️ 關鍵流程**：
```
1. deep_researcher_start → 取得 researchId
2. deep_researcher_check(researchId) → 檢查狀態
3. status ≠ "completed" → 等待 5-10 秒 → 再次 check
4. status = "completed" → 取得完整報告
```

---

### 8. get_code_context_exa

**用途**：程式碼/技術文件搜尋（GitHub、Stack Overflow、官方文件）

**回傳**：相關程式碼範例與文件，已格式化

| 參數 | 類型 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `query` | string | ✅ | — | 搜尋描述 |
| `tokensNum` | number | | 5000 | 回傳 token 數（1,000-50,000） |

**tokensNum 建議**：

| 場景 | 建議值 |
|------|--------|
| 快速查特定 API | 1000-3000 |
| 一般技術問題 | 5000（預設） |
| 需要完整文件/多範例 | 10000-20000 |
| 深度技術研究 | 30000-50000 |

```json
{ "query": "NIR textile sorting algorithm implementation", "tokensNum": 10000 }
```

---

## Tavily 工具（5 個，間隔 ≥ 500ms，額度有限作交叉驗證用）

### 1. tavily_search

**用途**：網頁搜尋（第三引擎，交叉驗證用）

**回傳**：搜尋結果摘要 + 來源 URL

#### 基本搜尋

| 參數 | 類型 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `query` | string | ✅ | — | 搜尋關鍵字 |
| `search_depth` | enum | | `"basic"` | `"ultra-fast"` / `"fast"` / `"basic"` / `"advanced"` |
| `topic` | enum | | `"general"` | 目前僅支援 `"general"` |
| `max_results` | number | | 5 | 結果數（5-20） |

#### 時間篩選

| 參數 | 類型 | 說明 |
|------|------|------|
| `time_range` | enum | `"day"` / `"week"` / `"month"` / `"year"` |
| `start_date` | string | 起始日期（YYYY-MM-DD） |
| `end_date` | string | 結束日期（YYYY-MM-DD） |

#### 域名篩選

| 參數 | 類型 | 說明 |
|------|------|------|
| `include_domains` | string[] | 限定域名 |
| `exclude_domains` | string[] | 排除域名 |

#### 地理定位

| 參數 | 類型 | 說明 |
|------|------|------|
| `country` | string | 國碼，提升特定國家結果權重 |

#### 額外內容

| 參數 | 類型 | 預設 | 說明 |
|------|------|------|------|
| `include_raw_content` | boolean | false | 包含清理後的 HTML 內容 |
| `include_images` | boolean | false | 包含相關圖片 |
| `include_image_descriptions` | boolean | false | 包含圖片描述 |

```json
{
  "query": "TOMRA Systems annual revenue 2025",
  "search_depth": "advanced",
  "max_results": 10,
  "time_range": "year"
}
```

---

### 2. tavily_extract

**用途**：URL 內容擷取（支援批次 + LinkedIn/受保護網站）

**回傳**：頁面原始內容（markdown/text 格式）

| 參數 | 類型 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `urls` | string[] | ✅ | — | URL 列表（**可批次**） |
| `query` | string | | — | 內容區塊相關性排序查詢 |
| `extract_depth` | enum | | `"basic"` | `"basic"` / `"advanced"`（LinkedIn 等受保護網站用 advanced） |
| `format` | enum | | `"markdown"` | `"markdown"` / `"text"` |

```json
{
  "urls": ["https://linkedin.com/company/tomra", "https://glassdoor.com/tomra"],
  "extract_depth": "advanced",
  "format": "markdown"
}
```

**⚠️ 與 crawling_exa 差異**：tavily_extract 支援批次 URL + 受保護網站 + 格式選擇；crawling_exa 僅支援單一 URL。

---

### 3. tavily_crawl

**用途**：網站多頁爬取（文件站、官網完整爬取）

**回傳**：多個頁面的內容（markdown/text 格式）

#### 基本設定

| 參數 | 類型 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `url` | string | ✅ | — | 起始 URL |
| `instructions` | string | | — | 自然語言指令，指定爬取哪類頁面 |
| `format` | enum | | `"markdown"` | `"markdown"` / `"text"` |
| `extract_depth` | enum | | `"basic"` | `"basic"` / `"advanced"` |

#### 爬取範圍控制

| 參數 | 類型 | 預設 | 說明 |
|------|------|------|------|
| `max_depth` | integer | 1 | 最大爬取深度 |
| `max_breadth` | integer | 20 | 每層最大連結數 |
| `limit` | integer | 50 | 總處理連結數上限 |

#### 路徑篩選

| 參數 | 類型 | 說明 |
|------|------|------|
| `select_domains` | string[] | 域名正則（如 `["^docs\\.example\\.com$"]`） |
| `select_paths` | string[] | 路徑正則（如 `["/docs/.*"]`） |
| `allow_external` | boolean | 是否回傳外部連結（預設 true） |

```json
{
  "url": "https://www.tomra.com/en/solutions",
  "instructions": "Crawl all product and solution pages",
  "max_depth": 2,
  "max_breadth": 10,
  "limit": 20,
  "select_paths": ["/en/solutions.*", "/en/products.*"]
}
```

---

### 4. tavily_map

**用途**：映射網站結構，回傳 URL 列表（不擷取內容，速度快）

**回傳**：從起始 URL 發現的所有 URL 列表

| 參數 | 類型 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `url` | string | ✅ | — | 起始 URL |
| `instructions` | string | | — | 自然語言指令 |
| `max_depth` | integer | | 1 | 最大映射深度 |
| `max_breadth` | integer | | 20 | 每層最大連結數 |
| `limit` | integer | | 50 | 總處理連結數上限 |
| `select_domains` | string[] | | [] | 域名正則 |
| `select_paths` | string[] | | [] | 路徑正則 |
| `allow_external` | boolean | | true | 是否回傳外部連結 |

```json
{
  "url": "https://www.tomra.com",
  "instructions": "Find all investor relations and annual report pages",
  "max_depth": 2,
  "limit": 100
}
```

**⚠️ 典型流程**：先 `tavily_map` 了解網站結構 → 再用 `tavily_extract` 或 `WebFetch` 擷取特定頁面。

---

### 5. tavily_research

**用途**：同步深度研究，多來源收集資訊生成報告（deep_researcher 備援）

**回傳**：基於多來源研究結果的詳細回答

| 參數 | 類型 | 必填 | 預設 | 說明 |
|------|------|------|------|------|
| `input` | string | ✅ | — | 研究任務完整描述 |
| `model` | enum | | `"auto"` | `"mini"` / `"pro"` / `"auto"` |

**研究模式**：

| 模式 | 適用場景 |
|------|---------|
| `mini` | 狹窄任務，子主題少 |
| `pro` | 廣泛任務，子主題多 |
| `auto` | 自動選擇（**預設**） |

```json
{
  "input": "Analyze TOMRA Systems' competitive position in NIR sorting technology for textile recycling",
  "model": "pro"
}
```

**⚠️ 與 Exa deep_researcher 差異**：

| 差異點 | tavily_research | Exa deep_researcher |
|--------|----------------|-------------------|
| 調用方式 | 單一工具，同步回傳 | 雙工具（start + check），非同步輪詢 |
| 結構化輸出 | ❌ | ✅（outputSchema） |
| 等待方式 | 直接等待回傳 | 需持續呼叫 check 直到 completed |

---

## 內建工具（永遠可用，無限制）

### WebSearch

Claude Code 內建搜尋，無 QPS 限制，永遠可用的基礎保障。

### WebFetch

Claude Code 內建 URL 擷取，無限制。失敗時（403/429/503）切換到 tavily_extract → crawling_exa → r.jina.ai。

### r.jina.ai（透過 WebFetch）

免費公開服務，20 RPM 限制（≥ 3 秒/次）。用法：

```
原始 URL：https://example.com/article
Jina URL：https://r.jina.ai/https://example.com/article
```

透過 `WebFetch` 呼叫 Jina URL 即可。由主對話統一排程，串行不並行。

---

## Playwright MCP 工具（5 個，終極備援）

> **定位**：當所有其他擷取工具都失敗時使用。真實瀏覽器渲染，能處理 SPA、JS 渲染、反爬機制。
> **前提**：需要 Playwright MCP Server 已安裝（MANIFEST 中記錄狀態）。
> **重要**：用完必須呼叫 `browser_close` 釋放記憶體。

### 1. browser_navigate

**用途**：開啟目標頁面

| 參數 | 類型 | 必填 | 說明 |
|------|------|------|------|
| `url` | string | ✅ | 目標網址 |

```json
{ "url": "https://smithery.ai/server/@anthropics/brave-search" }
```

### 2. browser_snapshot

**用途**：取得頁面可訪問性快照（結構化文字，比截圖更適合 LLM 處理）

| 參數 | 類型 | 必填 | 說明 |
|------|------|------|------|
| `filename` | string | | 儲存快照到 markdown 檔案（不提供則直接回傳） |

```json
{}
```

**比 screenshot 更推薦**：快照回傳結構化文字（含 ref 標識），適合程式化處理和元素定位。

### 3. browser_run_code

**用途**：執行完整的 Playwright 程式碼片段（進階操作：點擊、滾動、等待、取資料）

| 參數 | 類型 | 必填 | 說明 |
|------|------|------|------|
| `code` | string | ✅ | Playwright 程式碼（接收 `page` 參數） |

```json
{
  "code": "async (page) => { await page.waitForSelector('.skill-list'); return await page.$$eval('.skill-card', cards => cards.map(c => ({ name: c.querySelector('h3')?.textContent }))); }"
}
```

可用完整 Playwright API（`page.click()`、`page.waitForSelector()` 等），適合需要互動操作的場景。

### 4. browser_close

**用途**：關閉瀏覽器，釋放記憶體

```json
{}
```

**必須呼叫**：每次使用 Playwright 完成後都必須 `browser_close`，避免 headless 瀏覽器長時間佔用記憶體。

### Playwright 典型使用流程

```
場景：SPA 頁面擷取
1. browser_navigate({ url: "https://target.com/page" })
2. browser_snapshot({})  → 取得頁面結構
3. browser_close({})  → 釋放資源

場景：需要互動操作或執行 JS 取資料
1. browser_navigate({ url: "https://target.com" })
2. browser_run_code({ code: "async (page) => { ... }" })
3. browser_close({})
```

---

## Registry API（透過 Bash curl）

> **定位**：許多平台的前端是 SPA（爬蟲抓不動），但後端有公開 API 可直接取結構化 JSON 資料。
> **優先級最高**：如目標平台有 Registry API，應優先於所有爬蟲工具使用。

### 調用方式

**首選**：直接用 **Claude Code 內建 WebFetch 工具**（跨平台、無殼相依）：

```
WebFetch({ url: "https://registry.smithery.ai/skills/anthropics/brave-search", prompt: "return raw JSON" })
WebFetch({ url: "https://registry.npmjs.org/express", prompt: "extract description field" })
```

**備援**（若特殊場景需離線 shell）：透過 `Bash` 工具執行 `curl` 命令（Windows 10 build 17063+ 內建 curl）：

```bash
# 單一資源查詢（下例用 python3＝macOS/Linux；Windows 環境把 python3 換成 python）
curl -s "https://registry.smithery.ai/skills/anthropics/brave-search" | python3 -c "import sys,json; print(json.dumps(json.load(sys.stdin), indent=2))"

# 列表查詢（分頁）
curl -s "https://registry.smithery.ai/skills?page=1&pageSize=100"

# npm 套件查詢（下例用 python3＝macOS/Linux；Windows 環境把 python3 換成 python）
curl -s "https://registry.npmjs.org/express" | python3 -c "import sys,json; d=json.load(sys.stdin); print(d.get('description',''))"
```

### 已知的 Registry API

| 平台 | API 端點 | 備註 |
|------|---------|------|
| Smithery | `registry.smithery.ai/skills/{ns}/{slug}` | 支援 `?page=N&pageSize=100`、`?category=X` |
| npm | `registry.npmjs.org/{package}` | 無需認證 |
| PyPI | `pypi.org/pypi/{package}/json` | 無需認證 |
| Docker Hub | `registry.hub.docker.com/v2/repositories/{ns}/{repo}` | 無需認證 |
| VS Code Marketplace | `marketplace.visualstudio.com/_apis/public/gallery/extensions/{pub}/{name}` | 無需認證 |
| crates.io | `crates.io/api/v1/crates/{name}` | 無需認證 |
| GitHub | `api.github.com/repos/{owner}/{repo}` | 有 rate limit |

### 使用原則

- Registry API 拿到基本資料後，從 `gitUrl`、`homepage` 等欄位可進一步取得原始文件
- 不確定平台是否有 API 時，先搜尋 `"{平台名} API documentation"` 或嘗試 `curl https://{域名}/api/`
- 回傳的 JSON 可用 `python -c`（Windows）或 `python3 -c`（macOS/Linux）做輕量解析，不需要額外安裝套件；跨平台首選是 Claude Code 內建 WebFetch tool，見上方「首選」

---

## 全工具功能對照

| 功能 | Exa 工具 | Tavily 工具 | Playwright | Registry API |
|------|---------|------------|-----------|-------------|
| 通用搜尋 | `web_search_exa` | `tavily_search` | — | — |
| 進階搜尋 | `web_search_advanced_exa` | （有限篩選） | — | — |
| URL 擷取 | `crawling_exa`（單一） | `tavily_extract`（批次） | `navigate + snapshot` | — |
| SPA 頁面擷取 | ❌ 常超時 | ❌ 常 432 | ✅ 最可靠 | ✅（如有 API） |
| 結構化資料 | `company/people_research` | — | 需寫 JS 解析 | ✅ 原生 JSON |
| 公司研究 | `company_research_exa` | — | — | — |
| 人物搜尋 | `people_search_exa` | — | — | — |
| 程式碼搜尋 | `get_code_context_exa` | — | — | — |
| 深度研究 | `deep_researcher`（非同步） | `tavily_research`（同步） | — | — |
| 網站爬取 | — | `tavily_crawl` | 逐頁手動 | 分頁 API |
| 網站映射 | — | `tavily_map` | — | — |
| 速率限制 | /search 10 QPS、/contents 100 QPS（全域併發預算） | 依方案 | 無（本地瀏覽器） | 依平台 |
| 適合批次 | ✅ | ✅ | ❌ 太慢 | ✅ |
