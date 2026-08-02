# Repository Instructions for Codex

<!-- decision-wiki:routing:begin -->
## 決策 Wiki 工作流程

- 本 repo 自 2026-07-16 起以 `docs/decisions/` 作為已接受 repo 決策的 SSOT（Single Source of Truth，單一真相來源）；決策前先讀 `docs/decisions/INDEX.md` 與相關正式卡。
- 只有使用者或負責人明確拍板的重大架構、系統行為、資料口徑或 config（設定）決策，才立即使用 `decision-wiki:save-decision` 建立 `_draft/` 草稿；不要等到 session（工作階段）結束或 auto compact（自動壓縮）。
- 草稿不具決策權威；完整預覽、人工確認內容／來源／關係後，才可移為正式卡、同步更新 `INDEX.md`，並執行 `node scripts/validate-decisions.mjs`。
- 既有 `research.md`、decision log（決策日誌）、spec（規格）或 memory（記憶）只視為 legacy evidence（歷史證據）；不得自動搬移、改寫或升格成正式決策。
- `supersedes`、`depends_on`、`conflicts_with`、`related_to` 均須人工確認；語意相似不得持久化為關係。翻案建立新卡並以 `supersedes` 指向舊卡，不得改寫舊卡理由。
- 長任務的執行進度、暫時推論與待辦寫入 plan（計畫）、progress（進度）或 handoff（交接），不得冒充正式決策。
<!-- decision-wiki:routing:end -->
