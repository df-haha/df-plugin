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

然後在 Codex CLI 內開 `/plugins`，從 `df-haha Plugins` 安裝任一 plugin；Codex marketplace 目前列出 18 個 plugins。安裝後可在 `/skills` 或以 `$<skill-name>`／自然語言觸發各 skill。`markitdown` 也已補上 Codex skill；它首次使用若未安裝 CLI，會先要求安裝授權。

## 可用 Plugins

| Plugin | 說明 |
|--------|------|
| **first-principles** | 第一性原理思考框架 — 基於 Elon Musk 的第一性原理方法論，自動拆解問題到最基本的事實與假設 |
| **markitdown** | 使用 Microsoft MarkItDown 將 PDF、PPT、Word、Excel 等檔案轉換為 Markdown |
| **deep-research-ryan** | 多階段深度研究引擎 v2.0.0 — 廣度掃描→深度搜索→多輪辯論+Judge→報告合成（Merge）→引用驗證→三閘門品質檢查（QG），8 種研究類型；Workflow 編排自動調度 subagents；跨平台（Windows/macOS/Linux）（by [Ryan](https://github.com/anthropics/claude-code/tree/main/.claude/skills/deep-research)） |
| **fact-check** | 文檔事實查核系統 — 5 層查證架構，支援實體、技術、數據、人物、論述的交叉驗證（by [Ryan](https://github.com/anthropics/claude-code/tree/main/.claude/skills/deep-research)） |
| **daily-work-log** | 跨專案工作日誌產生器 — 掃描 Claude Code / Codex / Gemini session，首次使用引導設定，產出 markdown 日誌並透過 **df-graph**（雲端 Graph，OS 無關）建草稿寄出 |
| **ai-review** | AI 二次審查與技術討論 — 使用 Codex / Antigravity（agy）/ Claude Code CLI 讀取目前 repository 後，對程式碼/計畫/技術決策進行獨立審查或合作式討論，支援單審或 Codex+Agy 雙重對審；reviewer 依宿主選擇（Claude Code 宿主預設 Codex，可 `--model`/`--effort`，未指定吃 `~/.codex/config.toml`；Codex 宿主預設 Claude `claude-opus-4-6[1m]` + max effort）、Agy 預設 `3.5-flash`；Claude Code 可用 `/ai-review`，Codex 用 `$ai-review` / `/skills` 觸發 |
| **evaluate-business-idea** | 五維深度系統評估框架 — 為軟體/SaaS/服務點子打分，判定深度系統 vs 免洗系統，含市場/GitHub/法規調查 SOP 與 idea 資料夾模板 |
| **handoff** | 跨 session 複雜任務交接 — 自動從本次對話收集 commit / 決策共識 / 待辦 / 相關 memory，產出結構化 handoff prompt 並落盤至 `~/.claude/handoffs/`，過程中呼叫 Codex CLI 對草稿做最後一道把關（只挑實質影響的問題），含自我清理段落避免堆積 |
| **om-daily-work-log** | OM 營運部雙向 async coaching loop（員工只裝這一個，自給自足）— 屬下端日誌偵測主管催辦信、用 CC 查 git/spec/tasks 後在 HTML anchor 區塊回覆；主管端產澄清問題卡 compose/reply 寄出，下次 /hi 解析閉環。日誌/寄信 vendored，無須額外裝 plugin。**前置**：Python 3.8+ + **df-graph** plugin（純雲端 Graph，OS 無關，取代舊的 Windows + Outlook Desktop + outlook-local；對 Claude 說「df-graph setup」裝），再說「work-log setup」跑 onboarding。註：教練卡片 reply 寄送仍在從 COM 遷移中 |
| **coolify-deploy** | Coolify（自架 PaaS）+ Docker Compose 部署規則 skill — compose 撰寫、Dockerfile、env/機密、SERVICE_URL magic env、Adminer/Seq 選配服務、部署流程、回滾、網域/TLS，整條 CD 生命週期的調和規則 |
| **df-graph** | Microsoft 365 / Graph MCP server（信箱 + 行事曆 + 人員）— 純雲端 Graph API、**OS 無關**、每人一次 device-code 登入，取代需 Windows + Outlook Desktop 的 COM 方案。無狀態、id-based、讀取零淨化。是 `daily-work-log` / `om-daily-work-log` 信箱讀寫的後端。啟用後對 Claude 說「df-graph setup」跑 onboarding（`claude mcp add df-graph --scope user`→device-code 登入→selftest）。上游 [dfroy00/df-graph](https://github.com/dfroy00/df-graph)（vendored，by 宗霖） |
| **decision-wiki** | Git-first 決策知識庫工具組 — **setup-decision-wiki** 安裝或升級可審查的決策 Wiki；**save-decision** 將已明確定案的 repository 決策建立為草稿。支援 Claude Code 與 Codex。 |
| **pilotfish-parallel** | Codex-only 並行工作程序 — 將 2–3 個互不依賴、路徑不重疊的 repository 工作放進隔離 Git worktree 並行執行，以 fail-closed（失敗即整批停止）方式整合，最後由 fresh verifier（全新上下文驗證器）裁決。 |
| **codex-image** | 用 Codex CLI 內建 `image_gen` 生圖 — 走 codex 帳號額度，不需額外 OpenAI / Stability API key。含 **codex-image-setup**（跨平台環境偵測：Windows / WSL / linux / darwin，寫 `${CODEX_HOME:-$HOME/.codex}/codex-image.local.md`）、injection-safe 的 Node `spawn` 呼叫（argv + stdin，`shell:false`）、反 code-drawing（禁止 LLM 改用 PIL/matplotlib 重繪）驗收與 chroma-key 去背透明流程。支援 Claude Code 與 Codex。 |
| **hermes-exchange** | 開放式 agent-to-agent 交換協定 + Hermes/Telegram 參考 adapter。雙方不必裝同一 plugin；Hermes 使用者可直接安裝 bundled runtime，其他 agent/runtime 只要實作 `HERMES_EXCHANGE/1` 協定與必要安全閘門即可互通。 |
