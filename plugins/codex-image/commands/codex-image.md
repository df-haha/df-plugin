---
description: "用 Codex CLI 生成圖片 — /codex-image [圖片描述] 或 /codex-image setup"
---

解析 `$ARGUMENTS`。

若第一個詞是 `setup`，套用 `codex-image-setup` skill 執行環境偵測與設定。

否則將 `$ARGUMENTS` 整體視為圖片請求，套用 `codex-image` skill 生成圖片。

Arguments: $ARGUMENTS
