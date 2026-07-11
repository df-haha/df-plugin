# 決策候選區

本目錄只存放 `status: proposed` 的候選決策卡，不具決策權威，也不得出現在正式決策索引。

只有使用者或負責人明確拍板重大架構、行為或 config（設定）決策時，coding agent（程式代理）才建立草稿；不要在每次 session（工作階段）結束時自動產卡。Agent（代理程式）不得自行接受、移出或把候選視為現行規則。

人工確認內容、遮罩後來源與持久化關係後，才可將卡片移至上層正式目錄、改為 `status: active`，並在同一個 commit（提交）更新 `../INDEX.md` 及執行 `node scripts/validate-decisions.mjs`。
