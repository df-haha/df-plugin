---
name: session-resume-inventory
description: 盤點目前開著的 Claude Code、Codex、tmux 與 Orca 工作階段（session），對應到 repo／worktree 與分支，說明即時狀態並產出精確的續接（resume）指令。觸發時機：使用者說「盤點 session」「開著哪些 session」「重開機前清單」「session inventory」「resume 指令」「有哪些 claude/codex 在跑」。不用於掃歷史待辦。
---

# Session Resume Inventory（Claude Code 版）

產出一份唯讀、重開機安全的盤點。檢查過程中不得關閉、續接、改名或以任何方式變動任何 session。

## 一鍵執行

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/scripts/inventory.py"
```

`CLAUDE_PLUGIN_ROOT` 沒有值時，從本檔位置往上三層就是 plugin 根目錄（`plugins/session-resume-inventory/`，內含 `scripts/`）。

需求：Linux／WSL（讀 `/proc`）、Python 3.9+；tmux 與 Orca 沒裝時該區自動略過。

腳本只讀 `/proc`、`~/.claude/sessions/<pid>.json`、Claude transcript、Codex rollout、git metadata、`tmux list-panes`、`orca-ide terminal list --json`，輸出下方格式的報告。跑完後由 main loop 做兩件事：

1. **補 work 摘要**：腳本的 work 欄只是「起頭／最新」使用者訊息原文，不夠判斷時再讀該 transcript 尾段，改寫成一兩句「目標＋目前階段」。
2. **把完整報告放在最終回覆正文**（不要只留在 shell 輸出）。

## 邊界

- 優先用官方 CLI 與本機狀態，不用 computer-use／GUI 控制，除非使用者本輪授權。
- 「OS process 活著」「有持久化 transcript」「Orca terminal 存在」是三件不同的事，要交叉核對，不能混成一個狀態。
- 計算頂層 session 時排除 helper process：MCP server、browser host、`osc52-tap` 這類 wrapper、`claude --output-format stream-json`／`-p` 的無頭呼叫、`codex-code-mode-host`、`codex app-server/exec`、guardian 與 subagent thread。
- resume 指令不得帶 `--dangerously-skip-permissions`、`--yolo` 等旗標，除非使用者明確要求。
- 有精確 session ID 時不用 `--last`。
- 本 session 自己會以 `busy` 出現在清單中，屬正常。

## 盤點流程（腳本已實作；腳本失效時依此手動做）

1. 用 `claude --help`、`codex resume --help` 確認本機 CLI 語法。
2. 掃主機 process：PID、PPID、啟動時間、TTY、command line、`/proc/<pid>/cwd`、環境變數 `ORCA_TERMINAL_HANDLE`／`ORCA_WORKTREE_ID`。
3. 每個 Claude Code 主 process：讀 `~/.claude/sessions/<pid>.json` 取 `sessionId`／`cwd`／`name`／`status`；`~/.claude/projects/*/<sessionId>.jsonl` 存在才給 resume 指令。
4. 每個 Codex 主 process（vendor 下的 `codex` 原生執行檔，非 node wrapper）：
   - 優先用 `codex resume <id>` 參數裡的 ID；否則讀 `/proc/<pid>/fd` 找開啟中的 `~/.codex/sessions/**/rollout-*.jsonl`，第一行 `session_meta` 的 `thread_source` 為 `user` 且無 parent 才算頂層。
   - 名稱查 `~/.codex/session_index.jsonl` 的 `thread_name`。
   - 沒有 rollout 的 TUI 列為 `empty / not resumable`，不捏造 resume 指令。
   - 需要查 `~/.codex/state_5.sqlite` 而沒有 `sqlite3` 時，用 Python `sqlite3` 模組以 `file:...?mode=ro` 唯讀開啟，不為盤點安裝套件。
5. 每個 cwd：確認路徑存在；Git repo 用 `git branch --show-current` 取分支，比對 `--absolute-git-dir` 與 `--git-common-dir` 區分 linked worktree 與主 checkout。
6. Orca：執行檔是 `orca-ide` 或 `orca`（腳本兩個都找）。先 `orca-ide status` 與 `orca-ide terminal list --help` 確認語法再用；指令失敗就記下錯誤、停止呼叫 Orca，改用 process 環境變數＋Git 佐證，並標「Orca CLI 驗證不可用」。
7. 報告前對所有 PID、transcript、rollout、路徑、分支再做一次存在檢查，數字一律以這次快照為準。

## 狀態說明（放在表之前）

Claude：
- `idle`：活著、等輸入。**不代表任務完成**。
- `busy`：正在處理 turn／用工具／輸出中，重開機會中斷。
- `shell`：在等 shell 指令，對話可續接但 process／watcher 不會自動恢復。

Codex：
- `active`：正在處理本次請求的 session。
- `open`：頂層 process 活著且可持久續接。
- `empty / not resumable`：介面在但找不到持久化的使用者 session。

## 輸出格式

純文字友善的階層清單，**不用 Markdown 表格**。排序：

1. 以完整絕對路徑分組並排序。
2. 同一路徑內依 runtime 固定順序：`Claude Code`、`Codex`、`tmux`、`Orca`（沒有的就省略標題）。
3. 同一 runtime 內有名稱的依名稱排序，未命名的排後面、依 session ID 或 handle 排序。

```text
Snapshot: 時間
Counts: 各 runtime 數量
Evidence: 資料來源

# /absolute/repo/path

Branch: branch-name

## Claude Code

- session-name — idle
  - session ID: full-session-id
  - resume: claude --resume full-session-id
  - work: 目標與目前階段
  - note: PID、多 process 共用、Orca handle、重開機敏感指令

## Codex

- session-name — open
  - session ID: full-session-id
  - resume: codex resume full-session-id
  - work: 目標與目前階段

## tmux

- tmux-session pane-id (前景指令)

## Orca

- terminal-title — connected
  - handle: full-terminal-handle
  - work: 用途或 empty / not resumable

重開機警告：列出所有 busy／shell session 與重開後要檢查什麼
```

格式要求：
- 完整絕對路徑、完整 session ID、完整 Orca handle。
- resume 行只能是 `claude --resume <id>` 或 `codex resume <id>`，不含 `cd`、旗標或路徑。
- 非 Git 目錄標 `Branch: 非 Git repo`。
- 同一 session ID 有多個 live process 時只列一次，note 列出所有 PID。
- 已承載 Claude／Codex 的 Orca terminal 只記在該 session 的 note，Orca 區只列其他 terminal，避免重複。

## 證據標準

註明使用的資料來源；任何來源不可用時，受影響欄位標 `尚未驗證`，不得用推論補。
