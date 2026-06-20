---
description: "記錄一筆決策到 devlog — /log-decision [決策標題或描述]（省略則從當前對話脈絡擷取）"
argument-hint: "[決策標題或描述]"
---

套用 `devlog` skill 的「主工作流：記錄一筆決策」。

## 步驟

1. **決定要記什麼**：
   - 若 `$ARGUMENTS` 非空 → 以它為決策主題。
   - 若為空 → 從當前對話脈絡擷取剛達成的決策（決定了什麼、有哪些選項、取捨點、為何選這個）。先用一兩句向使用者複述你要記的內容，確認無誤再寫。

2. **找 repo root 與確認已 init**：
   - repo root = `git rev-parse --show-toplevel`。
   - 若 `docs/devlog/` 不存在，先問使用者要用哪個 sensitivity profile，再跑 `init`（見 devlog skill 的「在新 repo 採用 devlog」）。

3. **判斷模式**：這個決策在別處（spec 段落、既有 decisions 檔、PR）已有權威全文嗎？有 → `pointer`，沒有 → `full`。

4. **建檔**（用 helper，不要手算 id / 手寫檔）：
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/devlog.py" new "$(git rev-parse --show-toplevel)" \
     --title "<標題>" --mode full|pointer --status proposed|accepted \
     --slug <short-english-slug> [--refs "..."] [--resolves "Q-x"] [--tags "..."]
   ```

5. **填 body**：打開腳本印出的檔案路徑，依模板填區段（full：Context/Options 含 trade-off/Decision/Consequences/Links；pointer：Decision digest/Trade-off digest/Authoritative source）。

6. **過 gate**：
   ```bash
   python3 "$CLAUDE_PLUGIN_ROOT/scripts/devlog.py" scan "$(git rev-parse --show-toplevel)"
   ```
   有命中 → 改用佔位符（`<client-id>` 等）後重掃至乾淨。

7. **回報**：告訴使用者建立了哪個 ADR、在哪個檔、LOG 加了哪行；提醒相關 commit 末行加 `Devlog: ADR-NNNN`。

完整規則、doctrine、範圍紀律見 `devlog` skill。

Arguments: $ARGUMENTS
