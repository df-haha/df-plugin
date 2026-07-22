# 衝突偵測與解決邏輯

## 1. 衝突偵測 Subagent 的工作流程

Phase 2 完成後啟動 1 個衝突偵測 subagent，讀取所有 phase1 + phase2 輸出，執行以下判斷：

### 對每個關鍵數據點的判斷規則

```
2+ 來源一致（誤差 < 10%）→ 🟢 高信心，直接採用最新來源
2+ 來源，數據接近但略有出入 → 🟡 中信心，標記最可靠來源，注明差異
只有 1 個來源 → 🟡 中信心，標記需補查
2+ 來源明顯矛盾（數字差異 > 30% 或事實直接相反）→ 🔴 衝突，進入 Resolution Search
完全找不到 → ⬜ 無資料，說明原因（未上市/保密/不存在）
```

### 衝突類型分類

| 衝突類型 | 範例 | 解決策略 |
|---------|------|---------|
| **數字衝突** | 員工人數：一說 472，一說 4025 | 分析時間點差異（Exa 可能是 LinkedIn 子集），查官方年報 |
| **事實衝突** | 成立年份、創辦人資訊矛盾 | 找一手來源（官網/維基/官方公告）裁定 |
| **評價衝突** | 產品一說好評如潮、一說負評頻繁 | 分析樣本來源（官方渠道 vs 用戶論壇），兩者都保留 |
| **時間衝突** | 舊資料 vs 新資料 | 以較新且可信來源為準，舊資料標記時效性 |
| **定義衝突** | 同一指標各方定義不同 | 說明各方定義差異，不強行統一 |

---

## 2. Resolution Search 執行邏輯

對每個 🔴 衝突項目，執行以下步驟：

### Step 1：分析衝突性質

- 數字差異？→ 是否因為統計口徑不同（如 LinkedIn 員工數 vs 總公司員工數）？
- 事實矛盾？→ 是否有時間點問題（舊資料 vs 新資料）？
- 定義差異？→ 各方使用的定義是否相同？

### Step 1.5：反事實推理驗證

在搜尋權威來源之前，先用邏輯推理縮小可能性。對衝突的每一方說法，問：

```
反事實推理流程：

1. 「如果說法 A 為真，那我們應該還能觀察到什麼？」
   → 列出 2-3 個可推導的伴隨事實（例：如果員工數真的是 4025 人，
     那公司應該有多個辦公室、LinkedIn 應有大量員工檔案、招聘規模應很大）
   → 搜索這些伴隨事實是否存在

2. 「如果說法 B 為真，那我們應該還能觀察到什麼？」
   → 同樣列出可推導的伴隨事實並搜索驗證

3. 根據伴隨事實的存在/缺失，更新對 A、B 的信心判斷：
   - 伴隨事實全部吻合 → 顯著增強該說法的可信度
   - 伴隨事實部分缺失 → 信心不變，繼續用權威來源裁定
   - 伴隨事實明顯矛盾 → 顯著削弱該說法的可信度
```

**適用場景**：特別適合無法直接找到官方一手來源的情況（如未上市公司、新創公司、非公開數據）。反事實推理提供了一條間接驗證路徑。

**不適用場景**：純定義衝突或評價衝突不需反事實推理，直接進入 Step 2。

### Step 2：設計裁定查詢

結合 Step 1.5 的反事實推理結果（如有），設計針對性的裁定查詢。

```
優先級（由高到低）：
1. 官方一手文件（官方財報、官方公告、政府公開記錄）
2. 大型可信媒體（Bloomberg、Reuters、WSJ、Financial Times）
3. 多個獨立可信媒體一致
4. 反事實推理的伴隨事實驗證結果（Step 1.5）
5. 維基百科（可用但需注意編輯者立場）
6. 各方說法都保留，標記爭議
```

### Step 3：裁定結論格式

```markdown
## 衝突項目：[項目名稱]

**衝突描述**：
- 來源 A（[工具]/[URL]）：[說法 A]
- 來源 B（[工具]/[URL]）：[說法 B]

**衝突性質**：[數字/事實/時間/定義] 衝突

**反事實推理**（如適用）：
- 若 A 為真 → 應能觀察到：[伴隨事實] → 實際：[存在/缺失]
- 若 B 為真 → 應能觀察到：[伴隨事實] → 實際：[存在/缺失]
- 推理結論：[A 更可信 / B 更可信 / 無法區分]

**Resolution 查詢**：[使用的查詢和工具]

**裁定結果**：
✅ 採用：[正確說法]（根據：[裁定依據 URL]）
或
⚠️ 無法裁定：兩方說法均有依據，保留爭議說明：
- 說法 A 的依據：[...]
- 說法 B 的依據：[...]
- 建議用戶自行查閱：[最可信的來源]
```

---

## 3. 信心評級系統（Synthesis 階段使用）

報告中所有主要數據點必須附信心評級：

| 符號 | 含義 | 條件 |
|-----|------|------|
| 🟢 已驗證 | 2+ 獨立可信來源一致 | 多工具交叉驗證通過 |
| 🟡 單一來源 | 只有一個來源，但來源可信 | 官方文件/主流媒體 |
| 🔴 已裁定衝突 | 有過衝突但已解決 | Resolution Search 裁定後 |
| ⚠️ 推估值 | 無直接資料，由競品/行業均值推估 | 必須標明推估基礎 |
| ⬜ 無資料 | 搜索後仍無法找到 | 說明可能原因 |
| ❗ 爭議未解 | 有衝突但無法裁定 | 保留各方說法 |

---

## 4. 禁止行為

- 不得在沒有來源的情況下陳述任何事實
- 不得把 AI 知識庫的陳述直接寫入報告（需要查詢來源驗證）
- 不得隱瞞衝突（必須標記並處理，不能選擇性採用其中一方）
- 不得對推估值假裝是已驗證事實

---

## 5. 三角驗證強制規則

關鍵數據點（財務數字、市佔率、用戶數、員工數）必須 ≥3 個**獨立**來源交叉驗證。非關鍵數據點維持 ≥2 個來源。

### 關鍵數據點定義

| 數據類型 | 門檻 | 說明 |
|---------|------|------|
| 財務數字（營收/利潤/估值/融資額）| ≥3 獨立來源 | 官方財報 + 權威媒體 + 第三方數據平台 |
| 市佔率/用戶數 | ≥3 獨立來源 | 差異超過 20% 視為衝突 |
| 員工人數 | ≥3 獨立來源 | LinkedIn + 官方 + 第三方 |
| 技術規格（性能指標） | ≥3 獨立來源 | 官方文檔 + 獨立測評 + 學術論文 |
| 一般事實（成立年份、所在地等）| ≥2 來源即可 | 低爭議性 |
| 評價/口碑 | ≥2 來源 | 保留多元觀點，不強制統一 |

### 「獨立來源」操作化定義

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

---

## 6. 來源可信度分級

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

### 衝突裁定優先級

L1 > L2 > L3 > L4 > 反事實推理 > L5 > L6

<!-- BEGIN SHARED:verification-core:access-state v1 sha:aa96c6ef86fd (generated；改 shared/verification-core.md 後跑 node scripts/verify-shared-core.mjs --write，禁止手改本區塊) -->
## 可及性軸（Access State）

可及性（access state）是獨立於可信度的第三軸。

### 原則

1. paywall（付費牆）／登入牆／地區限制＝取得障礙，**不得因此調降來源 L 級**（WSJ、FT、The Information 均為付費牆且屬 L2）
2. 無法重抓驗證的引用標「無法查證（UNVERIFIED）」，不是「錯誤」；兩者必須分開統計
3. 每筆無法查證記錄必附：使用工具、錯誤碼/原因、重試次數、替代來源搜索結果
4. 關鍵主張若只剩無法查證的證據 → 標 ⬜ 並降低該主張信心，不得以 paywall 為由自動放行
<!-- END SHARED:verification-core:access-state -->

### 兩個同級來源衝突的仲裁規則

**舊版漏洞**：兩個 L2 來源寫不同數字時沒講怎麼選，subagent 自由解讀。

**仲裁優先順序（L 級相同時）**：

1. **採集日期較新者勝**（前提：差距 ≥30 天）
2. **若都是近期（≤30 天差），看資料原始日**（如報導引用的研究發表日）
3. **若都同期，引用「更原始」的一方勝**（鏈條較短者）
4. **若原始度相同，看作者/機構在該領域的歷史準確率**（已知有錯誤紀錄者扣分）
5. **若仍無法仲裁，標 ❗ 衝突 + 兩種數字並列 + 標「需 Resolution Search」**

**Subagent 報告中標示仲裁結果**：
```
衝突項目：2024Q4 全球智慧型手機出貨量
- 來源 A：Canalys (canalys.com, 2025-01-15) → 3.21 億支 [L3]
- 來源 B：IDC (idc.com, 2025-01-20) → 3.28 億支 [L3]
仲裁：採用 IDC（依規則 1，採集日較新 5 天 ⚠️ 差距 <30 天）
→ 改採規則 2：兩者資料原始日相同（2024Q4 季報），同期
→ 改採規則 3：兩者皆 L3 自家研究，原始度同
→ 改採規則 5：標 ❗ 並列 3.21-3.28 億，差 2.2% < 衝突門檻 20%，可融合為「約 3.2-3.3 億」
```

---

## 7. 反事實推理量化

在 Step 1.5 反事實推理中加入量化機制，替代主觀判斷。

### 伴隨事實矩陣

- 對每個說法，列出 N 個可推導的伴隨事實（建議 3-5 個）
- 逐一搜索驗證每個伴隨事實
- 計算「驗證通過率」= 已確認伴隨事實數 / 總伴隨事實數

### 判定標準

| 驗證通過率 | 信心判定 | 後續動作 |
|-----------|---------|---------|
| ≥80% | 強力支持 | 可做為裁定依據（等同 L3 來源權重）|
| 60%-79% | 中等支持 | 需搭配其他來源共同裁定 |
| 40%-59% | 不確定 | 反事實推理無法區分，進入 Step 2 裁定 |
| <40% | 顯著削弱 | 該說法可信度大幅下降 |

---

## 8. 衝突影響度評估

Resolution Search 之前，先評估衝突對最終結論的影響度，決定是否值得投入搜尋資源。

### 影響度矩陣

| 影響度 | 條件 | 處理策略 |
|--------|------|---------|
| 🔴 高影響 | 衝突直接影響核心結論/決策建議 | 最高優先 Resolution Search |
| 🟡 中影響 | 衝突影響某個章節但不動搖核心結論 | 標準 Resolution Search |
| 🟢 低影響 | 衝突僅影響細節/背景資訊 | 保留爭議標記 ❗，不啟動 Resolution Search |

---

## 9. 信心聚合規則

報告層面的整體信心計算：

1. 每個數據點有信心評級（🟢/🟡/🔴/⚠️/⬜/❗）
2. 按維度重要性加權：
   - 核心維度（直接影響結論）：權重 3
   - 重要維度（支持核心論述）：權重 2
   - 輔助維度（背景資訊）：權重 1
3. 計算加權信心分數（SSOT：quality-gate.md §0 評級分數表）：
   - 🟢 = 1.0, 🟡 = 0.7, 🔴 = 0.5, ⚠️ = 0.3, ❗ = 0.2, ⬜ = 0
4. 整體信心 = Σ(評級分數 × 權重) / Σ(權重)
5. 報告附「整體信心指數」：≥0.7 高 / 0.5-0.69 中 / <0.5 低

---

## 10. 時效性驗證

所有數據點必須標記資料時間：

| 時效標記 | 條件 | 報告呈現 |
|---------|------|---------|
| 🕐 即時 | 資料日期在 3 個月內 | 正常使用 |
| 🕐 近期 | 資料日期在 3-12 個月內 | 正常使用，標注日期 |
| ⏰ 需注意 | 資料日期在 1-3 年前 | 標注「⏰ 資料時間：YYYY」，在報告品質說明中列出 |
| ⚠️ 過時風險 | 資料日期超過 3 年 | 標注「⚠️ 資料可能過時（YYYY）」，在 QG 中觸發警告 |

### Quality Gate 時效性檢查

若報告中 >20% 數據點標記 ⏰ 或 ⚠️ → 觸發 PASS_WITH_WARNINGS
