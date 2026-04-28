---
name: evaluate-business-idea
description: Use when assessing whether a software/SaaS/business idea is worth building, evaluating competitive moat, scoring product stickiness, or deciding between multiple ideas. Triggers on requests like "evaluate this idea", "should I build X", "五維評估", "值不值得做", "這個 idea 怎麼樣", "幫我打分", "深度系統 vs 免洗系統". Suitable for solo founders, internal product leads, and consultancies screening client opportunities.
---

# 評估商業點子（Evaluate Business Idea）

## Overview

用「**五維深度系統評估框架**」幫一個軟體/SaaS/服務點子打分，判定是「深度系統（拔不掉）」還是「免洗系統（隨時被換掉）」。核心問題：

> **拔掉這個系統，客戶會怎樣？**
>
> - 「換一個就好」→ 免洗系統，不要做
> - 「業務會中斷」→ 深度系統，全力投入

評估前需要做完市場/競品/法規調查，靠搜尋結果而不是憑感覺打分。

## When to use

- 在某個 idea 上要不要繼續投入時
- 多個 idea 要排優先順序
- 對客戶提案前先自我檢驗 moat
- 想釐清這個 idea 屬於主業 vs. 副產品

**Don't use for**：
- 純技術可行性問題（「這能不能做出來」）→ 先做技術 spike
- 已決定要做、只想規劃 roadmap → 用其他 planning skill
- 早期還沒收斂的 brainstorm → 先做 brainstorming，有具體 idea 才能打分

## Workflow

```
1. 記錄 idea (idea-capture)
   └→ 建立 ideas/{name}/ 資料夾，填 README

2. 市場調查
   ├→ 商業競品（中英文 WebSearch）→ market-research.md
   └→ GitHub 開源（mcp__github__search_repositories）→ github-scan.md

3. 法規調查（視 idea 性質可選）
   └→ 主管機關 + 全國法規資料庫 → regulation.md

4. 五維評估打分
   └→ 5 個維度 0/1/2 分 → five-dim-eval.md

5. 決策
   └→ 做 / 不做 / 暫緩 → decision.md
```

每一步的細節在 [references/research-process.md](references/research-process.md)；五維框架完整定義在 [references/evaluation-framework.md](references/evaluation-framework.md)。

## 五維評估快速參考

| # | 維度 | 核心問題 | 0 分 | 1 分 | 2 分 |
|---|------|---------|------|------|------|
| 1 | 人力替代性 | 拔掉後，要補回幾個人？ | 0 人 | 半人 | ≥1 全職 |
| 2 | 數據累積性 | 用越久越有價值嗎？ | 否 | 部分 | 強累積 |
| 3 | 流程嵌入度 | SOP 長在系統上嗎？ | 否 | 部分依賴 | 完全依賴 |
| 4 | 決策編碼 | 系統掌握「人腦規則」嗎？ | 否 | 部分編碼 | 核心已編碼 |
| 5 | 重建成本 | 換一套要多久？ | < 1 月 | 1-6 月 | > 6 月 |

**判定**：
- 8-10 分 🟢 深度系統 → 全力投入
- 5-7 分 🟡 有潛力 → 做但要規劃加深路徑
- 0-4 分 🔴 免洗系統 → 不做或只賣斷

**紅線檢驗（不可違反）**：
- 維度 1 ≥ 1（能淘汰人，不只是方便）
- 維度 2-5 總和 ≥ 4（具備深度特徵，不是純工具）

## 檔案結構

每個 idea 用獨立資料夾，標準 6 檔：

```
ideas/{idea-name}/
├── README.md           # 概要 + 調查進度 checklist
├── market-research.md  # 商業競品 + 市場空白
├── github-scan.md      # 開源專案掃描
├── regulation.md       # 法規盤點（可選）
├── five-dim-eval.md    # 五維打分結果
└── decision.md         # 最終決策
```

模板放在 [idea-template/](idea-template/)，第一次評估前複製到你想存放 idea 的位置（建議 `ideas/{name}/`）。

## 執行流程細節

### Step 1：建立 idea 資料夾
複製 `idea-template/` 到 `ideas/{idea-name}/`，填 `README.md`：問題定義、初步構想、觸發點。

### Step 2：市場調查
中英文各 3-5 組關鍵字，跑 WebSearch + GitHub MCP。每個結果填表格，最後寫「市場空白分析」與「差異化機會」。詳見 [references/research-process.md](references/research-process.md)。

### Step 3：法規調查（如涉及受管制產業）
判斷主管機關 → 搜「法規 + 主題」+「主管機關公告」+「違規裁罰」。詳見 [references/research-process.md](references/research-process.md)。

### Step 4：五維打分
讀 references/evaluation-framework.md 的維度定義，逐維度給分，**每個分數附 2-3 句理由**。算總分、判定、跑紅線檢驗。詳見 [references/evaluation-framework.md](references/evaluation-framework.md)。

### Step 5：商業模式草案（總分 ≥ 5 才填）
- 定價：客戶能省多少 → 報價 = 價值的 10-30%
- 客群：誰最痛？
- 營收結構：一次性 / 持續性 / 加值 比例
- **副產品檢驗**：這是為了優化本業，還是為了外部客戶？

### Step 6：決策
寫 `decision.md`：做 / 不做 / 暫緩 + 理由 + 下一步。

## Common Mistakes

| 陷阱 | 修正 |
|------|------|
| 沒做調查就直接打分 | 五維每個分數必須有依據，至少先完成市場 + GitHub 調查 |
| 維度 1 = 0 但其他都高 | 紅線檢驗失敗 → 不能算深度系統，重新評估 |
| 用「未來會...」幫高分辯護 | 打分基於**現狀**，未來計畫放「深化路徑」區塊 |
| 把工具型方案打成深度系統 | 工具型（發票 OCR、SEO 文章）總分通常 0-3，誠實打 |
| 副產品 vs. 主業沒區分 | 工具是為了讓本業更強，不是為了賣 SaaS。混淆會導致策略發散 |
| 捏造市場數據 | 找不到就標「待確認」，不要編 |

## 校準範例

跟已評估產品比對，幫助校準分數：

| 產品類型 | 總分 | 判定 |
|---------|------|------|
| 對帳自動化（深度業務嵌入） | 10 | 🟢 |
| 客服 + 派單系統 | 9 | 🟢 |
| 內部庫存透明化平台 | 9 | 🟢 |
| 車輛機具點檢 App | 5 | 🟡 |
| 發票 OCR | 3 | 🔴 |
| 標案爬蟲 | 1 | 🔴 |
| SEO 文章生成 | 0 | 🔴 |

完整校準表（含每個維度的子分數）在 [references/evaluation-framework.md](references/evaluation-framework.md)。

## Reference 檔案

- [references/evaluation-framework.md](references/evaluation-framework.md) — 五維框架完整定義 + 商業模式原則 + 校準表 + 評估輸出模板
- [references/research-process.md](references/research-process.md) — 市場 / GitHub / 法規調查 SOP + 搜尋模板
- [idea-template/](idea-template/) — 6 檔 idea 資料夾模板
- [USAGE.md](USAGE.md) — 觸發詞、標準流程、自訂校準與法規來源、FAQ
