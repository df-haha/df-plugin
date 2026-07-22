# Synthesis Subagent 分工規範

> **執行者**：2-3 個並行 Synthesis Subagent
> **輸入**：`research-digest.md`（由 Gap Analysis Subagent 產出的精華摘要）+ 此規範 + 相關 references
> **輸出**：合併為最終報告

---

### v2 強制：資料點級評級

所有 Synthesis subagent（S-1/S-2/S-3）產出的報告中，每個具體斷言/數字/結論必須附「信心符號 + L 級 + URL + 日期」4 項（詳見 frameworks.md §8 v2 強制規則）。漏附 >10% 觸發 QG WARNINGS。

---

## Subagent 分工

### S-1：分析報告（Analysis Layer）

負責報告**前半部——分析層**。

**讀取**：
- `research-digest.md`（Phase 1+2 精華摘要）
- `phase2/*.md`（Phase 2 深度搜索原始輸出）
- `references/output-template.md`（報告格式模板）
- `references/frameworks.md`（分析框架）
- `conflicts_*.md` / `resolution_*.md`（如有）

**任務**：
- 整合 Phase 1 + Phase 2 所有資料
- 套用 output-template.md 的報告結構（含類型特定章節）
- **每個維度必須包含至少 1 個橫向對標表**（≥3 個比較對象）
- 所有數據點附信心評級和來源引用
- 衝突數據按裁定結果呈現
- **執行 Steel-man 反論**：使用 `frameworks.md` §10，對 3 個核心結論構建最強反論並搜索證據
- **整合 Devil's Advocate 結果**：若 `phase2/devils-advocate_*.md` 存在，將其發現整合進對應章節
- 產出章節：執行摘要 → 類型特定主體 → 風險評估 → 成本結構 → 假說驗證結果 → **Devil's Advocate 分析** → **假設審計** → 決策建議 → 情境展望 → 資料品質說明 → 關鍵缺失信息

---

### S-2：行動手冊（Action Handbook Layer）

負責報告**後半部——行動層**。

**讀取**：
- `research-digest.md`
- `phase2/*.md`（Phase 2 深度搜索原始輸出）
- `references/output-template.md`（行動手冊模板區段）

**任務**：
- **供應商/方案優先度排序表**：按 4 級分類（🔴 立即聯繫 / 🟡 短期評估 / 🟢 中期觀望 / ⚪ 備選）
- **分階段實施路線圖**：Phase 1/2/3 或月度級別，含各階段成本與里程碑
- **成本分項計算**：環節級別（硬體/軟體/人力/維運），非籠統描述
- **下一步行動清單**：具體到聯繫誰、做什麼、預算多少、什麼時間點
- **關鍵決策節點**：何時做什麼決定，決策依據是什麼

**品質要求**：
- 供應商必須有聯繫管道或獲取方式
- 成本估算必須附信心評級
- 行動清單每項必須可操作（有具體對象和時間）

---

### S-3（條件啟用）：前瞻分析 + 批判分析

#### 啟用條件
| 研究深度 | 啟用規則 |
|---------|---------|
| 深度分析 | **強制啟用** |
| 標準研究 | 當 Gap Analysis 發現 ≥2 個爭議性結論時啟用 |
| 快速掃描 | **不啟用**（S-1 + S-2 即可）|

> Workflow 模式：standard + 爭議結論 ≥2 時，由主對話以 `args.s3=true` 傳入強制啟用。

**讀取**：
- `research-digest.md`
- `phase2/*.md`（Phase 2 深度搜索原始輸出，含 devils-advocate_*.md 如已執行）
- `references/frameworks.md`（情境分析 §9 + Steel-man §10 + 假設審計 §11）
- `gap-analysis_*.md`（假設審計預備資料，步驟 9）

**任務**：
- **三情境展望**（樂觀/基準/悲觀，含觸發條件、驗證指標、**二階效應分析**）
- **假說驗證結果表**（Phase 2 假說的 ✅/❌/⚠️ 結論彙整）
- **Pre-mortem 分析**（5 大失敗情境 + 早期警訊 + 預防措施）
- **假設審計**：使用 `frameworks.md` §11，對核心結論的隱含假設進行系統性審計
- **整合 Devil's Advocate 結果**：若 Devil's Advocate subagent 已執行，將其 Steel-man 反論和失敗先例整合進情境分析和 Pre-mortem

---

## 合併規則

S-1、S-2（、S-3）的輸出合併為最終報告，順序為：

```
分析層（S-1）→ 行動層（S-2）→ 前瞻層（S-3，如有）→ 附錄：資料來源
```

**執行方式**（依編排模式）：
- **Workflow 模式**：由 workflow Merge agent 執行合併，產出的最終報告為 Citation Verify / QG 的驗證對象
- **Task 模式**：由主對話合併，合併完成後才跑 Citation Verify / QG

**S-1 / S-2 為必要交付物**，缺任一不得標 DONE（workflow 模式下缺必要角色回 NEED_MANUAL_REVIEW）。

合併時檢查：
- S-1 和 S-2 的數字是否一致（如成本章節 vs 成本分項表）— **差異 >5% 視為不一致，需修正並在 notes 註記**
- 無重複章節
- 目錄正確反映所有章節
- **決策建議與 Steel-man 反論一致性**：韌性「弱」的結論是否在建議中附加條件
- **假設審計完整性**：核心結論是否都有對應的假設審計條目
- **研究參數記錄**（附錄 B）是否完整填入
