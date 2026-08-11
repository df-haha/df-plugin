# Orca ADE 上手與能力地圖（knowledge layer，知識層文件）

> 給團隊內用 Orca ADE 搭配 Claude Code / Codex 的人。核心教訓（2026-08-11，血淚換來）：
> **碰 Orca 自動化前，先讀 Orca 自帶的官方 skills，不要自己土砲**——官方 orchestration
> 已內建完成回呼、transcript（逐字稿）直讀、worker 生命週期管理；自建等於重造更脆的輪子。
>
> 查核基準：本文所有指令與語意主張於 2026-08-11 以 Orca CLI（relay 0.1.0+7175e0a）實測，
> 對照來源＝`orca skills get orchestration` / `orca skills get orca-cli` 原文與各指令 `--help`。
> Orca 升版後若行為不符，以你機器上 `orca skills get` 的當版原文為準。

## 1. Onboarding：裝官方 skills（每台機器各裝各的）

```bash
orca skills list                 # 看有哪些（含一行說明）
orca skills install --all        # 裝到本機偵測到的 agents（Claude Code / Codex）
orca skills update --all         # Orca 升版後更新（⚠️ 不帶 --all/--skill 只會列清單，不會更新）
```

注意事項：

- **`skills install` 要在目標機器本機跑**。透過 SSH relay（例如 WSL 內的 `orca` wrapper
  轉發到 Windows host）會被拒（`invalid_environment`）。此時優先改到目標主機上執行；
  真的只能手動時，`orca skills get <name>` 把原文（含 frontmatter）存到**兩個宿主各自的**
  skill 目錄：Claude Code → `~/.claude/skills/<name>/SKILL.md`、
  Codex → `~/.agents/skills/<name>/SKILL.md`（只裝一邊＝另一個宿主偵測不到）。
- Linux 非 Orca 管理的 shell 裡不要跑裸 `orca`（會撞上 GNOME 螢幕閱讀器），用 `orca-ide`。
- skills 與 CLI **版本配對**：Orca 升版後要重裝／重抓，不要 vendor 進 repo（必漂移）。

官方 skills 一覽（8 份，`linear-tickets` 是 `orca-linear` 的 legacy alias）：
`orca-cli`（worktree/terminal/瀏覽器原語）、`orchestration`（多 agent 協調）、
`computer-use`（桌面 App 操控）、`orca-emulator`（iOS）、`orca-emulator-android`、
`orca-linear`（Linear 工單）、`orca-per-workspace-env`（拋棄式 workspace 環境）。

## 2. 怎麼操控 Agent 做事（三種官方模式，別混用）

| 模式 | 什麼時候用 | 入口 |
|---|---|---|
| **Full handoff（交棒不管）** | 「交給另一個 agent／worktree」且沒要求監督 | `orca worktree create --name X --no-parent --agent codex --prompt "..." --json` → 回報 → 停止監控。禁用 task-create/dispatch/check --wait |
| **Supervised orchestration（監督式）** | 要等結果、追蹤完成、DAG、多 worker | `run-create` → `task-create` → `worker-start --task X --agent codex` → `check --wait` → `worker-read` → `worker-release` |
| **輕量 terminal** | 單發 prompt、不需任務追蹤 | `terminal create --worktree active --command codex` → `terminal wait --for tui-idle` → `terminal send` |

開 worktree 要點：

- **agent-first 是正道**：`worktree create --agent codex --prompt "..."`（agent 進第一個
  terminal）；「先 create 再另開 agent terminal」是官方點名的 anti-pattern。
- worktree id 是兩段式 `<repoId>::<path>`，整串複製，別只拿 repo id。
- 獨立工作用 `--no-parent`（只控 lineage；git base 由 repo 預設，不要 base 在 feature branch）。
- 進度可視化：`worktree set --worktree active --comment "..."` ＋ `--workspace-status`。

## 3. 官方語意易錯點（實測驗證過，照抄可省一輪踩坑）

- **`check --wait` 是 drain/ack 迴圈，不是一次呼叫**：每次回一個 bounded Delivery，
  要「處理整批 → 依事件 id 去重 → `check --ack <delivery_id>` → 續等」直到所有 Dispatch
  settled；timeout／count:0 是 checkpoint 不是失敗（長任務 15–60 分鐘正常）。
- **`worker_done` 的「產生」不保證，但「送達後」有持久化契約**：Run 是 durable inbox
  （持久信箱），Delivery 在 ack 前會重播——真正沒有事件的情況是 worker 在成功送出
  `worker_done` **之前**就崩潰。所以備援偵測（terminal／Dispatch 狀態檢查）的定位是
  「事件未產生時的存活性檢查」，不是懷疑已接受的事件會消失；也別因此重複派工。
- **`send --to dispatch:<id>` 是結構化信箱**，worker 要主動 `orchestration check` 才看到，
  **不是**打進 TUI 輸入框。追問分兩種：**進行中**的 Dispatch 補充指示 → 就用
  `send --to dispatch:<id>`；**已 settled** 且要同一 terminal 立即續作 → 建下一個 Task
  再 `worker-start --task <next> --terminal <handle>` 移交（進行中就建下一個 Task 會撞
  ownership 拒絕）。
- **`worker-read` 會降級**：Orca 證明不了 worker session 時回**有長度上限**的 terminal
  output（附 `fallbackReason`），不是完整 transcript；**永遠不要自己猜 provider session id**。
- **`worker-list` 只列 supervised worker**：盤點全部殘留 terminal 要聯集
  registry／`terminal list`／`worker-list`／dispatch 狀態／`worktree ps`。
- **`terminal wait --for tui-idle` 不可靠判「回答完成」**：實測 satisfied=true 時 agent
  可能還在跑；完成判定用 orchestration 的 `worker_done`，或明確的完成標記。
- 收工紀律：`worker-release` 成功失敗都要跑（要留著 debug 用 `worker-retain`）；
  不要因 timeout／idle／heartbeat 就 release 或 kill。

## 4. Pane 模式（per-machine opt-in，非團隊預設）

> 本節是 pane 政策的 **canonical wording（標準措辭）**；ai-review／codex-image 兩份
> SKILL.md 內的註記是它的精簡版，語意以本節為準。

reviewer／codex 呼叫**預設走 shell headless（無介面一次性執行）**。「在自己 tab 開
split pane 盯著 agent 跑」是 per-machine 的個人工作流：需要本機自建腳本（啟動驗證、
送出驗證、收工紀律），且官方 orchestration 沒有「split 在當前 tab」的形態。
進入 pane 模式需**同時**滿足：使用者本輪明確要求開 pane、且本機存在
`~/.claude/refs/orca-codex-pane.md`（canonical reference，標準參照檔）——
只有檔案存在、使用者沒點名，仍走 shell；檔案不存在，pane 模式不存在。

## 5. 更多

- 全指令 schema：`orca agent-context --json`（223 個指令，機器可讀）。
- 內嵌瀏覽器自動化（goto/click/fill/eval/screenshot/pdf/network intercept/多 profile）、
  排程 `automations`、桌面操控 `computer-use`、模擬器——各自的官方 skill 都有完整指南，
  裝好後直接觸發即可。
