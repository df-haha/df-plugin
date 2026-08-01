# 透明背景處理（Transparency workflow）

## 預設路徑：Chroma-key 去背

`gpt-image-2` 不支援 `background=transparent`。預設流程是在純色背景上生成，再用 vendored（附帶的）去背腳本移除背景。

### 步驟

1. **Prompt 設定 chroma-key 背景**：

```text
Create the requested subject on a perfectly flat solid #00ff00 chroma-key background for background removal.
The background must be one uniform color with no shadows, gradients, texture, reflections, floor plane, or lighting variation.
Keep the subject fully separated from the background with crisp edges and generous padding.
Do not use #00ff00 anywhere in the subject.
No cast shadow, no contact shadow, no reflection, no watermark, and no text unless explicitly requested.
```

- 預設 key color：`#00ff00`（綠幕）
- 主體為綠色 → 改用 `#ff00ff`（品紅）
- 避免主體含有 key color

2. **執行去背腳本**：

```bash
python <plugin>/vendor/remove_chroma_key.py \
  --input <source.png> \
  --out <final.png> \
  --auto-key border \
  --soft-matte \
  --transparent-threshold 12 \
  --opaque-threshold 220 \
  --despill
```

### 腳本 flag 說明

| Flag | 功能 |
|------|------|
| `--input` | 輸入圖片路徑 |
| `--out` | 輸出 PNG 路徑（含 alpha channel） |
| `--auto-key border` | 從圖片邊緣自動取樣 key color |
| `--soft-matte` | 產生柔邊 matte（半透明過渡），改善反鋸齒邊緣 |
| `--transparent-threshold 12` | 與 key color 差距 ≤ 12 的像素設為全透明 |
| `--opaque-threshold 220` | 與 key color 差距 ≥ 220 的像素設為全不透明 |
| `--despill` | 移除主體邊緣殘留的 key color 滲色 |

如果去背後仍有細微 fringe（色邊），可加 `--edge-contract 1` 再試一次。`--edge-feather 0.25` 僅在邊緣明顯階梯狀且主體不是光亮/反射表面時使用。

3. **Alpha 驗證**：
   - 四角必須透明
   - 主體覆蓋範圍合理
   - 無明顯 key-color fringe
   - 輸出格式為 PNG 或 WebP（JPEG 不支援 alpha）

## 前置條件

- **Python 3**（`python3` / `python` / `py -3`）
- **pillow** 套件

**Windows 上常常不具備這兩項。** 透明背景因此是**選用功能**。

缺少時的處理：
- 明確回報缺什麼：`python3 command not found` 或 `pillow is not installed`
- 不要靜默失敗或跳過
- 提示使用者可安裝（`pip install pillow`）或改用 CLI fallback

## 降級路徑：CLI fallback 使用 `gpt-image-1.5`

```bash
python "$IMAGE_GEN" generate \
  --model gpt-image-1.5 \
  --prompt "<prompt>" \
  --background transparent \
  --output-format png \
  --out <output_path>
```

此路徑需要：
- `OPENAI_API_KEY` 環境變數
- Codex config 中 `[sandbox_workspace_write] network_access = true`

**`gpt-image-1.5` 品質略低於 `gpt-image-2`**。這是降級路徑，必須明確告知使用者並取得同意後才走。

### 何時建議 CLI fallback

官方 `prompting.md` 列出以下複雜主體（complex subjects），chroma-key 去背可能效果不佳：

- 毛髮（hair）、毛皮（fur）、羽毛（feathers）
- 煙霧（smoke）、玻璃（glass）、液體（liquids）
- 半透明材質（translucent materials）
- 反射物體（reflective objects）
- 柔和陰影（soft shadows）

遇到這些情境時，說明 chroma-key 是預設路徑但可能不理想，CLI fallback 可提供原生透明，然後使用宿主可用的互動機制詢問並等待明確同意。
