# 查詢策略：多語言、語意擴展、社交平台

## 1. 多語言查詢規則

**英文（必查）**：任何主題都必須包含英文查詢。技術文獻、國際公司、學術資料大多以英文為主。

**依主題自動加入的語言**：

| 主題類型 | 額外語言 |
|---------|--------|
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
|-----|---------|--------|
| Reddit | 產品真實評價、技術討論、用戶投訴、業界吐槽 | `WebSearch: site:reddit.com {主題}` |
| LinkedIn | 公司員工動態、高管發言、職位招聘（判斷方向）| 直接查公司頁面或人物頁面 |
| Glassdoor | 員工文化評價、薪資範圍、管理風格 | `WebSearch: {公司名} Glassdoor review` |
| X (Twitter) | 最新動態、創辦人/高管公開言論、即時新聞 | `WebSearch: site:twitter.com {主題}` |
| Hacker News | 技術產品評價、工程師圈的真實看法 | `WebSearch: site:news.ycombinator.com {主題}` |
| YouTube | 產品 Demo 影片、創辦人訪談、業界演講 | `WebSearch: {主題} site:youtube.com` |
| Crunchbase | 融資記錄、投資人、估值歷史 | `WebSearch: {公司名} site:crunchbase.com` |

### 台灣特定平台（研究台灣相關主題時必查）

| 平台 | 適用內容 | 搜索方式 |
|-----|---------|--------|
| **Facebook** | 台灣最主要社交平台，品牌粉絲頁、社團討論、用戶評論 | `WebSearch: {主題} site:facebook.com` 或 WebFetch 粉絲頁 |
| **Threads** | 台灣使用量上升，意見領袖、品牌公告、即時討論 | `WebSearch: {主題} Threads 台灣` |
| **Instagram** | 品牌形象、生活風格產品、視覺化呈現、網紅合作 | `WebSearch: {主題} site:instagram.com` |
| **PTT** | 台灣最大論壇，各版真實討論、口碑評價 | `WebSearch: {主題} site:ptt.cc` 或 `PTT {版名} {主題}` |
| **Dcard** | 年輕族群（大學生/25-35歲）的討論、職場版、消費分享 | `WebSearch: {主題} site:dcard.tw` |
| **巴哈姆特** | 科技產品評測、遊戲相關、台灣數位產品討論 | `WebSearch: {主題} site:gamer.com.tw` |

### 中國大陸平台（研究中國相關主題時查）

| 平台 | 適用內容 |
|-----|--------|
| 知乎 | 技術深度討論、產業分析 |
| 微博 | 品牌動態、輿論即時反應 |
| 小紅書 | 消費者真實評價（尤其生活風格類產品）|

---

## 4. Jina.ai 爬取策略（WebFetch 失敗時的備援）

當 WebFetch 回傳 403/429/503 時，直接嘗試 `https://r.jina.ai/{原始URL}`。若 Jina 也失敗，跳過該來源並在輸出中標記。

```
原始 URL：https://example.com/article
Jina URL：https://r.jina.ai/https://example.com/article
```

---

## 5. 工具優先級（每個維度的查詢順序）

```
1. WebSearch（最即時，無限制）
2. web_search_exa（語意搜索更強，10 QPS）
3. company_research_exa（公司財務/人員專項，適合公司研究）
4. get_code_context_exa（技術/API/開源，適合技術研究）
5. WebFetch（抓取具體頁面，目標 URL 確定後使用）
6. r.jina.ai/（WebFetch 失敗的備援，受 20 RPM 限制）
```

同一維度至少使用 **2 種工具**交叉驗證，結果不一致時標記衝突。
