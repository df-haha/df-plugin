# 圖片內文字與 CJK 處理

## 核心原則

1. **不預設中文會糊。** 不要主動縮短標籤、減少文字量、或寫「CJK 可能有偏差請接受」之類的免責聲明。（此為過去的明確修正：曾有 agent 基於未經測試的臆測，斷言圖像生成會模糊密集中文。）

2. **先生一張驗證。** 如果實際產出確實模糊，THEN（才）縮短文字——並將其作為實測結果回報，不是預設假設。

3. **圖內中文一律指定「正體中文 only（禁簡體）」**，生成後逐圖檢視。

4. **文字精度的折衷**：你可以在 prompt 中指定確切要顯示的文字，用引號括起（官方 `prompting.md:57-60` 建議 verbatim rendering（逐字呈現）），但**禁止**使用壓力措辭——`character for character`、`exactly as written`、`必須完全正確`——這些措辭是 2026-07-08 code-drawing 事故的直接誘因。

## 看似矛盾的解決方案

官方指引建議「指定確切文字以求 verbatim rendering」；本 skill 禁止使用壓力措辭。這看似矛盾，解決方案如下：

- **指定文字內容**：`include the following text: "大豐環保"` — 合法。這告訴模型要寫什麼字。
- **禁止施壓措辭**：`render this text character for character with zero tolerance for errors` — 禁止。這誘導調度層 LLM 認為「image_gen 做不到精確 → 我該用 code 重繪」。

差別在於**意圖表達** vs **執行壓力**。前者描述期望結果，後者制造恐懼驅使 LLM 走 code path。

## 錯字處理

如果生成的圖片中文字有錯字或筆畫偏差：

1. **重新生成**整張圖片（修改 prompt 或直接重跑）
2. **永遠不用 code 修補**（PIL 重繪、字型疊加、截圖替換等全部禁止）
3. 回報不完美之處，讓使用者決定是否接受或再次嘗試

## Prompt 範例

```text
Image description:
A product label showing the text "回收再利用" in Traditional Chinese (no Simplified Chinese).
The text should be clearly legible on the label.
```

不要寫成：

```text
Image description:
A product label. The text "回收再利用" must appear character for character,
exactly as written, with zero deviation. Each stroke must be pixel-perfect.
```

後者會誘發 code-drawing。
