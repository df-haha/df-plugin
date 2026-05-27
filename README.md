# Claude Plugins Marketplace

自訂 Claude Code plugins 集合。

## 安裝方式

```bash
claude plugin marketplace add df-haha/df-plugin
```

## 安裝 plugin

```bash
claude plugin install first-principles
```

## 可用 Plugins

| Plugin | 說明 |
|--------|------|
| **first-principles** | 第一性原理思考框架 — 基於 Elon Musk 的第一性原理方法論，自動拆解問題到最基本的事實與假設 |
| **markitdown** | 使用 Microsoft MarkItDown 將 PDF、PPT、Word、Excel 等檔案轉換為 Markdown |
| **deep-research-ryan** | 多層次深度研究引擎 — 自動調度 subagents 執行多階段研究，支援公司、產品、技術、產業、人物等 8 種研究類型（by [Ryan](https://github.com/anthropics/claude-code/tree/main/.claude/skills/deep-research)） |
| **fact-check** | 文檔事實查核系統 — 5 層查證架構，支援實體、技術、數據、人物、論述的交叉驗證（by [Ryan](https://github.com/anthropics/claude-code/tree/main/.claude/skills/deep-research)） |
| **daily-work-log** | 跨專案工作日誌產生器 — 掃描 Claude Code / Codex / Gemini session，首次使用引導設定，產出 markdown 日誌並可透過 Outlook 寄出 |
| **ai-review** | AI 二次審查 — 使用 Codex CLI 或 Gemini CLI 對程式碼/計畫/技術決策進行獨立審查，支援單審或 Codex+Gemini 雙重對審 |
| **evaluate-business-idea** | 五維深度系統評估框架 — 為軟體/SaaS/服務點子打分，判定深度系統 vs 免洗系統，含市場/GitHub/法規調查 SOP 與 idea 資料夾模板 |
| **handoff** | 跨 session 複雜任務交接 — 自動從本次對話收集 commit / 決策共識 / 待辦 / 相關 memory，產出結構化 handoff prompt 並落盤至 `~/.claude/handoffs/`，過程中呼叫 Codex CLI 對草稿做最後一道把關（只挑實質影響的問題），含自我清理段落避免堆積 |
| **om-daily-work-log** | OM 營運部雙向 async coaching loop — 主管端產澄清問題卡並 reply 屬下原日報（Outlook ConversationID 自動串 thread，無需維護屬下 email）；屬下端日誌偵測主管 reply、用 CC 查 git/spec/tasks 後在 HTML anchor 區塊回覆，主管下次 /hi 解析閉環。需配合 daily-work-log plugin（reuse 不 copy） |
| **coolify-deploy** | Coolify（自架 PaaS）+ Docker Compose 部署規則 skill — compose 撰寫、Dockerfile、env/機密、SERVICE_URL magic env、Adminer/Seq 選配服務、部署流程、回滾、網域/TLS，整條 CD 生命週期的調和規則 |
