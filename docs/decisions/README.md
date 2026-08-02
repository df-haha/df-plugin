# 決策 Wiki 規範

本目錄是 Git-first（以 Git 為主）的決策知識庫。正式決策卡是 SSOT（Single Source of Truth，單一真相來源）；`INDEX.md` 只提供導覽，不另存決策內容。所有 coding agent（程式代理）直接讀取同一批檔案，不新增資料庫或 MCP（Model Context Protocol，模型情境協定）介面。

## 目錄與生命週期

- `_draft/`：尚未具決策權威的候選卡，只能使用 `status: proposed`。
- 根目錄 `DEC-*.md`：人工接受的正式卡，只能使用 `active`、`superseded` 或 `archived`。
- `INDEX.md`：正式卡及既有 ADR（Architecture Decision Record，架構決策紀錄）的導覽索引。每張正式 DEC 必須恰有一列可見索引，且索引狀態必須等於卡片狀態；草稿、未知 ID、重複、過期或格式不合 `DEC-YYYYMMDDTHHMMSSZ-<kebab-slug>` 的 DEC-like（類 DEC）列都不合法。正式 DEC 的 Markdown（標記語言）連結必須精確為 `./<id>.md`，不可使用 URI scheme（統一資源識別符配置）、絕對路徑、路徑穿越或其他位置；既有 ADR 連結可保留原路徑。

只有明確拍板的重大架構、行為或 config（設定）決策才建立草稿。Agent（代理程式）不得自行接受；人工確認內容、來源及關係後，才可把卡片移出 `_draft/`，並在同一個 commit（提交）更新索引。

## 檔名與資料模型

新卡的檔名必須等於 ID 加 `.md`，格式為 `DEC-YYYYMMDDTHHMMSSZ-<kebab-slug>`，例如 `DEC-20260711T052245Z-git-first-decision-wiki.md`。YAML frontmatter（YAML 檔頭）只使用下列受限格式：頂層純量、四個 inline array（行內陣列），以及 `sources` 下的純量 map（映射）。

```yaml
---
id: DEC-20260711T052245Z-example
title: 決策標題
status: proposed
decided_at: 2026-07-11T13:22:45+08:00
scope: repository-name
supersedes: []
depends_on: []
conflicts_with: []
related_to: []
sources:
  - id: S1
    type: manual
    client: human
    ref: user-confirmation-2026-07-11
    captured_at: 2026-07-11T13:22:45+08:00
    excerpt_sha256: 64-character-lowercase-hex
    verified: true
---
```

`type` 只能是 `session_excerpt`、`repo_file`、`git_commit` 或 `manual`。正式卡至少要有一個 `verified: true` 的來源；`ref` 不得使用本機絕對路徑或 `file:` URI（統一資源識別符）。

## 關係與語意相似

持久化關係只有四種，均由宣告該欄位的卡指向目標 ID：

- `supersedes`：本決策取代舊決策。
- `depends_on`：本決策依賴目標決策成立。
- `conflicts_with`：本決策與目標決策不相容。
- `related_to`：經人工確認、但不屬於前三類的關聯。

正式卡的關係目標只能是另一張正式 DEC 或已發現的既有 ADR；`_draft/` 中的 proposed（提議中）卡不得作為正式卡的關係目標。正式 DEC 的 `supersedes` 目標必須是 `status: superseded`；每張 `status: superseded` 的正式 DEC 也必須由另一張正式 DEC 的 `supersedes` 指向。既有 ADR 沒有相同 metadata（中繼資料）時不套用這項狀態檢查。

Semantic similarity（語意相似度）只能在閱讀或搜尋時計算並標為推測，不得寫成 `similarity`、`semantic_similarity` 或 `similar_to` 欄位。若相似性值得永久保存，必須先經人工確認，再轉成 `related_to`。

## 正文與原文溯源

每張卡固定包含六節：

1. 決策背景與決策前狀態
2. 替代方案及採否理由
3. 最終決策與理由
4. 預期後果及決策後狀態
5. 原文溯源
6. 後續結果與沿革

每個 source（來源）都要在可見的「原文溯源」下恰有一個可見的 `### <source-id>`，後接連續 blockquote（引文區塊）行；不得重複標題，也不得出現 frontmatter（檔頭）未宣告的孤立來源標題。藏在 HTML comment（HTML 註解）或 fenced code block（圍欄程式碼區塊）中的節或來源標題不算。寫入前先遮罩密鑰、個資及不應進 Git 的內容，不保存完整 transcript（逐字紀錄）。Hash（雜湊）正規化規則固定為：移除每行開頭的 `> `，其餘 bytes（位元組）原樣保留，以 `\n` 連接，不加尾端換行，再用 UTF-8 計算 SHA-256。

## 接受、取代與既有資料

人工接受後，背景、替代方案、最終決策及理由不可原地改寫；只允許更新生命週期狀態、關係 metadata，或追加具日期的後續結果。翻案必須建立新卡，以 `supersedes` 指向舊卡，再把舊卡狀態改為 `superseded`。

既有正式 ADR 保留原路徑並加入索引，不搬動、不重寫。舊 spec（規格）、plan（計畫）、session memory（工作階段記憶）或資料庫記錄只能唯讀盤點；去重後先產生候選卡，不得自動接受或刪除來源。

接受前及修改關係後執行：

```bash
node scripts/validate-decisions.mjs
```

Validator（驗證器）需要 Node.js 18 或更新版本，不需要 runtime dependency（執行期相依套件）；也可傳入 repo root（程式碼儲存庫根目錄）：`node scripts/validate-decisions.mjs /path/to/repo`。
