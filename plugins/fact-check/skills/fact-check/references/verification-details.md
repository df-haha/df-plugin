# 5 層查證詳細 Checklist

> 由原 8 層精簡合併：層 1 保留、層 2+8 合併、層 3+4 合併、層 5+6 合併、層 7 保留

---

## 第一層：事實查核

- [ ] 實體是否存在（公司/技術/人物/廠商/服務）
- [ ] 名稱拼寫正確（多語言檢查）
- [ ] 基本資訊準確（時間、地點、規模）
- [ ] 關鍵人物真實存在（CEO、創辦人、專家）

## 第二層：時效性 + 來源查證

### 時效性
- [ ] 實體當前狀態（營運中/已停業/已被收購/服務下線）
- [ ] 已停業/下線仍需驗證存在性與商業模式（用於案例分析）
- [ ] 重大變更（收購/合併/停業/轉型）
- [ ] 資料時效性（超過 12 個月標註 ⚠️，6-12 個月標註提醒）
- [ ] 是否有最新發展未反映
- [ ] 歷史案例需標註時間背景

### 來源查證
- [ ] 每個來源的可信度評級（L1-L6，見下方共用查核核心）
- [ ] 付費牆/不可及屬 access 軸，不得因此降低 L 級
- [ ] 公關稿/付費刊登 → 標註為公司視角（影響獨立性軸），不逕降可信度
- [ ] 是否有立場偏見或利益衝突
- [ ] 所有關鍵數據是否標註來源
- [ ] 為正確但缺來源的資訊補充 3+ 個可靠來源

## 第三層：交叉驗證 + 數據溯源

### 交叉驗證
- [ ] 至少 3 個獨立來源交叉比對（「獨立」判定見共用查核核心之獨立來源規則；同一原始研究的多篇轉述只算 1 個）
- [ ] 官方來源（官網、財報）
- [ ] 第三方來源（新聞、產業報告）
- [ ] 社群驗證（LinkedIn、Glassdoor、GitHub）

### 數據溯源與計算
- [ ] 追溯原始資料來源
- [ ] 驗證數據計算方法
- [ ] 檢查是否斷章取義
- [ ] 數學正確性：百分比加總、成長率計算、CAGR、市場規模推算
- [ ] 統計合理性：樣本數、統計顯著性
- [ ] 單位一致性：同指標一致單位、貨幣標註、時間範圍一致

## 第四層：邏輯一致性 + 隱藏錯誤

### 邏輯一致性
- [ ] 前後文是否矛盾
- [ ] 數據推論是否合理
- [ ] 因果關係是否成立
- [ ] 結論是否過度推論

### 隱藏錯誤偵測

完整 7 項見共用查核核心之數據自洽 Checklist。

## 第五層：技術與術語查證

### 技術查核（反籠統）
反籠統規則在技術架構/白皮書章節嚴格執行，商業概述允許合理概括。之所以要求具體術語，是因為模糊技術描述可能導致讀者對技術能力產生錯誤預期。
- [ ] 技術/框架是否真實存在（官方文檔/GitHub 可查證）
- [ ] 技術名稱拼寫正確（大小寫、版本號）
- [ ] 禁止籠統描述（技術章節中）：❌「AI 技術」→ ✅ 具體框架名+版本
- [ ] 技術版本是否為當前版本
- [ ] 技術生命週期狀態（活躍/維護/停止開發）
- [ ] 流行詞濻用檢測

### 專業術語
- [ ] 商業術語使用是否符合定義（B2B/B2C、SaaS/PaaS、MVP/PMF）
- [ ] 產業術語使用準確性

---

## 共用查核核心（Shared Verification Core）

<!-- BEGIN SHARED:verification-core:source-class v1 sha:f532c86104de (generated；改 shared/verification-core.md 後跑 node scripts/verify-shared-core.mjs --write，禁止手改本區塊) -->
## 來源可信度分級

### 軸分離說明

L1-L6 **只評估來源可信度**（該來源發布的資訊有多大機率為真）。其他維度獨立處理：
- **獨立性**：由三角驗證規則另行判定（同源 N 篇 = 1 個獨立來源），不影響 L 級
- **可及性**（paywall / unreachable）：是第三軸，不得因 paywall 降 L 級——付費牆只影響驗證可行性，不影響來源品質

### 6 級可信度分級表

| 等級 | 來源類型 | 權重 | 範例 |
|------|---------|------|------|
| L1 一手來源 | 官方財報、SEC Filing、政府公報、官方 API | 最高 | 10-K、年報、公司公告 |
| L2 權威媒體 | Bloomberg、Reuters、WSJ、FT、TechCrunch（報導非評論）| 高 | 新聞報導、專訪 |
| L3 產業報告 | Gartner、McKinsey、CB Insights、Statista | 高 | 付費研究報告 |
| L4 專業社群 | HN、Reddit（高贊回答）、Stack Overflow、GitHub Issue | 中 | 工程師/從業者一手經驗 |
| L5 一般媒體 | 一般新聞網站、部落格、Medium、個人網站 | 低 | 需交叉驗證 |
| L6 AI/聚合 | 維基百科、AI 生成摘要、SEO 內容農場 | 最低 | 僅作線索，不可直接引用 |

### 30+ 常見網域對照表

查核者拿到 URL 後對照本表歸級；表中沒有的網域用「擁有者背景 + 內容類型」推導（範例：`somecompany.com/blog/` 是公司自家部落格 → L5 一般媒體；`somelab.edu/paper/` 是學術 → L1-L3 看是否經同行評審）。

#### L1 一手來源（官方/政府/SEC）
- `sec.gov`（美國證券交易委員會 EDGAR）
- `bls.gov`、`bea.gov`、`census.gov`（美國勞工/經濟/人口統計）
- `eur-lex.europa.eu`（歐盟法規）
- `stat.gov.tw`、`mof.gov.tw`、`moea.gov.tw`（台灣官方）
- 任何 `{公司域名}/investors/`、`{公司域名}/press/`（IR 投資人關係頁、官方公告；L1 僅限公司自述事實如財務數字、產品規格，涉及評價或比較時屬公司立場，需獨立第三方佐證）
- `arxiv.org`（preprint 未經同儕審查，屬一手文獻但引用時標註 preprint）、`pubmed.ncbi.nlm.nih.gov`
- `scholar.google.com`（索引工具——引用時以其指向的原始論文為來源，不以 scholar 本身為 L1）
- GitHub 官方 README / RELEASES（在 `github.com/{org}/{repo}` 下，非 issue 推論）

#### L2 權威媒體
- `bloomberg.com`、`reuters.com`、`wsj.com`、`ft.com`（彭博/路透/華爾街日報/金融時報）
- `economist.com`（經濟學人）
- `nytimes.com`、`washingtonpost.com`、`theguardian.com`（紐時/華郵/衛報）
- `nikkei.com`（日經）、`scmp.com`（南華早報）
- `cw.com.tw`、`businessweekly.com.tw`（天下/商周；台灣語境 L2）
- `techcrunch.com`（報導，**非** opinion / editorial）
- `theinformation.com`（科技深度報導，付費牆）

#### L3 產業報告/分析機構
- `gartner.com`、`forrester.com`、`idc.com`、`statista.com`
- `mckinsey.com/insights`、`bcg.com`、`bain.com`、`deloitte.com/insights`
- `cbinsights.com`、`pitchbook.com`、`crunchbase.com`（投資/新創）
- `counterpointresearch.com`、`canalys.com`、`omdia.com`（科技市佔率）
- `similarweb.com`、`semrush.com`、`ahrefs.com`（網路流量/SEO）

#### L4 專業社群/從業者
- `news.ycombinator.com`（Hacker News，高分回答）
- `reddit.com/r/{programming|MachineLearning|stocks|...}`（高 upvote 回答）
- `stackoverflow.com`、`stackexchange.com`（accepted answers）
- GitHub issues / discussions（**項目維護者回答**才算 L4，路人回答 L5）
- `lwn.net`、`thenewstack.io`（資深技術社群）
- `seekingalpha.com`（buy-side analyst 文章，需看作者資歷）

#### L5 一般媒體/部落格
- `medium.com`、`substack.com`、個人部落格
- `theverge.com` / `engadget.com` / `arstechnica.com`（opinion / editorial 部分）
- 一般地方新聞網
- 公司公關稿 / 業配文（要標註並當輔助）

#### L6 AI/聚合/百科
- `wikipedia.org`（**僅作線索**，需追溯到原始引用才能採信）
- `baidu.com/baike` 百度百科
- ChatGPT / Perplexity / Google AI Overview / Bing Chat 等 AI 摘要
- `quora.com`（除非作者是領域權威）
- SEO 內容農場（如 `*-howto.com`、`*-guide.io` 等模式）
<!-- END SHARED:verification-core:source-class -->

<!-- BEGIN SHARED:verification-core:independence v1 sha:a475065f144d (generated；改 shared/verification-core.md 後跑 node scripts/verify-shared-core.mjs --write，禁止手改本區塊) -->
## 獨立來源判定規則

> 需要幾個獨立來源才達標由各 plugin 自訂（本核心只定義「什麼算獨立」）。

### 「獨立」判定規則（從嚴）

1. **同一份原始研究/資料庫的 N 篇轉述 = 1 個獨立來源**（無論轉述者多權威）
   - 範例：TechCrunch、The Verge、Reuters 都引「Gartner 2025 Q1 雲端市佔報告」→ 算 1 個（Gartner）不是 3 個
   - 範例：Bloomberg、WSJ、FT 都引「SEC 10-K Form」→ 算 1 個（SEC filing）不是 3 個

2. **無論門檻幾個來源，其中至少 2 個必須追溯到不同的原始研究/資料庫，才可能構成 ≥2 個獨立來源**
   - 例如：Gartner 報告 + IDC 報告 + 公司財報 = 3 個獨立
   - 例如：Gartner 報告 + 3 篇引 Gartner 的媒體 = 仍只有 1 個
   - 例如：Gartner 報告 + Gartner 另一份不同主題報告 = 半獨立（同機構不同研究，視為 1.5 個）

3. **官方公司來源不能與「公司公關稿轉述媒體」混算**
   - 例如：Apple 10-K + Apple Newsroom 公告 + 引 Apple 公告的 TechCrunch 報導 = 1 個獨立來源（都是 Apple 視角）
   - 需要再加 1 個以上獨立第三方（如 Counterpoint Research、Canalys）才算 2 個以上

4. **AI 摘要不算獨立來源**
   - ChatGPT / Perplexity / Google AI Overview 等的摘要 → L6，僅作線索，不計入三角驗證的 N

5. **同一作者的 N 篇文章 = 1 個獨立來源**
   - Ben Thompson Stratechery 一週寫 3 篇都引用同一推理 → 算 1 個（Thompson 視角）

### 查核輸出中標示獨立性

```
財務數字驗證：營收 $X
- 來源 1：Apple 10-K (sec.gov, 2025-Q1) [L1，原始資料]
- 來源 2：Bloomberg 報導 (引 10-K, 2025-04-15) [L2，但同一原始 = 不計入獨立]
- 來源 3：Canalys 報告 (canalys.com, 2025-03) [L3，獨立研究]
- 來源 4：Counterpoint Research (counterpointresearch.com, 2025-04) [L3，獨立研究]
→ 獨立來源數：3（10-K / Canalys / Counterpoint）（是否達標依各 plugin 自訂門檻判定）
```
<!-- END SHARED:verification-core:independence -->

<!-- BEGIN SHARED:verification-core:access-state v1 sha:aa96c6ef86fd (generated；改 shared/verification-core.md 後跑 node scripts/verify-shared-core.mjs --write，禁止手改本區塊) -->
## 可及性軸（Access State）

可及性（access state）是獨立於可信度的第三軸。

### 原則

1. paywall（付費牆）／登入牆／地區限制＝取得障礙，**不得因此調降來源 L 級**（WSJ、FT、The Information 均為付費牆且屬 L2）
2. 無法重抓驗證的引用標「無法查證（UNVERIFIED）」，不是「錯誤」；兩者必須分開統計
3. 每筆無法查證記錄必附：使用工具、錯誤碼/原因、重試次數、替代來源搜索結果
4. 關鍵主張若只剩無法查證的證據 → 標 ⬜ 並降低該主張信心，不得以 paywall 為由自動放行
<!-- END SHARED:verification-core:access-state -->

<!-- BEGIN SHARED:verification-core:data-consistency v1 sha:c32e678f2294 (generated；改 shared/verification-core.md 後跑 node scripts/verify-shared-core.mjs --write，禁止手改本區塊) -->
## 數據自洽 Checklist

### Canonical 7 項檢查

1. 跨章節同指標數字一致
2. 單位換算（萬/億、million/billion）
3. 幣別與匯率日期、名目/實質金額
4. 資料期間 vs 發布日期錯位（過去數據描述為現況）
5. 地域範圍混淆（全球/區域/國家）
6. 百分比 vs 百分點 vs 絕對數
7. 加總、成長率、衍生計算正確性

### Warning 記錄格式

每筆 warning 必附以下 4 欄：

| 欄位 | 說明 |
|------|------|
| 原始值 | 報告中出現的原始數字/表述 |
| 正規化值 | 統一單位/幣別後的值 |
| 兩處位置 | 不一致出現的兩個位置（章節+段落） |
| 換算假設 | 採用的匯率日期、單位換算基準等 |
<!-- END SHARED:verification-core:data-consistency -->
