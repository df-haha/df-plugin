---
name: intel-scan
description: 情報雷達的 First-Principles 分析框架（本質？關聯？行動？）。來源/關鍵字/產業脈絡來自 oc-config，零 hard-code。僅在 config.modules.intel.enabled=true 時由 cockpit 呼叫。
allowed-tools: Bash, Read, WebFetch
---

# Intel Scan Skill（情報雷達 — First-Principles 分析）

把抓到的情報用第一性原理框架分析成可行動的決策資訊。**所有產業脈絡（公司業務範疇、關鍵字、
RSS 來源、競爭對手）一律取自 `config.modules.intel` 與 `config.identity`，不寫死任何特定產業。**

> 前置：本 skill 只負責「分析」。抓取由 `scripts/intel_crawler.py --config` 完成（依
> `config.modules.intel.storage` 決定落地後端：quick_only 不落 DB / sqlite / postgres）。

## First-Principles 三問

對每則情報問三個問題，組成分析：

1. **本質**：這則新聞「真正在講什麼」？用一句話拆解（去除標題包裝）。
2. **關聯**：跟「我」的業務（`config.identity` + `modules.intel.keywords`）有何關聯？評 🟢高 / 🟡中 / ⚪低。
3. **行動**：若關聯高，具體可採取什麼動作？估算降本/增效價值。

## 範疇與優先序

來源範疇與優先序可由 config 的關鍵字群推導；預設優先序（高→低）：
**法規/政策 > 產業/原物料 > 競爭對手 > 技術趨勢 > 地方新聞**。

- **每範疇取 top 3**：不是一個 LIMIT 塞滿，而是每範疇各取最相關 3 則。
- 🟢 高關聯項目可在表格下方補 1-2 行策略展開。

## 輸出格式（5 欄表，每範疇一張）

```markdown
### 🔴 {範疇名}（直接業務衝擊）
| 標題 | 本質 | 關聯性 | 建議行動 | 價值 |
|------|------|--------|----------|------|
| [標題](URL) | 1 句話拆解 | 🟢高 — 原因 | 具體動作 | 降本/增效估算 |
```

## 注意

- **不捏造**：關聯性與價值估算須基於情報實際內容，無法判斷時標「待確認」。
- **來源透明**：表格上方標各範疇來源數（`來源：環境 ×N、產業 ×N…`），數字來自實際抓取結果。
