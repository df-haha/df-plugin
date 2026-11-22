---
name: devin-cli
description: 呼叫 Devin CLI（`devin -p` 非互動模式）把工作派出去——寫 code、修 bug、code review、偵察問答。使用時機：使用者說「用 devin」「派給 devin」「叫 devin 寫／修／審」「devin review」「devin 審一下」；或任務適合外派（規格完整的實作、獨立的二次審查）且使用者同意用 Devin。內含實測過的旗標組合、模型代號、權限模式與靜默失敗的檢查方法。不用於：Devin 雲端 session（`--cloud`，程式碼會送到外部，要先問使用者）。
---

# devin-cli：把工作派給 Devin CLI

> 依據：`devin 3000.14.30821` 的 `--help` 原文（`references/cli-help.md`）＋ 2026-11-21 實測。
> 標「實測」的是真的跑過；標「未驗證」的沒跑過，用之前先試。版本升級後先跑 `devin --version`，不同就重讀 help。

## 一行用法

```bash
bash "${CLAUDE_PLUGIN_ROOT}/skills/devin-cli/scripts/devin-run.sh" <ask|review|edit|code> <prompt 檔> [--trust] -C <工作目錄> -o <輸出資料夾>
```

包裝腳本固定帶安全旗標、存回覆與紀錄、檢查靜默失敗。**不要手拼 `devin -p`**，除非腳本不支援該用法。

| 模式 | 權限 | 能做什麼 | 預設模型 |
|---|---|---|---|
| `ask` | `auto` | 讀檔、搜尋、跑唯讀指令（`git diff` 實測可跑） | `swe-2-medium`（免費） |
| `review` | `smart` | 讀檔、跑指令。技術上也能改檔，腳本事後檢查 | `gpt-6-2-sol-xhigh` |
| `edit` | `accept-edits` | 可改檔。**非唯讀指令會被拒**（`pytest` 實測被拒） | `swe-2-high`（免費） |
| `code` | `smart` | 可改檔，也可跑快速模型判定安全的指令（`pytest` 實測可跑） | `swe-2-high`（免費） |

選項：`--trust` 略過 devin 的工作區信任檢查（規則見「硬規則」）、`-m <模型>` 換模型、`-r <session_id>` 續接上一輪、`-t <秒>` 逾時（預設 1800）。

結束碼：`0` 成功｜`3` 有工具呼叫被拒（可能只做一半，讀 `answer.md` 判斷缺什麼）｜`4` 沒有回覆｜`5` 唯讀模式（`ask`、`review`）卻改動了工作目錄｜`6` `.git` 的設定或 hooks 被改（任何模式都算；不要跑 git，處理方式見下方）｜`124` 逾時。

結束碼 3 的判定方式：devin 自己回 0，但 `stderr.log` 出現 `rejected a tool call`。

結束碼 5 的判定方式：比對執行前後整個 repo 的 `git status`、未暫存與已暫存的 `git diff`、HEAD 與目前分支、所有分支與標籤、stash、skip-worktree 等索引標記，以及未追蹤檔的內容。輸出資料夾（`-o`）在 repo 裡時，只有腳本自己寫的 `answer.md`、`answer.md.tmp`、`stderr.log`、`export.json` 四個檔不算在內；資料夾裡出現其他檔照樣算改動。

`.git` 裡的設定檔、hooks、`info/`（含 include 進來的設定檔與全域設定），以及 `core.hooksPath` 指到的 hooks 資料夾（在 `.git` 以外、是符號連結也算）也會比對。這些一有變動，腳本就不再跑任何 git 指令，直接當成有改動，回結束碼 6（四種模式都一樣），並多印一行 `devin-run: git_config_changed=1`。看到結束碼 6 先不要跑任何 git 指令（`git status` 就可能執行被埋進設定的程式），改用 `cat .git/config`、`ls -la .git/hooks` 檢查（有設 `core.hooksPath` 就連那個資料夾一起看）。連結 worktree 的 `.git` 是一個檔案：先 `cat .git` 看它指到哪個 repo，再去那個主 repo 的 `.git` 檢查設定與 hooks。

限制有六個：
- 只偵測、不還原。看到 5 就跑 `git status --short` 與 `git diff`，確認後自己還原（結束碼 6 時照上一段先檢查，不要跑 git）。
- 抓不到被 `.gitignore` 忽略的檔。未追蹤檔超過 2000 個時只比對檔名（多印一行 `devin-run: untracked_hash_skipped=<個數>`）；單一未追蹤檔超過 50 MB 只比對大小與修改時間。
- 子模組只看指到哪個 commit，不看子模組裡面的改動。
- 分不出是誰改的。比對範圍是整個 repo，連同所有分支、stash、`.git` 設定與全域 git 設定；同一個 repo 的其他 worktree 在這段時間 commit、背景 fetch、別的程序改了 git 設定，也會回 5 或 6。
- git 指令本身出錯、解析不到 `.git` 位置、或 git 因設定錯誤等原因整個拒跑時，一律當成有改動（後兩者多印一行 `devin-run: git_unresolved=1`）。
- 工作目錄不是 git repo 時不檢查（輸出 `workdir_changed=na`）。

結束碼 6 蓋掉所有其他結束碼；結束碼 5 蓋掉 6 以外的結束碼（含逾時的 124）。逾時與否另看 `stderr.log`。

產物：`<輸出資料夾>/answer.md`（回覆）、`stderr.log`、`export.json`（完整過程，含 `final_metrics` token 用量）。

## 動手前必知的 7 件事（全部實測）

1. **`devin -p` 結束碼是 0 不代表做完。** `accept-edits` 模式下跑測試被拒，結束碼仍是 0，只在 stderr 印 `warning: rejected a tool call that requires confirmation`。包裝腳本把這種情況改成結束碼 3。
2. **不帶 `--model` 時用 `~/.config/devin/config.json` 的 `agent.model`**（實測當時是 `claude-fable-5-2-medium`，輸入 8、輸出 40 美元／百萬 token）。新 session 不帶 `--model` 就會用它。續接（`-r`、`-c`）不帶 `--model` 時沿用該 session 原本的模型。包裝腳本一律帶 `--model`，所以用 `-r` 續接時要再帶一次 `-m`，否則會被換回該模式的預設模型。
3. **沒信任過的資料夾，`-p` 直接失敗**（`Refusing to run in an untrusted workspace`）。包裝腳本預設不略過這個檢查；帶 `--trust` 才傳 `--respect-workspace-trust false`，不改全域設定。被拒時腳本保留 devin 自己的結束碼，並多印 `devin-run: untrusted_workspace=1`。
4. **Devin 會自動讀工作目錄的 `AGENTS.md` 與 `CLAUDE.md`。** repo 規則不用重貼進 prompt。使用者層的 `~/.claude/CLAUDE.md` 與 `~/.claude/rules/` 會不會讀：未驗證，重要規則仍寫進 prompt。
5. **Devin 會接 `~/.claude.json` 裡的 MCP 伺服器，包含你接上的可寫入正式系統、可寄信的那些。** 它也載入 `~/.claude/skills/` 全部 skill。
6. **Claude Code 的 hook 管不到 Devin。** 你在 Claude Code 設的攔截（例：擋正式資料庫寫入、擋 `.env` 寫入）對 Devin 都不會生效。
7. **不同模型家族拿到的工具不同。** SWE-2 有專用的 `read`、`grep`、`edit` 工具（共 26 個）。GPT 系沒有讀檔工具，讀檔與改檔都走 `exec` 與 `apply_patch`（共 19 個）。後果：GPT 系在 `auto` 權限下連檔案都讀不到（讀檔指令被拒）。所以 `review` 模式用 `smart`。`ask` 模式要換成 GPT 系模型時，改用 `review` 模式。

## 硬規則

- **禁用 `--permission-mode dangerous`。** 理由見上面第 5、6 點：它會自動核准 MCP 寫入與任何指令。使用者當輪點名才用，且只在拋棄式 worktree。
- **`--trust` 只用在使用者自己的 repo 或使用者指定的工作目錄。** 外部來源的程式碼（別人的 PR、剛下載的 repo）不加 `--trust`，除非使用者當輪同意；要看這類程式碼時開拋棄式 worktree、用 `ask` 模式。
- **禁用 `--cloud`、`devin cloud`、`devin ssh`、`devin forward`，除非使用者當輪點名。** 雲端 session 在 Devin 的機器上跑，不在本機。哪些本機程式碼會被上傳：未查原文，當作會上傳處理。
- **每個派工 prompt 都貼「固定禁令段」**（見下）。
- **Devin 的產出是輸入，不是已驗證結果。** 寫 code 的派工：Claude 自己讀 diff、自己重跑測試，才回報完成。review 的派工：逐項判採納或反駁並寫理由，不照單全收。
- **commit、push、開 PR 由 Claude 做**，不交給 Devin。
- **prompt 一律寫成檔案**（放 scratchpad 或 repo 的 `.scratch/`），不塞在指令列。
- 跑超過一分鐘的派工：`run_in_background` ＋同一輪掛 Monitor 盯 `stderr.log` 與 `answer.md`，失敗特徵（`Error`、`Killed`、逾時）也要抓，不只抓成功字樣。

### 固定禁令段（每個 prompt 開頭貼上）

```
Constraints (hard):
- Do not call any MCP tool (mcp_call_tool, mcp_read_resource).
- Do not git commit, git push, open PRs, or change git config.
- Do not read or write outside this workspace. Never touch .env, *.key, *.pem, or any credentials file.
- Do not run commands that touch a database, deploy, or call external write APIs.
- Do not invoke the devin CLI or launch other agent CLIs.
- If a step is blocked, stop and report what is blocked. Do not work around it.
```

## 用法一：派出去寫 code

1. 開隔離的 worktree（改動範圍一眼看得出來）：
   `git -C <repo> worktree add <repo>/../<repo>-devin-<短名> -b feature/<短名>`
   Orca 內改用 `orca worktree create`（見 orca-cli skill）。
2. 寫 prompt 檔。必含：固定禁令段、目標、為什麼、動哪些檔、不動哪些檔、完成標準（要跑哪個測試指令、預期輸出）。
3. 派工：
   `bash "${CLAUDE_PLUGIN_ROOT}/skills/devin-cli/scripts/devin-run.sh" code <prompt 檔> --trust -C <worktree> -o <輸出資料夾>`（使用者自己 repo 開出來的 worktree 才加 `--trust`）
4. 看結束碼。`3`＝有步驟被拒：讀 `stderr.log` 與 `answer.md`，判斷缺哪一步，Claude 自己補跑，或用 `-r` 續接。
5. 驗收：結束碼 6 先不要跑任何 git，照「結束碼 5 的判定方式」後面那段用 `cat`、`ls` 檢查 `.git` 的設定與 hooks。其他結束碼才跑 `git -C <worktree> status --short`、`git -C <worktree> diff`，Claude 自己重跑測試。
6. 不合格就續接修正：先 `cd <worktree> && devin list --format json` 取最新的 `id`，再加 `-r <id>` 重派。同一模型失敗兩次就升級模型或收回自己做，不做第三次重試。
7. 合格後照原本流程：code review → commit → PR。

只要改檔、不需要跑指令時用 `edit` 模式，權限更小。

## 用法二：code review

1. 寫 prompt 檔。必含：固定禁令段、審查範圍（例：`git diff <merge-base>...HEAD`）、背景與需求原文、輸出格式。
2. 派工：
   `bash "${CLAUDE_PLUGIN_ROOT}/skills/devin-cli/scripts/devin-run.sh" review <prompt 檔> --trust -C <repo 或 worktree> -o <輸出資料夾>`（審使用者自己的改動才加 `--trust`；別人的 PR 照硬規則處理）
3. 讀 `answer.md`，逐項寫採納或反駁與理由。要對審就用 `-r <id>` 把回應送回去；輪數一般兩輪，最多三輪；開審前先寫好停止條件。

review prompt 的輸出格式段（可直接用）：

```
Review scope: run `git diff <BASE>...HEAD` and read the changed files in full.
Do not modify any file.
Report only findings you are at least 80% confident are real.
For each finding: severity (high/medium/low), file:line, what is wrong, a concrete failure scenario, suggested fix.
If you think the question itself is wrong, or key information was never gathered, say so directly.
End with one line: "VERDICT: PASS" or "VERDICT: FAIL".
```

診斷、事故分析、交叉驗證類的委派，prompt 先攤原始事實（含反例證據、使用者原話、已失敗的取證途徑），自己的假設放最後並標明是假設，並明說「如果你認為問題本身問錯了，直接說」。

## 用法三：問答與偵察

`bash "${CLAUDE_PLUGIN_ROOT}/skills/devin-cli/scripts/devin-run.sh" ask <prompt 檔> --trust -C <使用者自己的 repo>`

目標是外部來源的程式碼時拿掉 `--trust`，改指向拋棄式 worktree；devin 拒跑時由使用者決定要不要加。

適合「這段怎麼運作」「X 在哪裡用到」這類只讀問題。Devin 內建 `web_search`、`webfetch`，也能查網路。

## session 管理

| 要做什麼 | 指令 |
|---|---|
| 列出這個資料夾的 session | `devin list --format json`（欄位：`id`、`title`、`last_activity_at`） |
| 續接指定 session | 包裝腳本加 `-r <id>` |
| 刪除 session | `devin rm <id> --force` |
| 看登入狀態與方案 | `devin auth status` |
| 健檢 | `devin doctor` |

session 依資料夾分開。在 A 資料夾 `list` 看不到 B 資料夾的 session。

## 模型

- 速查表：`references/models.md`。原文：`references/models-full.md`。即時清單：`devin models`。
- **一律寫完整代號**（例 `claude-opus-5-5-high`），代號從 `devin models` 的輸出複製，不自己拼。多數別名指到舊版：`opus`＝Opus 5、`fable`＝Fable 5.1、`sonnet`＝Sonnet 5、`gpt`＝GPT-5.5。
- 換模型：包裝腳本加 `-m <代號>`。各模式的預設在 `scripts/devin-run.sh` 的 `DEFAULT_MODEL` 表（plugin 更新會覆蓋本機改動，臨時換用 `-m`）。
- 使用者說「用 X 模型跑」時照他說的，不替換。

## Devin 的其他能力（知道有，預設不用）

| 能力 | 入口 | 狀態 |
|---|---|---|
| 內建工具（SWE-2 26 個、GPT 系 19 個）：讀寫檔、`exec`、`grep`、子代理（`run_subagent`）、`web_search`、`webfetch`、瀏覽器預覽、Jupyter、MCP 呼叫、skill | agent 內部 | 清單來自 `export.json` 的 `tool_definitions`（實測） |
| 沙箱：指令只能寫工作目錄與已授權的 `Write(...)` 範圍；除了 `Deny(Read(...))` 隱藏的路徑，其餘都能讀 | `--sandbox` | Research Preview。`devin sandbox setup` 說需要 `bwrap` 與 `socat`，兩者都裝好才能用 |
| ACP 伺服器（給編輯器或其他程式用 stdio 協定驅動） | `devin acp [--agent-type review\|summarizer\|codemap]` | 未驗證。內含專用的 review agent |
| 供應商因使用政策拒絕請求時，依序改試其他模型（每個候選模型每個 session 最多試一次；一般的模型拒答不觸發） | 環境變數 `DEVIN_REFUSAL_FALLBACK=<模型,模型>` | 未驗證 |
| 續接最近一次對話 | `-c` | 未驗證。包裝腳本只支援 `-r <id>` |
| 輸出詳細程度 0–3 | `--verbosity <0-3>` 或 `DEVIN_VERBOSITY` | 未驗證 |
| 機器可讀的健檢與清單 | `devin doctor --json`、`devin skills list --json [--trigger user\|model]`、`devin skills show <名>`、`devin rules show <名>` | 未驗證 |
| 裝 plugin | `devin plugins install <owner/repo\|git URL\|路徑> [-y] [--local]`，另有 `info\|update\|remove\|prune` | 未驗證。會裝進別人的 skill 與 hook，先問使用者 |
| 從 Windsurf 搬設定 | `devin migrate hooks\|workflows` | 未驗證。`workflows` 會刪原檔 |
| 背景常駐程序（多個 `devin` 指令共用） | `devin daemon start\|stop\|status` | 包裝腳本用 `--process-model in-process`，不啟動它 |
| MCP 管理 | `devin mcp add\|list\|get\|remove\|login\|logout\|enable\|disable`；`add` 支援 `stdio\|sse\|http`，範圍 `-s local\|project\|user`（預設 `local`＝`.devin/mcp_config.local.json`） | `list` 實測。`disable` 會不會改到 `~/.claude.json`：未驗證，不要跑 |
| skill、rule、plugin | `devin skills\|rules\|plugins list` | skill 路徑：`~/.config/devin/skills/`、`~/.agents/skills/`、`~/.claude/skills/`、專案 `.devin/skills/`。rule：`.windsurf/rules/*.md`、`.cursor/rules/**/*.mdc` |
| hook | `~/.config/devin/config.json` 的 `hooks` | 其他工具（例：Orca）可能已接上 hook，改之前先看現有內容，不要覆蓋 |
| 雲端 session、SSH、port 轉發、環境藍圖 | `--cloud`、`devin cloud drs …`、`devin ssh`、`devin forward` | 未驗證。要先問使用者 |
| 桌面版 | `devin desktop`、`devin <路徑>` | 未驗證。注意：`devin <路徑>` 不帶 `-p` 會開桌面版，不是跑 agent |
| 匯出對話 | `--export <路徑>` | 實測。格式 ATIF-v1.7 JSON |
| 設定 | `--config <路徑>`、環境變數 `DEVIN_MODEL`、`DEVIN_PERMISSION_MODE`、`DEVIN_VERBOSITY`、`DEVIN_SANDBOX` | 來自 help 原文 |

## 排錯

| 症狀 | 原因 | 做法 |
|---|---|---|
| `Refusing to run in an untrusted workspace`（輸出有 `untrusted_workspace=1`） | 包裝腳本沒帶 `--trust` | 照「硬規則」判斷：使用者自己的 repo、或使用者當輪同意，才加 `--trust` 重跑；不要一看到就加 |
| 結束碼 3 | 權限模式不夠，步驟被拒 | 先讀 `answer.md`：主要工作有做完就照用（例：審查完成，只是想多跑一次驗證被拒）；沒做完就換 `code` 模式，或 Claude 自己補跑那一步 |
| 結束碼 5 | 唯讀模式下 Devin 改了檔 | `git status --short`、`git diff` 確認後還原 |
| 結束碼 6 | `.git` 的設定或 hooks 被改 | 不要跑 git；先用 `cat`／`ls` 檢查 `.git/config` 與 `.git/hooks`（連結 worktree 先 `cat .git` 找主 repo），確認沒被埋指令再處理 |
| 回覆說「command was rejected」 | 同上 | 同上 |
| 找不到模型 | 代號打錯，或帳號沒有這個模型 | `devin models \| grep <關鍵字>` |
| 跑出來的模型不是預期的那個 | 用了別名（別名有效，但可能指到舊版） | 改寫完整代號 |
| 登入失效 | 憑證過期 | 請使用者跑 `! devin auth login`（SSH 環境加 `--force-manual-token-flow`） |

## 實測紀錄（2026-11-21～22；未註明的用 `swe-2-medium`）

| 測試 | 結果 |
|---|---|
| `-p` 在未信任資料夾 | 失敗，結束碼 1 |
| 加 `--respect-workspace-trust false` | 成功，3.6 秒 |
| `auto` 模式審查一個檔 | 抓到 bug，沒改檔，5.1 秒 |
| `auto` 模式跑 `git diff HEAD --stat` | 可跑 |
| `accept-edits` 模式修 bug＋跑 pytest | 檔案改了，pytest 被拒，結束碼仍 0 |
| `-r <id>` ＋ `smart` 模式跑 pytest | 可跑，`1 passed` |
| 工作目錄的 `AGENTS.md`、`CLAUDE.md` | 兩個都自動載入 |
| `gpt-6-2-sol-xhigh` ＋ `auto` 審查 | 讀檔指令被拒，審不了；加「一次只跑一個簡單指令」的提示也一樣 |
| `claude-opus-5-5-high` ＋ `auto` 審查 | 讀檔成功、審查完成；想跑 python 驗證被拒（結束碼 3） |
| `gpt-6-2-sol-xhigh` ＋ `smart` 審查 | 成功，沒改檔 |
| 包裝腳本 `review`（預設模型） | 結束碼 0，抓到 bug |
| 包裝腳本 `review` 卻要求它改檔 | 檔案被改，腳本回結束碼 5 |
| 包裝腳本 `edit` 要求跑 pytest | 結束碼 3 |
| 包裝腳本 `code` 修 bug＋跑 pytest | 結束碼 0，Claude 重跑 `1 passed` |
| 包裝腳本 `ask` 不帶 `--trust`，在沒信任過的新資料夾 | devin 拒跑，腳本回結束碼 1，印 `untrusted_workspace=1` 與提示 |
| 包裝腳本 `ask` 帶 `--trust`，同一個資料夾 | 答案正確；中途一個工具呼叫被拒，結束碼 3 |
| 包裝腳本 `ask`、`review`（`swe-2-medium`）在本 repo | 結束碼 0，沒有誤報改檔 |
