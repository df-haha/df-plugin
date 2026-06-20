# Devlog — 決策日誌（LOG）

> 本檔是**時間序、append-only** 的決策索引脊椎，給人與 LLM 回溯「何時、做了什麼決策」。
> 每筆固定前綴 `## [YYYY-MM-DD] decision | ADR-NNNN | <title>`，方便 grep / awk 解析。
> 規則：最舊在上、最新在下；**狀態不寫在這裡**（狀態只存在各 ADR 檔的 frontmatter，避免雙寫 drift）。
> 詳細內容與 trade-off 見 `decisions/ADR-NNNN-*.md`。
>
> 新增請用 `/log-decision`（或 `python3 scripts/devlog.py new ...`），不要手寫以免格式/配號飄。

---
