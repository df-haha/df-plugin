---
name: deep-research-ryan
description: "Multi-phase deep research engine for companies, products, technologies, industries, people, regions, business models, and social issues. Use when user says 深度研究, 幫我研究, /deep-research, 分析可行性, or wants comprehensive research on any topic — even if they just mention a company or product name curiously."
---

# Deep Research

## 概覽

啟動深度研究引擎：與用戶完成一次對話收集所有細節後，完全自動調度 subagents 執行多階段研究，最終產出結構化報告，包含所有資料來源、信心評級與決策建議，無需用戶在過程中確認任何步驟。

---

## 前置需求檢查（每次啟動時自動執行）

技能啟動後、進入 Phase 0 之前，**必須先執行以下檢查**。

### 工具清單

本技能使用的所有工具：

| 工具 | 來源 | 需要額外安裝？ |
|------|------|---------------|
| WebSearch | Claude Code 內建 | 否 |
| WebFetch | Claude Code 內建 | 否 |
| Task (subagent) | Claude Code 內建 | 否 |
| Write / Read / Edit | Claude Code 內建 | 否 |
| r.jina.ai | 免費公開服務（透過 WebFetch） | 否 |
| **web_search_exa** | **Exa MCP Server** | **是（見下方安裝說明）** |
| **company_research_exa** | **Exa MCP Server** | **是（見下方安裝說明）** |
| **get_code_context_exa** | **Exa MCP Server** | **是（見下方安裝說明）** |

### 檢查步驟

確認以下核心搜索工具可用，若缺少任一項則提示用戶：

1. **檢查 `web_search_exa` 是否可用**（確認 Exa MCP Server 已安裝並連線）
2. **檢查 `WebSearch` 是否可用**

**全部可用** → 直接進入 Phase 0。

**Exa 不可用** → 向用戶顯示以下提示，並用 AskUserQuestion 詢問：

```
⚠️ 偵測到缺少 Exa MCP Server

此技能使用 Exa 的語意搜索（web_search_exa）和公司專項搜索（company_research_exa）
進行多工具交叉驗證，能顯著提升研究品質。

缺少 Exa 時的影響：
- 搜索引擎從 2 種降為 1 種（僅 WebSearch），交叉驗證能力下降
- 無法使用語意搜索，複雜主題的相關性匹配較弱
- 無法使用公司專項搜索，公司/產品類研究的財務數據覆蓋率較低
- 技能仍可運行（內建降級邏輯），但報告中 🟢 已驗證的資料點會減少
```

AskUserQuestion 選項：
- **繼續研究（不安裝）**：「以現有工具執行，接受降級」
- **幫我安裝 Exa**：「引導我完成安裝（免費，不需 API key）」
- **停止，我自己處理**：「結束技能，讓我自行安裝」

### 若用戶選擇安裝 Exa

引導用戶在 Claude Code 中執行：

```bash
claude mcp add --scope user --transport stdio exa npx -y mcp-remote https://mcp.exa.ai/mcp
```

安裝後需重啟 Claude Code 使 MCP Server 生效，再重新觸發 `/deep-research`。

> **說明**：Exa 提供免費的託管 MCP 端點（`mcp.exa.ai/mcp`），透過 `mcp-remote` 連接，不需要註冊帳號或設定 API key。

### 若用戶選擇不安裝

在 MANIFEST 中記錄 `Exa: ❌ 未安裝（降級模式）`，然後正常進入 Phase 0。後續所有 subagent 的工具策略自動調整為僅使用 WebSearch + WebFetch。

---

## Phase 0：初始對話（唯一需要用戶參與的環節）

用 AskUserQuestion 一次收集所有必要資訊（合併成 3-4 題，不分多輪）：

**必問**：
1. 研究主題（確認理解是否正確）
2. 特別關注點（用戶想深挖的方向，例如：供應鏈風險、技術壁壘、創辦人背景、成本結構）
3. 研究深度：快速掃描（1-2 Phase）/ 標準研究（3 Phase）/ 深度分析（4+ Phase 含衝突驗證）
4. 輸出目錄：請用戶提供研究專案的根目錄路徑（例如：`/Users/user/研究專案/`），每次研究會在其中建立獨立子資料夾

**自動判斷**（不需問用戶）：
- 研究類型：依主題自動識別（公司/產品/技術/產業/人物/地區/商業模式/社會議題）
- 查詢語言：依主題自動決定（見 `references/query-strategy.md`）

**收集完畢後：**
1. 生成 MANIFEST
2. 向用戶顯示執行預估：「預計啟動 X 個 subagent（Phase 1: 3 個 + Phase 2: 2-3 個），約需 10-20 分鐘。確認開始？」
3. 用戶確認後自動開始執行

---

## 輸出路徑結構

每次研究在用戶指定的專案根目錄下建立獨立子資料夾：

```
{用戶指定的專案根目錄}/
└── {主題}_{YYYYMMDD}/               ← 每次研究的獨立子資料夾
    ├── {主題}_MANIFEST_{YYYYMMDD}.md   ← 進度存檔（研究開始前建立）
    ├── phase1/                          ← Discovery 搜索結果
    │   └── {維度名稱}_{YYYYMMDD}.md
    ├── phase2/                          ← Deep Search 搜索結果
    │   └── {任務名稱}_{YYYYMMDD}.md
    ├── conflicts_{YYYYMMDD}.md          ← 衝突偵測結果（如有）
    ├── resolution_{YYYYMMDD}.md         ← 衝突解決結果（如有）
    └── report/
        └── {主題}_{研究類型}_{YYYYMMDD}.md  ← 最終報告
```

**所有檔案命名必須包含主題與日期。**

---

## 執行流程

```
Phase 0: 對話 → MANIFEST → 自動執行
    ↓
Phase 1: Discovery（廣度，3 個並行 subagents）
    ↓
Gap Analysis（主對話：缺口識別 + 陌生詞抓取 + 路徑決策）
    ↓
Phase 2: Deep Search（深度，2-3 個並行 subagents）
    ↓
Conflict Detection（1 個衝突偵測 subagent）
    ├─ 無衝突 → Synthesis
    └─ 有衝突 → Phase 3: Resolution Search → Synthesis
    ↓
Synthesis（1-2 個 subagents 撰寫報告）
    ↓
更新 MANIFEST 狀態為 DONE，告知用戶報告路徑
```

---

## 各 Phase 的 Subagent 任務分配

### Phase 1：Discovery 分配原則

根據研究類型，將必要維度（見 `references/dimensions.md`）分組，每組 3-5 個維度交給一個 subagent（共 3 個 subagent）。每個 subagent 必須：
- 使用至少 2 種不同工具查詢同一維度（互相驗證）
- 每個資料點立即附來源 URL + 採集工具名稱
- 遇到工具失敗時執行重試邏輯（見 `references/agent-config.md`）

**Information Relay**：Phase 1 完成後，主 agent 提取「重要發現摘要」（如：重大事件、關鍵人物變動、市場轉向等），嵌入 Phase 2 每個 subagent 的 prompt 中，避免資訊隔離。

### Phase 2：Deep Search 根據 Gap Analysis 動態分配

Gap Analysis 輸出：陌生詞清單、缺漏維度清單、初步衝突清單、特別關注點深挖方向。Phase 2 的 subagents 針對這些具體缺口設計。

### 風險評估與成本分析（必要維度，所有研究類型通用）

無論什麼研究類型，Phase 2 必須包含以下兩個通用 subagent：

**風險評估 Subagent**：
- 財務風險（資金鏈、融資依賴）
- 市場風險（競品威脅、需求萎縮、週期性）
- 技術風險（技術路線過時、核心人才流失）
- 法規風險（監管政策變化、灰色地帶）
- 地緣/供應鏈風險（單一市場/供應商依賴）
- Pre-mortem 分析：相似模式的失敗案例

**成本結構 Subagent**：
- 成本項目拆解（固定成本 vs 變動成本）
- 主要成本驅動因素
- 與競爭對手的成本對比
- 成本優化空間評估
- 對商業可行性的影響評估

---

## Gap Analysis 邏輯

Phase 1 完成後，主對話執行：

1. **陌生詞抓取**：所有 Phase 1 輸出中出現但未解釋的專有名詞 → 加入 Phase 2 查詢
2. **空白識別**：哪些必要維度資料不足？→ 標記補強優先級
3. **衝突初步標記**：同一數據在不同來源有出入 → 記錄待 Phase 2 驗證
4. **路徑決策**：Phase 2 深挖方向（技術深潛 / 財務深潛 / 競爭格局 / 依用戶特別關注點）
5. **代理資料策略**：目標資料找不到時（例如未上市公司無財報），決定使用競爭對手或行業均值推估
6. **用戶確認 Phase 2 方向**：向用戶展示 Phase 1 重要發現摘要和 Phase 2 建議的深挖方向，讓用戶確認或調整後再啟動 Phase 2

---

## Context 監控

每次啟動新批次 subagents 前評估 context 使用量：
- **< 60%**：正常啟動
- **60-75%**：縮減 subagent 數量，優先啟動
- **> 75%**：停止新批次，等當前批次完成並寫入後，輸出：
  ```
  ⚠️ Context 接近上限。所有已完成結果已寫入 {路徑}
  請執行 /compact 後告訴我「繼續研究 {主題}」，我將從 MANIFEST 斷點續跑。
  ```

---

## 斷點恢復

用戶說「繼續研究 {主題}」時：讀取 `{專案根目錄}/{主題}_*/MANIFEST_*.md`，從未完成任務繼續執行。

---

## 參考文件

| 文件 | 內容 | 何時讀取 |
|------|------|--------|
| `references/dimensions.md` | 8 種研究類型的必要維度（含風險/成本）| Phase 0 確認研究類型後 |
| `references/frameworks.md` | 分析框架（PESTEL、Moat、Unit Economics 等）| Synthesis 階段 |
| `references/query-strategy.md` | 多語言查詢、語意擴展、社交平台規則 | 每個 subagent 啟動前 |
| `references/agent-config.md` | Subagent 指令模板、Rate Limit、MANIFEST 格式、重試邏輯 | Phase 0.5 生成 MANIFEST 時 |
| `references/verification.md` | 衝突偵測與解決邏輯 | Conflict Detection 階段 |
| `references/output-template.md` | 報告格式模板（含決策建議區塊）| Synthesis 階段 |
