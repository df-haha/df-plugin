# 尺寸限制（Size constraints）

## 來源範圍

以下四項限制來自官方 `image-api.md:3`，該檔案開頭自註：

> "This file is for the fallback CLI mode only... Do not assume they are normal arguments on the built-in `image_gen` tool."

因此：

| 路徑 | 這四項限制的作用 |
|------|----------------|
| **內建 `image_gen`（built-in path）** | **Advisory only（僅供參考）**。不阻擋、不改寫使用者請求的尺寸。`lib/validate-size.mjs` 以 `{ mode: 'builtin' }` 呼叫時，violations 標為 `advisory`，不觸發 hard block。 |
| **CLI / API fallback** | **Hard validation（硬性驗證）**。`{ mode: 'cli' }` 呼叫時，violations 觸發 block，須先修正再送出。 |

歷史上 `1920x1080` 在內建路徑下一直正常運作——正是因為內建路徑不直接取用此參數。

## 四項限制（`gpt-image-2` CLI/API fallback）

1. **最大邊長** ≤ `3840` px（inclusive，即 `<=`）
2. **兩邊皆為 16 的倍數**
3. **長邊：短邊** ≤ `3:1`
4. **總像素**介於 `655,360` 至 `8,294,400`

## Tie-break 規則

當某邊不是 16 的倍數時，`lib/validate-size.mjs` 建議最近的合法值。精確中點（exact midpoint）時，**向上**取到較大的 16 倍數。

例：邊長 1080 → 最近的 16 倍數為 1072 和 1088。1080 距兩者各 8（精確中點），snap UP → 建議 `1088`。

## 常用尺寸速查

| 標籤 | 尺寸 | 備註 |
|------|------|------|
| Square | `1024x1024` | 快速預設 |
| Landscape | `1536x1024` | 標準橫幅 |
| Portrait | `1024x1536` | 標準直幅 |
| 2K square | `2048x2048` | — |
| 2K landscape | `2048x1152` | — |
| 4K landscape | `3840x2160` | 4K 橫幅 |
| 4K portrait | `2160x3840` | 4K 直幅 |
| Auto | `auto` | 預設 |

## 待查（unverified）

一份外部 prompting cookbook 據報記載最大邊長為 `<3840`（exclusive，即 `< 3840`，排除 3840 本身）。此說法**尚未**經一手來源驗證——官方 `image-api.md` 的措辭為 `<= 3840px`。在取得明確的一手澄清前，**不要**依據此外部說法行動，沿用 `<= 3840`（inclusive）。
