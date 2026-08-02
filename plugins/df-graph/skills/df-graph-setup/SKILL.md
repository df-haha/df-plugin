---
name: df-graph-setup
description: 安裝並登入 df-graph（Microsoft 365 / Graph MCP server）——確認 uv、以 user-scope 註冊 MCP、device-code 登入、selftest 驗證、提醒重啟。每人跑一次。支援 Claude Code 與 Codex。觸發詞：「df-graph setup」「設定 df-graph」「裝 Graph MCP」「df-graph onboarding」。
allowed-tools: Bash, Read, AskUserQuestion
---

# df-graph Onboarding（每人一次）

把 df-graph（純雲端 Microsoft Graph MCP）裝起來：信箱 + 行事曆走 Graph API，**OS 無關**，
不需要 Windows / Outlook Desktop。全程一次 device-code 登入，token 存本機 `~/.df-graph/`。Claude Code 與 Codex 都以使用者層級註冊同一個 MCP server；依目前宿主選擇對應命令。

> ℹ️ **階段性提醒（措辭精確）**：目前是讀寫遷移的過渡期。
> **讀信（列信/搜信/讀信/下載附件）已全面走 Graph**；
> **寄信（日報、coaching 卡片）的 COM 寄送路徑，於 Stage B 完成前仍需 Windows + Outlook Desktop**。
> 純讀信用途裝好 df-graph 即可；需要自動寄信者請留意這點。

## Phase 0：前置確認

1. **uv 已安裝**（用來免污染地跑 Python MCP）：
   ```bash
   uv --version
   ```
   沒有 → 裝：`curl -LsSf https://astral.sh/uv/install.sh | sh`（或見 https://docs.astral.sh/uv/ ）。
2. 確認 plugin 已啟用——本 skill 來自 df-graph plugin。Claude Code 可使用 `${CLAUDE_PLUGIN_ROOT}`；Codex 需先從這份 `SKILL.md` 的路徑往上找到 plugin root（其下應有 `server/server.py`）。

## Phase 1：以 user-scope 註冊 MCP server

**用 user-scope 註冊，工具名才會是乾淨的 `mcp__df-graph__*`**（不要透過 plugin `.mcp.json`，
否則變成 `mcp__plugin_df-graph_df-graph__*`）：

Claude Code：

```bash
claude mcp add df-graph --scope user -- \
  uv run --with mcp --with msal python "${CLAUDE_PLUGIN_ROOT}/server/server.py"
```

Codex（將 `<plugin-root>` 換為本 skill 所在 plugin 的實際路徑）：

```bash
codex mcp add df-graph -- \
  uv run --with mcp --with msal python "<plugin-root>/server/server.py"
```

> 已存在同名註冊時，Claude Code 先跑 `claude mcp remove df-graph --scope user`；Codex 先跑 `codex mcp remove df-graph`，再重跑對應命令。

## Phase 2：device-code 登入（一次）

```bash
cd "${CLAUDE_PLUGIN_ROOT}/server" && uv run --with msal python login.py
```

Codex：

```bash
cd "<plugin-root>/server" && uv run --with msal python login.py
```

照終端指示開瀏覽器、輸入 device code、用**公司 Microsoft 365 帳號**登入並同意 4 個 scope
（Mail.ReadWrite / Mail.Send / Calendars.ReadWrite / Calendars.ReadWrite.Shared，員工可自批）。
成功後 token（含 refresh token）存 `~/.df-graph/`，之後自動續，不需重登。

## Phase 3：selftest 驗證（真實 Graph 唯讀）

Claude Code：

```bash
cd "${CLAUDE_PLUGIN_ROOT}/server" && uv run --with msal python selftest.py
```

Codex：

```bash
cd "<plugin-root>/server" && uv run --with msal python selftest.py
```

預期 7/7 通過。失敗多半是還沒登入（重跑 Phase 2）或公司帳號權限不足。

## Phase 4：重啟讓工具生效

提醒使用者：**重啟目前宿主的 session**，重啟後 `mcp__df-graph__*` 工具才會出現。
驗證（依宿主擇一）：

```bash
claude mcp list | grep df-graph    # 應顯示 df-graph ✓ Connected
```

```bash
codex mcp list                      # 應列出 df-graph
```

之後即可呼叫 `mcp__df-graph__mail_list_recent(days=1)` 等工具讀信。
