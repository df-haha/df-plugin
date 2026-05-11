# 跨 Session 任務交接

產出結構化 handoff prompt，讓下個 session 無歧義接續複雜任務。

## 使用方式

- `/handoff` — 自動從本次對話歷史推斷脈絡，產出 handoff md → 落盤 → 呼叫 Codex CLI 對草稿做最後審核（只挑實質影響的問題）→ print 完整 prompt
- `/handoff <自由補述>` — 補充本 session AI 沒抓到的決策共識、特殊禁止事項

## 適用情境

- **複雜跨 session 任務**：多 Phase、多步驟、含 AI 對審輪次的任務
- **Codex / 多 AI 對審結論**：上個 session 達成的對審共識，避免下個 session 重複辯論
- **明確的 input artifact + Step A/B/C**：有具體輸入檔案路徑、要做什麼、禁止什麼

## 與 remember:remember 的差異

| 項目 | `/handoff`（本 skill） | `remember:remember` |
|------|----------------------|---------------------|
| 長度 | 50-200 行 | < 20 行 |
| 結構 | 完整任務交接（Step、禁止、執行紀律） | State / Next / Context 極簡 |
| 落盤 | `~/.claude/handoffs/<project>-<ts>.md` | `<project>/.remember/remember.md` |
| 適用 | 複雜多步驟任務 | 簡單接續 |

## 執行

實際流程交給 `handoff` skill 處理。本 command 只負責觸發。

解析 `$ARGUMENTS`：
- 為空 → 純自動推斷模式
- 有內容 → 將內容當作「使用者額外補述」傳給 skill，融入到生成的 prompt 中

呼叫 Skill tool（`handoff`）執行完整流程。
