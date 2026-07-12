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

1. 確認 `~/.claude/` 存在且目前宿主是 Claude Code，並建立安裝目標目錄（新機器上可能都還不存在，缺目錄會讓後續複製與備份直接失敗）：
   ```bash
   mkdir -p ~/.claude/agents ~/.claude/rules ~/.claude/backups
   ```
2. **`CLAUDE_CODE_SUBAGENT_MODEL` 必須未設**（它會靜默覆蓋所有 per-agent model，破壞整套分層）。逐層檢查 shell env 與 settings 優先序鏈（user / project / project-local / managed）：
   ```bash
   env | grep CLAUDE_CODE_SUBAGENT_MODEL
   grep -l CLAUDE_CODE_SUBAGENT_MODEL ~/.claude/settings.json .claude/settings.json .claude/settings.local.json /etc/claude-code/managed-settings.json 2>/dev/null
   ```
   任一處命中都停下，回報命中的具體來源，請使用者移除後再繼續。
3. 偵測既有安裝：`ls ~/.claude/agents/` 若已有同名檔 → 本次進入**升級模式**（Phase 1/2 一律先 diff、不盲蓋）。

## Phase 1：安裝 agent 檔（保持原名，不走 plugin 命名空間）

刻意用「複製到 `~/.claude/agents/`」而非 plugin 原生 agents 目錄：plugin 原生 agent 會被加上
`pilotfish-roles:` 前綴，路由表與使用習慣全要跟著改。installer 形式讓 `executor`、`scout` 保持素名。

1. 用 AskUserQuestion 問是否安裝 2 個版本變體（executor-opus47 / executor-opus45），組出**本次安裝清單**：6 個核心角色一律在列，變體依回答加入。之後只處理清單內的檔案，不要用 glob 全掃（否則變體詢問形同虛設）。
2. 逐檔處理清單內的 `templates/agents/<名>.md` → `~/.claude/agents/<同名>.md`：
   - 目標不存在 → 直接複製，清單標記「新建」。
   - 目標已存在 → 先 `mkdir -p` 並備份到 `~/.claude/backups/agents-<時間戳>/`，再 `diff` 模板與本機檔；**無差異**跳過（標記「跳過」），**有差異**把 diff 呈現給使用者選（保留本機／採用模板／手動合併），依選擇標記「保留」或「覆蓋」。本機檔可能載有使用者客製，是資產不是垃圾，永不盲蓋。
3. 完成後列出安裝清單（檔名＋該檔 frontmatter 的 `model:` 值＋新建/覆蓋/保留/跳過標記）——回滾時以此清單為準。

## Phase 2：委派政策（draft-first，人審後才落正式位置）

1. 把 `templates/rules/agents.md` 複製到暫存路徑（scratchpad 或 `/tmp`）作為草稿。
2. `~/.claude/rules/agents.md` 不存在 → 呈現草稿要點（角色路由表＋委派規則條數）請使用者確認後安裝。
   已存在 → 先備份到 `~/.claude/backups/`（同 Phase 3 命名慣例），再 `diff` 草稿與本機檔呈現差異，讓使用者決定：整檔採用／保留本機／逐段合併。
3. **未經使用者確認前不得寫入** `~/.claude/rules/`。
4. 提醒：政策內引用的 review 工具鏈（dev-workflow 等）是 df-haha rules 全套的一部分，新機器若沒有那些 rules，相關條文照常保留即可（引用落空不影響路由本身）。另政策模板以 df-haha 的主模型（Fable 5）行文（開頭的 orchestrator 敘述與 #13、#14）；安裝者主模型不同時，在 draft 審閱階段把主模型敘述改成自己實際的主模型——分層路由原則本身與主模型是誰無關。
5. 若 Phase 1 未安裝版本變體，呈現草稿時一併講明：政策 #12 與路由表中的 `executor-opus47` / `executor-opus45` 要重跑本 skill 加裝後才可用，未裝前對其派工會找不到 agent。

## Phase 3：settings.json env 釘選（先備份，只動 env 三鍵）

別名解析未釘選前，`opus` / `sonnet` / `haiku` 會解析到 CC 當下預設（可能是最新版模型），
與政策「版本受控」的前提矛盾——**這一步不是可選的**。

> 適用範圍：本 skill 假設 **Anthropic API 直連**。Bedrock / Vertex 部署的模型 ID 命名不同，
> 這三個預設值不適用——偵測到該類部署（如 `CLAUDE_CODE_USE_BEDROCK` / `VERTEX` 相關 env）先停下與使用者確認。

1. 先 Read `~/.claude/settings.json` 確認現況（改前必讀，不憑記憶）。檔案不存在（新機器常見）→ 建立內容為 `{}` 的新檔、跳過備份步驟，並在安裝清單記錄「settings.json 為本次新建」（回滾時刪檔而非還原）。同時檢查頂層 `model`：若為 tier 別名（`opus` / `sonnet` / `haiku`），先停下警告——env 釘選會連動主迴圈的別名解析，重啟後主模型會跟著換版；請使用者選擇接受連動，或先把頂層 `model` 改為完整 model ID 再繼續。
2. 備份既有檔（任一步失敗即**中止**，不得繼續改 settings）：
   ```bash
   mkdir -p ~/.claude/backups && chmod 700 ~/.claude/backups && \
   BK=~/.claude/backups/settings.json.pilotfish-$(date +%Y%m%d-%H%M%S) && \
   cp ~/.claude/settings.json "$BK" && chmod 600 "$BK"
   ```
3. 用 AskUserQuestion 問三個釘選值，預設值（df-haha 2026-07-11 拍板，可改）：
   - `ANTHROPIC_DEFAULT_OPUS_MODEL` = `claude-opus-4-6[1m]`
   - `ANTHROPIC_DEFAULT_SONNET_MODEL` = `claude-sonnet-4-6`
   - `ANTHROPIC_DEFAULT_HAIKU_MODEL` = `claude-haiku-4-5-20251001`
4. 只在 `env` 區塊 upsert 這三鍵，其餘內容原樣保留。**不碰**頂層 `model`（主迴圈模型是個人選擇；另注意 `"best"` 不支援 `[1m]` 後綴）。
5. 改完立即驗證整檔仍是合法 JSON（如 `python3 -m json.tool ~/.claude/settings.json`）；驗證失敗 → 既有檔用步驟 2 的備份還原、本次新建者直接刪除該檔，回報後停止，不留半壞檔。
6. 紅線：不新增 `CLAUDE_CODE_SUBAGENT_MODEL`。

## Phase 4：重啟與驗證

env 與 agent 檔改動需重啟 session 才生效。請使用者重啟後執行驗證：

1. 對每個要驗的角色 spawn 一次（Agent tool），請 agent 回報它 system prompt 中的實際 model id。
   至少驗 `executor`（應回 opus 釘選值）與 `scout`（應回 haiku 釘選值）；有裝變體則各驗一次（應回釘死的完整版號）。
2. 任一角色解析錯誤 → 回頭檢查 Phase 3 的 env 鍵名拼字與重啟是否確實執行。

## 升級 / 回滾

- **升級**：plugin 更新後重跑本 skill 即可——Phase 1/2 的 diff-first 流程會把模板變更與本機客製攤開讓使用者逐項決定。上游（Nanako0129/pilotfish）的升級由 plugin 維護者手動 diff 其 `templates/agents/` 後搬移，不在本 skill 範圍。
- **回滾**：以 Phase 1 安裝清單為準——標記「新建」的 `~/.claude/agents/` 檔才刪除，標記「覆蓋」的用 `~/.claude/backups/agents-<時間戳>/` 還原；**不要按檔名整批刪**（會誤刪使用者原有同名檔）。settings.json 還原 `~/.claude/backups/settings.json.pilotfish-*` 最新備份（安裝清單記錄為本次新建者 → 刪除該檔）；`~/.claude/rules/agents.md` 同理：本次新建才移除，否則以備份還原。

## 授權

角色定義與政策模板衍生自 [Nanako0129/pilotfish](https://github.com/Nanako0129/pilotfish) v1.1.2（MIT），
完整授權與著作權聲明見 plugin 根目錄 `LICENSE.pilotfish`。
