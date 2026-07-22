# 查詢策略：多語言、語意擴展、社交平台

## 1. 多語言查詢規則

**英文（必查）**：任何主題都必須包含英文查詢。技術文獻、國際公司、學術資料大多以英文為主。

**依主題自動加入的語言**：

| 主題類型 | 額外語言 |
|---------|---------|
| 台灣公司/市場/議題 | 繁體中文（必加）|
| 中國大陸公司/市場 | 簡體中文（必加）|
| 日本公司/技術 | 日文（必加，用日文公司名） |
| 韓國公司/技術 | 韓文（必加，用韓文名） |
| 歐洲特定公司（德/法/北歐） | 英文即可，該國語言為輔 |
| 全球產業/市場 | 英文 + 繁中 + 簡中 |

**注意**：中文互聯網可能缺乏技術/財務的深度資料，遇到資料不足時立即切換英文補查。

---

## 2. 語意擴展規則

不只使用用戶輸入的關鍵字，必須自動擴展到語意相關詞。

**展開邏輯**：
1. **正式名稱變體**：縮寫、別名、前公司名（例：「Meta」也查「Facebook」）
2. **相關概念**：描述同一事物的不同說法
3. **上下游關聯**：競爭對手名稱、母子公司、主要產品名
4. **問題導向**：用戶真正想知道的事（不只字面意思）

**範例**：
```
輸入：「TOMRA」
擴展：TOMRA Systems ASA、TOMRA recycling、TOMRA sorting、
      reverse vending machine leader、TOM.OL、
      挪威回收分選機器、Tomra 紡織分選

輸入：「二手衣分選技術」
擴展：used clothing sorting automation、textile sorting technology、
      secondhand apparel processing、clothing AI classification、
      NIR textile sorting、fibresort、automated thrift sorting

輸入：「台灣二手衣市場」
擴展：Taiwan secondhand clothing market、台灣二手服飾市場、
      台灣舊衣回收、台灣二手衣產業、台灣環保回收衣物
```

---

## 3. 社交平台搜索規則

社交平台能提供官網/財報找不到的真實評價、用戶心聲、內部文化。**必須納入**，不可省略。

### 全球平台

| 平台 | 適用內容 | 搜索方式 |
|-----|---------|---------|
| Reddit | 產品真實評價、技術討論、用戶投訴、業界吐槽 | `WebSearch: site:reddit.com {主題}` |
| LinkedIn | 公司員工動態、高管發言、職位招聘（判斷方向）| 直接查公司頁面或人物頁面 |
| Glassdoor | 員工文化評價、薪資範圍、管理風格 | `WebSearch: {公司名} Glassdoor review` |
| X (Twitter) | 最新動態、創辦人/高管公開言論、即時新聞 | `WebSearch: site:twitter.com {主題}` |
| Hacker News | 技術產品評價、工程師圈的真實看法 | `WebSearch: site:news.ycombinator.com {主題}` |
| YouTube | 產品 Demo 影片、創辦人訪談、業界演講 | `WebSearch: {主題} site:youtube.com` |
| Crunchbase | 融資記錄、投資人、估值歷史 | `WebSearch: {公司名} site:crunchbase.com` |

### 台灣特定平台（研究台灣相關主題時必查）

| 平台 | 適用內容 | 搜索方式 |
|-----|---------|---------|
| **Facebook** | 台灣最主要社交平台，品牌粉絲頁、社團討論、用戶評論 | `WebSearch: {主題} site:facebook.com` 或 WebFetch 粉絲頁 |
| **Threads** | 台灣使用量上升，意見領袖、品牌公告、即時討論 | `WebSearch: {主題} Threads 台灣` |
| **Instagram** | 品牌形象、生活風格產品、視覺化呈現、網紅合作 | `WebSearch: {主題} site:instagram.com` |
| **PTT** | 台灣最大論壇，各版真實討論、口碑評價 | `WebSearch: {主題} site:ptt.cc` 或 `PTT {版名} {主題}` |
| **Dcard** | 年輕族群（大學生/25-35歲）的討論、職場版、消費分享 | `WebSearch: {主題} site:dcard.tw` |
| **巴哈姆特** | 科技產品評測、遊戲相關、台灣數位產品討論 | `WebSearch: {主題} site:gamer.com.tw` |

### 中國大陸平台（研究中國相關主題時查）

| 平台 | 適用內容 |
|-----|---------|
| 知乎 | 技術深度討論、產業分析 |
| 微博 | 品牌動態、輿論即時反應 |
| 小紅書 | 消費者真實評價（尤其生活風格類產品）|

### 社交平台查詢詞模板庫
| 平台 | 查詢模板 | 範例 |
|------|---------|------|
| PTT | `site:ptt.cc {版名} {主題}` | `site:ptt.cc Stock TOMRA` |
| Dcard | `site:dcard.tw {主題} {關鍵字}` | `site:dcard.tw 二手衣 回收` |
| Reddit | `site:reddit.com/r/{subreddit} {topic}` | `site:reddit.com/r/investing TOMRA` |
| HN | `site:news.ycombinator.com {topic}` | `site:news.ycombinator.com sorting automation` |
| Glassdoor | `"{公司名}" site:glassdoor.com reviews` | `"Cursor" site:glassdoor.com reviews` |
| LinkedIn | `site:linkedin.com/posts "{名稱}"` | `site:linkedin.com/posts "Perplexity AI"` |
| 知乎 | `site:zhihu.com {主題}` | `site:zhihu.com 二手服装回收` |

---

## 4. URL 擷取策略（多層備援）

### 前置檢查：該平台有沒有 Registry API？

很多平台的前端是 SPA（爬蟲抓不動），但後端有**公開 API** 可直接取結構化資料。
**遇到 SPA 頁面抓取失敗時，第一反應應該是找 API，不是換爬蟲工具。**

**檢測方法**：
1. 試 `curl https://{域名}/api/...` 或 `curl https://registry.{域名}/...`
2. 查 `robots.txt`、`sitemap.xml`
3. 搜尋 `"{平台名} API documentation"` 或 `"{平台名} registry API"`

**已知的 Registry API**：

| 平台 | API 端點 | 備註 |
|------|---------|------|
| Smithery（技能市集） | `registry.smithery.ai/skills/{ns}/{slug}` | 無需認證，支援分頁 `?page=N&pageSize=100` |
| npm | `registry.npmjs.org/{package}` | 無需認證 |
| PyPI | `pypi.org/pypi/{package}/json` | 無需認證 |
| Docker Hub | `registry.hub.docker.com/v2/repositories/{ns}/{repo}` | 無需認證 |
| VS Code Marketplace | `marketplace.visualstudio.com/_apis/public/gallery/extensions/{pub}/{name}` | 無需認證 |
| crates.io | `crates.io/api/v1/crates/{name}` | 無需認證 |
| GitHub | `api.github.com/repos/{owner}/{repo}` | 有 rate limit，認證後更高 |

**使用方式**：透過 `Bash (curl)` 呼叫，回傳 JSON 直接解析。Registry API 拿到基本資料後，從 `gitUrl` 等欄位可進一步取得原始文件。

### 擷取鏈（按優先級）

```
0. Registry API（如目標平台有公開 API，優先用 curl 取結構化 JSON）
1. WebFetch（內建，無限制，首選）
2. tavily_extract（Tavily，支援批次 URL + LinkedIn/受保護網站）
3. crawling_exa（Exa，單一 URL 擷取）
4. r.jina.ai（免費備援，20 RPM 限制）
5. Playwright（終極手段，真實瀏覽器渲染，能繞過 SPA/反爬）
```

### 各工具使用時機

| 場景 | 推薦工具 |
|------|----------|
| 平台有公開 API（npm、PyPI 等） | Bash (curl) 呼叫 Registry API |
| 一般網頁 | WebFetch |
| LinkedIn / 受保護網站 | tavily_extract（extract_depth: "advanced"） |
| 多個 URL 批次擷取 | tavily_extract（urls 陣列） |
| WebFetch + Tavily 都失敗 | crawling_exa |
| 以上全部失敗 | r.jina.ai（加入 jina-queue 排程） |
| SPA 頁面 / JS 渲染內容 / 反爬嚴格 | Playwright（瀏覽器自動化） |

### Jina.ai 管理策略

**限制**：20 RPM（每分鐘最多 20 次）= 約 3 秒/次

```
原始 URL：https://example.com/article
Jina URL：https://r.jina.ai/https://example.com/article
```

- 所有 subagent 遇到全部工具失敗時，將 URL 記錄到 `jina-queue.md`
- 等 Phase 搜索結束後，由主對話統一排程 Jina 批次（每次間隔 3-4 秒）
- 若同時有多個 subagent 需要 Jina，串行執行不並行

**已知問題**：
- Jina Reader 對 Vercel 託管的 SPA 網站會被攔截（429 Vercel Security Checkpoint）
- 遇到 Vercel SPA 時直接跳過 Jina，改用 Registry API 或 Playwright

### Playwright 使用策略

**定位**：終極備援手段，當所有其他工具都失敗時使用。能渲染 JavaScript、繞過 SPA 反爬。

**適用場景**：
- SPA 頁面（Next.js、React 等前端框架渲染的內容）
- 有反爬機制的網站（Vercel Security Checkpoint、Cloudflare 等）
- 需要等待 JS 載入後才能看到內容的頁面
- 需要互動操作（點擊展開、滾動載入）才能取得的內容

**基本流程**：
```
1. browser_navigate → 開啟目標頁面
2. browser_snapshot → 取得頁面可訪問性快照（結構化文字）
3. browser_evaluate / browser_run_code → 進階擷取（執行 JS 取特定資料）
4. browser_close → 用完必須關閉，釋放記憶體
```

**注意事項**：
- 用完必須 `browser_close`，避免 headless 瀏覽器長時間佔用記憶體
- 比其他工具慢很多，不適合批次大量頁面
- 若需批次擷取，優先考慮 Registry API 或其他工具

### 網站完整爬取策略

需要爬取整個文件站或多頁內容時：

```
0. Registry API（如平台有 list 端點，直接分頁取全量結構化資料）
1. tavily_crawl（首選，支援深度/廣度控制 + 路徑篩選）
2. tavily_map 取 URL 列表 → 逐一 WebFetch / tavily_extract
3. WebSearch site:目標域名 → 逐一 WebFetch
4. Playwright（上述全失敗時，用瀏覽器逐頁抓取，適合 SPA 網站）
```

---

## 5. 工具優先級（每個維度的查詢順序）

### 核心原則

- 每個維度至少跨 **2 個不同來源**（內建 + Exa 或 內建 + Tavily）
- 切換時必須跨來源，不可同源切換
- 完整切換鏈見 `agent-config.md` §2

### 通用搜尋優先級

```
1. WebSearch（內建，最即時，無限制，永遠可用的基礎保障）
2. web_search_advanced_exa（Exa，精確篩選 + 語意搜索，10 QPS，全域 10 QPS）
3. tavily_search（Tavily，第三引擎，交叉驗證用）
4. web_search_exa（Exa，快速語意搜索，10 QPS，全域 10 QPS）
```

### 專項工具（依研究類型選用）

| 研究類型 | 專項工具 | 備援 |
|---------|---------|------|
| 公司研究 | company_research_exa（結構化公司資料） | WebSearch + tavily_search |
| 人物研究 | people_search_exa（結構化職涯/教育） | WebSearch + tavily_search |
| 技術研究 | get_code_context_exa（GitHub/SO/官方文件） | WebSearch + tavily_search |
| 深度預研究 | deep_researcher_start → check（非同步 AI 研究） | tavily_research（同步） |

### web_search_advanced_exa 的精確篩選用法

利用進階搜尋的篩選能力提升搜尋精準度：

| 場景 | 建議參數 |
|------|---------|
| 最新動態（近 3 個月） | `startPublishedDate: "YYYY-MM-DD"` |
| 學術論文 | `category: "research paper"` |
| 財報/財務資料 | `category: "financial report"` |
| GitHub 專案 | `category: "github"` |
| 新聞報導 | `category: "news"` |
| 限定特定網站 | `includeDomains: ["arxiv.org", "reddit.com"]` |
| 排除不可靠來源 | `excludeDomains: [...]` |
| 語意搜索模式 | `type: "neural"` |

### URL 擷取優先級

```
0. Registry API（如目標平台有公開 API，用 curl 取 JSON）
1. WebFetch（內建，無限制）
2. tavily_extract（Tavily，批次 + 受保護網站）
3. crawling_exa（Exa，單一 URL）
4. r.jina.ai（免費備援，20 RPM 限制）
5. Playwright（終極手段，瀏覽器渲染，繞過 SPA/反爬）
```

同一維度至少使用 **2 種不同來源的工具**交叉驗證，結果不一致時標記衝突。

---

## 6. 反向查詢規則
**核心原則**：每個維度除了正向搜索，必須包含至少 1 個反向查詢，避免確認偏誤。

**反向查詢模式**：

| 正向查詢 | 反向查詢 | 目的 |
|---------|---------|------|
| `{公司名} advantages` | `{公司名} problems/failures/criticism` | 挖掘負面資訊 |
| `{技術} benefits` | `{技術} limitations/drawbacks/risks` | 找出技術短板 |
| `{市場} growth opportunity` | `{市場} bubble/decline/oversaturated` | 識別泡沫風險 |
| `{產品} review` | `{產品} complaints/issues/lawsuit` | 找真實問題 |
| `{人物} achievements` | `{人物} controversy/scandal/criticism` | 挖掘爭議面 |
| `{商業模式} success story` | `{商業模式} failure/why failed` | 找失敗先例 |

**執行規則**：
1. 每個 subagent 在其負責的維度中，至少 20% 的查詢必須是反向查詢
2. 反向查詢的結果標記為 `[反向]`，在 research-digest.md 中保留
3. 若反向查詢未發現顯著負面資訊，記錄「反向查詢未發現重大負面」（這本身也是有價值的資訊）

---

## 7. 多語言查詢順序配置表
取代原 §1 的模糊規定，提供明確的語言配比和執行順序。

| 研究類型 | 英文佔比 | 地區語言佔比 | 執行順序 |
|---------|---------|------------|---------|
| 技術研究 | 80% | 20% | 英文優先，地區語言為補充 |
| 公司研究（國際公司）| 70% | 30% | 英文基礎 + 當地語言驗證 |
| 公司研究（本地公司）| 40% | 60% | 當地語言為主 + 英文交叉 |
| 產業/市場研究 | 60% | 40% | 並行查詢 |
| 人物研究 | 50% | 50% | 並行查詢 |
| 社會議題 | 40% | 60% | 當地語言為主 + 英文國際觀點 |
| 商業模式可行性 | 60% | 40% | 英文找國際案例 + 當地語言找本地數據 |

「佔比」指 subagent 在該維度的查詢數量中，各語言的建議比例。

---

## 8. 時間切片搜索
**核心原則**：不只搜最新資料，刻意搜索不同時間點的資料用於趨勢分析。

**時間切片規則**：

| 切片 | 搜索時間範圍 | 用途 | 工具建議 |
|------|------------|------|---------|
| 即時切片 | 最近 3 個月 | 掌握最新動態 | WebSearch（預設最新）|
| 近期切片 | 3-12 個月前 | 觀察短期變化 | web_search_advanced_exa（startPublishedDate）|
| 歷史切片 | 1-3 年前 | 分析長期趨勢 | web_search_advanced_exa（startPublishedDate + endPublishedDate）|
| 起源切片 | 3+ 年前 | 追溯源頭/轉折點 | WebSearch（加入年份關鍵字）|

**執行規則**：
1. **標準研究**必須包含至少 2 個時間切片（即時 + 近期或歷史）
2. **深度分析**必須包含至少 3 個時間切片
3. **快速掃描**只需即時切片
4. 時間切片的結果用於構建「趨勢比較表」（如：2023 年員工 200 人 → 2024 年 472 人 → 2025 年 800 人）
5. Phase 1 中至少 1 個 subagent 專門負責歷史資料收集

---

## 9. 語意擴展控制
設定語意擴展的上限和品質控制：

| 規則 | 說明 |
|------|------|
| 每個維度最多 5 組查詢變體 | 超過時按預期資訊回報排序取前 5 |
| 每組查詢變體必須跨語言 | 至少 1 個英文 + 1 個地區語言（如適用）|
| 擴展深度最多 2 層 | 直接相關（第 1 層）+ 間接相關（第 2 層），不擴展到第 3 層 |
| 排除規則 | 移除明顯無關的擴展詞（如搜「TOMRA」不擴展到「挪威旅遊」）|

**品質排序標準**（當查詢變體超過上限時，按以下順序保留）：
1. 正式名稱變體（必留）
2. 直接競爭對手名稱
3. 核心技術/產品名稱
4. 上下游關聯
5. 問題導向查詢
