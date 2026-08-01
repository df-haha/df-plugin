# 疑難排解（Troubleshooting）

## 常見症狀對照

| 症狀 | 原因 | 診斷 / 處理 |
|------|------|------------|
| `Error: sandbox is read-only` | 漏帶 `--sandbox workspace-write` | 確認 `lib/run.mjs` 的 `buildCodexArgs()` 有包含此 flag |
| `Error: not a git repository` | 漏帶 `--skip-git-repo-check` | 同上確認 flag |
| Codex 跑完但 staging dir 無新檔案 | Prompt 未包含存檔指示（`Save ... in the current directory`），codex 只生不複製 | 檢查 prompt 結尾的存檔指示 |
| 檔案在 `$CODEX_HOME/generated_images/` 但 staging dir 無檔 | codex 嘗試 cp 但目標目錄無寫入權限 | 檢查 staging dir 權限 |
| Codex 超時（timeout） | 網路慢、冷啟動、或 prompt 過於複雜 | 回報 timeout，印出 stderr 最後 20 行；可嘗試簡化 prompt 後重試 |
| 內容政策拒絕（content policy refusal） | Prompt 觸發安全過濾 | 印出 stderr 給使用者看，建議修改 prompt |
| 圖片品質差、模糊、細節少 | Prompt 未表達 quality 意圖（預設可能走 `medium`） | 確認 prompt 含 `Render at high quality if your image tool exposes a quality setting.` |
| 圖片看起來像乾淨的點線圖 / 文字完美無誤 | **Code-drawing**：調度層 LLM 用 PIL / matplotlib / SVG 重繪了整張圖，而非使用 `image_gen` 產出 | 解析 JSONL 中的 `command_execution` / tool events（透過 `lib/parse-events.mjs` 的 `detectCodeDrawing()`）。**不要** grep prompt 或自然語言 messages——那不是可靠的偵測方式。確認失敗後刪除產出，帶更強的反 code-drawing 條款重跑 |
| 輸出目錄 permission denied | 目標路徑無寫入權限或命中 `deny_write_paths` | 檢查 config 中的 `deny_write_paths` 設定與實際檔案系統權限 |
| `codex features list` 輸出格式不符預期 | CLI 版本更新導致輸出形狀改變 | 標記 `image_generation` 狀態為 `unknown`，不要猜測。不要 grep config 檔後假稱知道 effective state（生效狀態） |
| 登入過期（login expired） | Codex session 或 token 過期 | 提示使用者執行 `codex login`，然後重試 |
| CLI fallback 無法執行 | 缺少 `OPENAI_API_KEY` 或 `network_access` 未開啟 | 確認兩項前置條件（見主 SKILL.md §12）。注意 `--ask-for-approval never` **不會**開啟 network access |

## 診斷原則

1. **JSONL 事件是主要診斷來源**——使用 `lib/parse-events.mjs` 的 `parseEvents()` 解析結構化事件，不要依賴 grep stdout prose。
2. **code-drawing 偵測解析 `command_execution` events**——不解析 prompt 或 assistant（助理）的自然語言回覆。
3. **不確定時標 `unknown`**——不要因為查不到就假設是 `false`。
4. **Config 問題先看 config**——`lib/config.mjs` 的 `loadConfigOrDefaults()` 會在 config 不可讀時 fallback 到預設值並印提示。
