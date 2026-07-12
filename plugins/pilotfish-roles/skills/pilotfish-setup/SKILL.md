---
name: pilotfish-setup
description: 安裝 pilotfish 多模型協調層（Claude Code 專用）——把 6 個角色 agent 與 2 個版本變體裝進 ~/.claude/agents/、委派政策裝進 ~/.claude/rules/agents.md、settings.json env 釘選模型別名，最後導引重啟與 spawn 驗證。已安裝者再跑一次即為升級模式（diff 模板 vs 本機）。觸發詞：「pilotfish setup」「裝 pilotfish」「pilotfish onboarding」「多模型協調層安裝」「pilotfish 升級」。
allowed-tools: Bash, Read, Write, Edit, AskUserQuestion
---

# pilotfish-setup（Claude Code 專用 installer）

把 pilotfish 多模型協調層裝進本機三個位置。原則：**主 session 的 token 花在判斷上，
量產工作路由給便宜模型，品質靠 verifier 把關**。

| 層 | 安裝目標 | 內容 |
|---|---|---|
| agents | `~/.claude/agents/*.md` | 6 角色（scout / Explore / mech-executor / executor / verifier / security-executor）＋ 2 個可選版本變體（executor-opus47 / executor-opus45） |
| 政策 | `~/.claude/rules/agents.md` | 委派規則、角色路由表、模型版本管理紀律 |
| env 釘選 | `~/.claude/settings.json` 的 `env` | `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` 三鍵，決定 agent frontmatter 內 tier 別名的解析結果 |

模板都在 `${CLAUDE_PLUGIN_ROOT}/skills/pilotfish-setup/templates/`。
本 skill 只在 Claude Code 宿主執行（寫 `~/.claude/` 路徑）；Codex 並行執行是另一個 plugin（pilotfish-parallel），不要混用。

## Phase 0：前置確認

1. 確認 `~/.claude/` 存在且目前宿主是 Claude Code。
2. **`CLAUDE_CODE_SUBAGENT_MODEL` 必須未設**（它會靜默覆蓋所有 per-agent model，破壞整套分層）：
   ```bash
   env | grep -c CLAUDE_CODE_SUBAGENT_MODEL; grep -c CLAUDE_CODE_SUBAGENT_MODEL ~/.claude/settings.json
   ```
   兩者都應為 0；不為 0 時停下，先請使用者移除再繼續。
3. 偵測既有安裝：`ls ~/.claude/agents/` 若已有同名檔 → 本次進入**升級模式**（Phase 1/2 一律先 diff、不盲蓋）。

## Phase 1：安裝 agent 檔（保持原名，不走 plugin 命名空間）

刻意用「複製到 `~/.claude/agents/`」而非 plugin 原生 agents 目錄：plugin 原生 agent 會被加上
`pilotfish-roles:` 前綴，路由表與使用習慣全要跟著改。installer 形式讓 `executor`、`scout` 保持素名。

1. 用 AskUserQuestion 問是否安裝 2 個版本變體（executor-opus47 / executor-opus45）；6 個核心角色一律安裝。
2. 逐檔處理 `templates/agents/*.md` → `~/.claude/agents/<同名>.md`：
   - 目標不存在 → 直接複製。
   - 目標已存在 → `diff` 模板與本機檔；**無差異**跳過，**有差異**把 diff 呈現給使用者選（保留本機／採用模板／手動合併）。本機檔可能載有使用者客製，是資產不是垃圾，永不盲蓋。
3. 完成後列出安裝清單（檔名＋該檔 frontmatter 的 `model:` 值）。

## Phase 2：委派政策（draft-first，人審後才落正式位置）

1. 把 `templates/rules/agents.md` 複製到暫存路徑（scratchpad 或 `/tmp`）作為草稿。
2. `~/.claude/rules/agents.md` 不存在 → 呈現草稿要點（角色路由表＋委派規則條數）請使用者確認後安裝。
   已存在 → `diff` 草稿與本機檔呈現差異，讓使用者決定：整檔採用／保留本機／逐段合併。
3. **未經使用者確認前不得寫入** `~/.claude/rules/`。
4. 提醒：政策內引用的 review 工具鏈（dev-workflow 等）是 df-haha rules 全套的一部分，新機器若沒有那些 rules，相關條文照常保留即可（引用落空不影響路由本身）。

## Phase 3：settings.json env 釘選（先備份，只動 env 三鍵）

別名解析未釘選前，`opus` / `sonnet` / `haiku` 會解析到 CC 當下預設（可能是最新版模型），
與政策「版本受控」的前提矛盾——**這一步不是可選的**。

1. 先 Read `~/.claude/settings.json` 確認現況（改前必讀，不憑記憶）。
2. 備份：
   ```bash
   mkdir -p ~/.claude/backups && cp ~/.claude/settings.json ~/.claude/backups/settings.json.pilotfish-$(date +%Y%m%d-%H%M%S)
   ```
3. 用 AskUserQuestion 問三個釘選值，預設值（df-haha 2026-07-11 拍板，可改）：
   - `ANTHROPIC_DEFAULT_OPUS_MODEL` = `claude-opus-4-6[1m]`
   - `ANTHROPIC_DEFAULT_SONNET_MODEL` = `claude-sonnet-4-6`
   - `ANTHROPIC_DEFAULT_HAIKU_MODEL` = `claude-haiku-4-5-20251001`
4. 只在 `env` 區塊 upsert 這三鍵。**不碰**頂層 `model`（主迴圈模型是個人選擇；另注意 `"best"` 不支援 `[1m]` 後綴）。
5. 紅線：不新增 `CLAUDE_CODE_SUBAGENT_MODEL`。

## Phase 4：重啟與驗證

env 與 agent 檔改動需重啟 session 才生效。請使用者重啟後執行驗證：

1. 對每個要驗的角色 spawn 一次（Agent tool），請 agent 回報它 system prompt 中的實際 model id。
   至少驗 `executor`（應回 opus 釘選值）與 `scout`（應回 haiku 釘選值）；有裝變體則各驗一次（應回釘死的完整版號）。
2. 任一角色解析錯誤 → 回頭檢查 Phase 3 的 env 鍵名拼字與重啟是否確實執行。

## 升級 / 回滾

- **升級**：plugin 更新後重跑本 skill 即可——Phase 1/2 的 diff-first 流程會把模板變更與本機客製攤開讓使用者逐項決定。上游（Nanako0129/pilotfish）的升級由 plugin 維護者手動 diff 其 `templates/agents/` 後搬移，不在本 skill 範圍。
- **回滾**：還原 `~/.claude/backups/settings.json.pilotfish-*` 最新備份；刪除本次安裝的 `~/.claude/agents/` 檔案；移除或還原 `~/.claude/rules/agents.md`。

## 授權

角色定義與政策模板衍生自 [Nanako0129/pilotfish](https://github.com/Nanako0129/pilotfish) v1.1.2（MIT），
完整授權與著作權聲明見 plugin 根目錄 `LICENSE.pilotfish`。
