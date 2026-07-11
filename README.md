# Claude/Codex Plugins Marketplace

自訂 Claude Code / Codex plugins 集合。

## Claude Code 安裝方式

```bash
claude plugin marketplace add df-haha/df-plugin
```

## 安裝 plugin

```bash
claude plugin install first-principles
```

安裝決策知識庫工具組：

```bash
claude plugin install decision-wiki
```

安裝後可使用 `setup-decision-wiki` 安裝或升級決策 Wiki，並以 `save-decision` 將已定案的決策建立為可審查草稿。

## Codex 安裝方式

從本 repo root 加入本地 marketplace：

```bash
codex plugin marketplace add ./
```

然後在 Codex CLI 內開 `/plugins`，從 `df-haha Plugins` 安裝 `ai-review`、`coolify-deploy`、`first-principles` 或 `decision-wiki`。安裝後可在 `/skills` 發現 `setup-decision-wiki` 與 `save-decision`；目前 Codex marketplace 已暴露已完成 Codex manifest 的 `ai-review`、`coolify-deploy`、`first-principles`、`decision-wiki`。

## 可用 Plugins

| Plugin | 說明 |
|--------|------|
| **first-principles** | 第一性原理思考框架 — 基於 Elon Musk 的第一性原理方法論，自動拆解問題到最基本的事實與假設 |
| **markitdown** | 使用 Microsoft MarkItDown 將 PDF、PPT、Word、Excel 等檔案轉換為 Markdown |
| **deep-research-ryan** | 多層次深度研究引擎 — 自動調度 subagents 執行多階段研究，支援公司、產品、技術、產業、人物等 8 種研究類型（by [Ryan](https://github.com/anthropics/claude-code/tree/main/.claude/skills/deep-research)） |
| **fact-check** | 文檔事實查核系統 — 5 層查證架構，支援實體、技術、數據、人物、論述的交叉驗證（by [Ryan](https://github.com/anthropics/claude-code/tree/main/.claude/skills/deep-research)） |
| **daily-work-log** | 跨專案工作日誌產生器 — 掃描 Claude Code / Codex / Gemini session，首次使用引導設定，產出 markdown 日誌並透過 **df-graph**（雲端 Graph，OS 無關）建草稿寄出 |
| **ai-review** | AI 二次審查 — 使用 Codex / Antigravity（agy）/ Claude Code CLI 對程式碼/計畫/技術決策進行獨立審查，支援單審或 Codex+Agy 雙重對審；reviewer 依宿主選擇（Claude Code 宿主預設 Codex，可 `--model`/`--effort`，未指定吃 `~/.codex/config.toml`；Codex 宿主預設 Claude `claude-opus-4-6[1m]` + max effort）、Agy 預設 `3.5-flash`；Claude Code 可用 `/ai-review`，Codex 用 `$ai-review` / `/skills` 觸發 |
| **evaluate-business-idea** | 五維深度系統評估框架 — 為軟體/SaaS/服務點子打分，判定深度系統 vs 免洗系統，含市場/GitHub/法規調查 SOP 與 idea 資料夾模板 |
| **handoff** | 跨 session 複雜任務交接 — 自動從本次對話收集 commit / 決策共識 / 待辦 / 相關 memory，產出結構化 handoff prompt 並落盤至 `~/.claude/handoffs/`，過程中呼叫 Codex CLI 對草稿做最後一道把關（只挑實質影響的問題），含自我清理段落避免堆積 |
| **om-daily-work-log** | OM 營運部雙向 async coaching loop（員工只裝這一個，自給自足）— 屬下端日誌偵測主管催辦信、用 CC 查 git/spec/tasks 後在 HTML anchor 區塊回覆；主管端產澄清問題卡 compose/reply 寄出，下次 /hi 解析閉環。日誌/寄信 vendored，無須額外裝 plugin。**前置**：Python 3.8+ + **df-graph** plugin（純雲端 Graph，OS 無關，取代舊的 Windows + Outlook Desktop + outlook-local；對 Claude 說「df-graph setup」裝），再說「work-log setup」跑 onboarding。註：教練卡片 reply 寄送仍在從 COM 遷移中 |
| **coolify-deploy** | Coolify（自架 PaaS）+ Docker Compose 部署規則 skill — compose 撰寫、Dockerfile、env/機密、SERVICE_URL magic env、Adminer/Seq 選配服務、部署流程、回滾、網域/TLS，整條 CD 生命週期的調和規則 |
| **df-graph** | Microsoft 365 / Graph MCP server（信箱 + 行事曆 + 人員）— 純雲端 Graph API、**OS 無關**、每人一次 device-code 登入，取代需 Windows + Outlook Desktop 的 COM 方案。無狀態、id-based、讀取零淨化。是 `daily-work-log` / `om-daily-work-log` 信箱讀寫的後端。啟用後對 Claude 說「df-graph setup」跑 onboarding（`claude mcp add df-graph --scope user`→device-code 登入→selftest）。上游 [dfroy00/df-graph](https://github.com/dfroy00/df-graph)（vendored，by 宗霖） |
| **decision-wiki** | Git-first 決策知識庫工具組 — **setup-decision-wiki** 安裝或升級可審查的決策 Wiki；**save-decision** 將已明確定案的 repository 決策建立為草稿。支援 Claude Code 與 Codex。 |
