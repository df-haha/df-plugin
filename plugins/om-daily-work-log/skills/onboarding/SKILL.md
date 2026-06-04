---
name: work-log-onboarding
description: 引導屬下（員工）把 om-daily-work-log 裝起來——安裝 df-graph MCP plugin（Microsoft Graph 雲端讀信，OS 無關）、設自己的 member_id 與主管收件人、驗證能讀信並產出日報。觸發詞：「工作日誌 onboarding」「設定 daily work-log」「work-log setup」。
allowed-tools: Bash, Read, Write, Edit, AskUserQuestion, ToolSearch, TaskCreate, TaskUpdate, TaskList
---

# om-daily-work-log Onboarding Wizard（屬下端）

每位屬下跑一次。互動式，把日報 + async coaching loop 接起來。**全程零 tenant hard-code**——
所有環境特定值（member_id、主管 email）都問你、用 `<...>` 佔位，技術碼裡不寫死任何人名/路徑。

> ⚠️ 本 skill **刻意不依賴** `mcp__df-graph__*`——因為 MCP 還沒設好時也要能跑 onboarding。
> 設好之後，日常的「日報」skill（`om-daily-work-log`）才會用到 df-graph MCP 讀信。

> ℹ️ **階段性說明（措辭精確）**：
> **讀信（偵測主管催辦信）已走 df-graph（Microsoft Graph 雲端）**，OS 無關，不需要 Windows/Outlook Desktop 來讀信。
> **寄信（日報的 COM 寄送路徑）於 Stage B 完成前仍需 Windows + Outlook Desktop**。
> 純讀信用途裝好 df-graph 即可；需要自動寄信者請留意此限制。

## Phase 0：前置確認（先問清楚再動工）

用 AskUserQuestion 確認：
1. **uv 已安裝**（df-graph plugin 用 uv 跑 Python MCP server）：
   ```bash
   uv --version
   ```
   沒有 → 裝：`curl -LsSf https://astral.sh/uv/install.sh | sh`（或見 https://docs.astral.sh/uv/ ）
2. **寄信需求**：需要自動寄日報者，請確認 Windows 10+ ＋ Outlook Desktop（桌面版）＋ Python 3.8+（COM 路徑，Stage B 才會遷移）。僅需讀信偵測的情境 OS 不限。
3. **不適用情況**：若**沒有 Outlook Desktop** 且需要 **自動寄日報** → 明說 Stage B COM 路徑尚未完成，需手動寄出日報 md 檔。讀信偵測（Phase 0）本身 OS 無關可正常運作。

## Phase 1：安裝並登入 df-graph plugin

讀信改走 df-graph（Microsoft Graph 雲端 API），**不需 clone 任何 repo、不需 COM/pywin32**。

1. **確認 df-graph plugin 已啟用**（在 `${CLAUDE_PLUGIN_ROOT}` 下有 `server/server.py`）。
   如果你是從 df-plugin 套件安裝的，plugin 已內建；若看不到請先啟用 df-graph plugin。

2. **以 user-scope 註冊 MCP server**（工具名才會是乾淨的 `mcp__df-graph__*`）：
   ```bash
   claude mcp add df-graph --scope user -- \
     uv run --with mcp --with msal python "${CLAUDE_PLUGIN_ROOT}/server/server.py"
   ```
   > 已存在同名註冊時先移除：`claude mcp remove df-graph --scope user` 再重跑。

3. **device-code 登入（一次，之後 token 自動續）**：
   ```bash
   cd "${CLAUDE_PLUGIN_ROOT}/server" && uv run --with msal python login.py
   ```
   照終端指示開瀏覽器、輸入 device code、用**公司 Microsoft 365 帳號**登入並同意 4 個 scope
   （Mail.ReadWrite / Mail.Send / Calendars.ReadWrite / Calendars.ReadWrite.Shared）。
   成功後 token 存 `~/.df-graph/`，之後自動續，不需重登。

4. **selftest 驗證（真實 Graph 唯讀）**：
   ```bash
   cd "${CLAUDE_PLUGIN_ROOT}/server" && uv run --with msal python selftest.py
   ```
   預期 7/7 通過。失敗多半是還沒完成登入（重跑步驟 3）或公司帳號權限不足。

5. **重啟 Claude Code session**，重啟後 `mcp__df-graph__*` 工具才會出現：
   ```bash
   claude mcp list | grep df-graph    # 應顯示 df-graph ✓ Connected
   ```

## Phase 2：設身分（member_id）＋ 主管收件人

1. **member_id（關鍵）**：填你自己的 `member_id`，**必須與主管 cockpit config 的
   `team.members[].member_id` 完全一致**——主管端 directive 靠 marker `employee_id==你的 member_id`
   篩信，不一致就收不到催辦信。不確定你的值 → 直接問主管。寫進你本機小設定供日報 skill 讀：
   ```bash
   mkdir -p ~/.claude/om-daily-work-log
   # 寫入 ~/.claude/om-daily-work-log/config.json：{"member_id": "<你的-member-id>"}
   ```
2. **主管 email（日報收件人）**：日報 skill Phase 6 寄出時用。擇一：
   - 寄信時帶 `--to <主管 email>`，或
   - 事先設在 `~/.claude/daily-work-log/config.json` 的 `outlook_email`：
     ```bash
     mkdir -p ~/.claude/daily-work-log
     # 寫入 ~/.claude/daily-work-log/config.json：{"outlook_email": "<主管 email>"}
     ```
     > ⚠️ 這裡是 `daily-work-log`（非 `om-daily-work-log`）路徑——因為內建寄信腳本
     > `send_work_log_email.py` 固定讀這個路徑（與 daily-work-log plugin 相同慣例）。沒裝那個
     > plugin 也沒關係，只是共用同一個設定檔位置。

## Phase 3：驗證 ＋ 驗收

1. 確認 df-graph 可用：
   `ToolSearch("select:mcp__df-graph__mail_list_recent")` → 呼叫 `mcp__df-graph__mail_list_recent(days=1)` 試讀近 1 日信。
   抓不到 → 回 Phase 1 檢查是否已重啟 session、`claude mcp list | grep df-graph` 是否顯示 Connected。
2. 對 Claude 說「**日報**」跑一次 `om-daily-work-log` skill，確認：
   - **日報 md 真的產出**在 `daily_proposal/daily_work_log_{今天日期}.md`；
   - 若主管當日有寄催辦信，能偵測到並引導你回覆。
3. 全綠 → onboarding 完成。之後每天說「日報」即可跑完整 coaching loop。
