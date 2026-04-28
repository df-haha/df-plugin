# 調查標準流程（Research Process）

evaluate-business-idea 的支撐流程：市場、GitHub 開源、法規。
五維打分前要先完成市場 + GitHub 調查；法規調查視 idea 性質決定是否需要。

## 一、市場調查 SOP

### Step 1：定義搜尋範圍
- 從 `ideas/{name}/README.md` 讀取問題定義 + 初步構想
- 列中文關鍵字 3-5 組
- 列英文關鍵字 3-5 組
- **先把關鍵字列給使用者確認**，再開始大量搜尋

### Step 2：商業軟體搜尋
- 用 WebSearch 搜尋每組關鍵字
- 每組關鍵字至少看前 5 個結果
- 重點關注：
  - 在地方案（在地語言介面、在地服務、稅務 / 金流整合）
  - 國際知名方案（市場驗證過的模式）
  - 最近 2 年內的新進者（市場趨勢指標）
- 整理為表格：

| 軟體名稱 | 網站 | 定價模式 | 核心功能 | 目標客群 | 優勢 | 劣勢 |
|---------|------|---------|---------|---------|------|------|

### Step 3：市場空白分析
1. 現有方案的共同不足之處？
2. 有沒有被忽視的市場區隔？
3. 我們的差異化機會在哪裡？
4. 進入門檻：技術 / 市場 / 資金（高 / 中 / 低）

### Step 4：寫入結果
寫到 `ideas/{name}/market-research.md`，每個結論附**來源連結**。
找不到的資料一律標「待確認」，**嚴禁編造數字**。

---

## 二、GitHub 開源掃描 SOP

### 搜尋策略

```bash
# 找成熟專案
mcp__github__search_repositories
  query: "{keyword} language:python stars:>100 sort:stars"

# 找活躍專案
mcp__github__search_repositories
  query: "{keyword} language:typescript pushed:>2026-01-01 sort:updated"

# 找特定技術棧
mcp__github__search_repositories
  query: "{keyword} topic:{framework} language:{lang}"

# 搜尋具體實作
mcp__github__search_code
  query: "{specific_function_or_pattern}"
```

### GitHub 搜尋語法
- `language:python` — 指定語言
- `stars:>100` — 最低星數
- `topic:{tag}` — 主題標籤
- `pushed:>YYYY-MM-DD` — 最近更新
- 多關鍵字用空格組合

### 評估標準

| 指標 | 🟢 好 | 🟡 普通 | 🔴 差 |
|------|------|--------|------|
| Stars | > 1000 | 100-1000 | < 100 |
| 最近 commit | < 3 個月 | 3-12 個月 | > 1 年 |
| Issues 回應 | 維護者活躍回應 | 偶爾回應 | 無人回應 |
| License | MIT / Apache | GPL | 無 License |
| 文件品質 | 完整 README + docs | 基本 README | 無文件 |

### 可用性判定

| 等級 | 定義 |
|------|------|
| 直接可用 | clone 就能跑，符合需求 80%+ |
| 需要改造 | 核心邏輯可用，需客製 30-50% |
| 僅供參考 | 架構或部分邏輯可參考，需要重寫 |

### 寫入結果
寫到 `ideas/{name}/github-scan.md`，附搜尋語法 + 可用性總結。

---

## 三、法規調查 SOP

> 不是每個 idea 都需要。如果你的 idea 涉及受管制產業（金融、醫療、廢棄物、食品、運輸、教育、隱私敏感資料）才做。

### Step 1：確定法規範圍
- 涉及哪些產業？（廢棄物、食品、金融、醫療...）
- 主管機關是誰？
- 可能涉及的法規類型？（個資法、消保法、勞基法...）

### Step 2：搜尋法律依據
- WebSearch：「{產業} 法規 {國家/地區}」
- WebSearch：「{主題} 管理辦法」「{主題} 許可」
- WebFetch 政府法規資料庫的相關條文（如有確切 URL）

> 在台灣：全國法規資料庫 `law.moj.gov.tw`、政府公報 `gazette.nat.gov.tw`。
> 在其他地區請替換成當地的政府官方法規來源。

### Step 3：搜尋主管機關公告
- 「{主管機關} {主題} 公告」
- 「{主管機關} {主題} 函釋」

### Step 4：搜尋實務案例
- 「{產業} 違規 裁罰」
- 了解實務上的執法強度
- 找出常見合規陷阱和灰色地帶

### Step 5：寫入結果

寫到 `ideas/{name}/regulation.md`：

| 法規名稱 | 條文 | 主管機關 | 對本 idea 的影響 | 影響程度 |
|---------|------|---------|----------------|---------|

額外整理：
- 需要什麼許可 / 證照 / 資格？
- 合規風險（高 / 中 / 低）
- 灰色地帶說明

---

## 品質標準

1. **每個結論附來源**：URL 或出處，沒有來源就不寫
2. **找不到就標註**：「市場規模：待確認（搜尋未找到可靠來源）」
3. **嚴禁捏造**：不編造市場規模、營收數字、競品 ARR
4. **區分事實與推測**：推測內容加「推測：」前綴
5. **標注調查日期**：市場資料有時效性，每份報告寫日期

## 常用搜尋模板

### 市場調查

```
中文：
- "{核心功能} 軟體"
- "{產業} {解決方案}"
- "{問題} 自動化"
- "{競品} 替代方案"
- "{產業} SaaS"

英文：
- "{core feature} software"
- "{industry} {solution} platform"
- "{problem} automation tool"
- "{competitor} alternative"
- "best {category} software 2026"
```

### GitHub

```
# 成熟度
"{keyword} language:{lang} stars:>100 sort:stars"

# 活躍度
"{keyword} language:{lang} pushed:>2026-01-01 sort:updated"

# 技術棧匹配
"{keyword} topic:{framework}"
```

### 法規

```
# 法律條文
"{法規關鍵字} site:law.moj.gov.tw"   # 台灣
"{regulation keyword} site:gov.uk"    # 英國
"{keyword} federal regulation"        # 美國

# 主管機關
"{機關} {主題} 公告"
"{agency} {topic} guidance"

# 實務案例
"{產業} 違規 裁罰"
"{industry} enforcement action"
```
