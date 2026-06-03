---
name: work-log-onboarding
description: 引導屬下（員工）把 om-daily-work-log 裝起來——確認 Windows/Outlook/Python 環境、安裝 outlook-local MCP server、設自己的 member_id 與主管收件人、驗證能讀信並產出日報。觸發詞：「工作日誌 onboarding」「設定 daily work-log」「work-log setup」。
allowed-tools: Bash, Read, Write, Edit, AskUserQuestion, ToolSearch, TaskCreate, TaskUpdate, TaskList
---

# om-daily-work-log Onboarding Wizard（屬下端）

每位屬下跑一次。互動式，把日報 + async coaching loop 接起來。**全程零 tenant hard-code**——
所有環境特定值（clone 路徑、member_id、主管 email）都問你、用 `<...>` 佔位，技術碼裡不寫死任何人名/路徑。

> ⚠️ 本 skill **刻意不依賴** `mcp__outlook-local__*`——因為 MCP 還沒設好時也要能跑 onboarding。
> 設好之後，日常的「日報」skill（`om-daily-work-log`）才會用到 outlook-local MCP。

## Phase 0：前置確認（先問清楚再動工）

用 AskUserQuestion 確認：
1. **環境**：是否 Windows 10+ ＋ **Outlook Desktop（桌面版，非網頁版）** ＋ Python 3.8+？
   outlook-local MCP 走 Windows COM（`pywin32`），三者缺一不可。
2. **Claude Code 跑在哪**：Windows-native（原生 Windows）還是 WSL？——決定 MCP config 的寫法。
3. **不適用情況**：若**非 Windows** 或**沒有 Outlook Desktop**（只有網頁版 / Mac / 純 Linux）→
   明說此 plugin 的自動讀信/寄信不適用，請改走**人工 Outlook 降級**（自己開 Outlook 收主管催辦信、
   手動把日報 md 貼進信件寄出）。日報產出（Phase 1）本身仍可用，只是少了自動偵測與寄送。

## Phase 1：安裝 outlook-local MCP server

上游是公開 Python MCP server：`github.com/marlonluo2018/outlook-mcp-server`。

1. **clone**（放一個你記得住的絕對路徑，例如 `<你的工具目錄>/outlook-mcp-server`）：
   ```bash
   git clone https://github.com/marlonluo2018/outlook-mcp-server <absolute-clone-path>
   ```
2. **裝相依**（擇一）：
   - **uvx（推薦，免裝進系統）**：config 直接用 uvx 拉（見下方範本）。
   - **pip**：`cd <absolute-clone-path> && pip install -r requirements.txt`。
3. **COM smoke test**（Windows 端跑，確認 pywin32 能連 Outlook）：
   ```bash
   python -c "import win32com.client; win32com.client.Dispatch('Outlook.Application'); print('COM OK')"
   ```
   失敗 → 多半是 Outlook Desktop 沒開、或 pywin32 沒裝好（`pip install "pywin32>=226"`）。
4. **在 MCP config 加一個 stdio server，名稱必須叫 `outlook-local`**（本 plugin 呼叫 `mcp__outlook-local__*`，
   ⚠️ **不是**上游預設的 `outlook-mcp-server`）。**用絕對路徑，別用 `.` 當 cwd**。擇一範本：

   - **Windows-native（uvx）**——這組 args 與上游 `mcp-config-uvx.json` 一致（`--with-editable <clone>`
     會把你 clone 的本地套件以 editable 裝入，其 distribution name 即 `outlook-mcp-server`，
     故末尾同名 positional 由本地 editable 套件滿足、不會去 PyPI 抓）：
     ```json
     "outlook-local": {
       "command": "uvx",
       "args": ["--with", "pywin32>=226", "--with-editable", "<absolute-clone-path>", "outlook-mcp-server"]
     }
     ```
     > uvx 解析不到時，改用下方 `python -m` / run.bat 範本（等效、最穩）。
   - **Windows-native（python -m，或 run.bat）**：
     ```json
     "outlook-local": { "command": "python", "args": ["-m", "outlook_mcp_server"], "cwd": "<absolute-clone-path>" }
     ```
     （或 `{"command": "cmd.exe", "args": ["/c", "<absolute-clone-path>\\run.bat"]}`，run.bat 內含 `cd /d <path>` + `python -m outlook_mcp_server`）
   - **WSL（呼叫 Windows 端 run.bat）**：
     ```json
     "outlook-local": { "command": "cmd.exe", "args": ["/c", "<windows-clone-path>\\run.bat"] }
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

1. 確認 outlook-local 可用：
   `ToolSearch("select:mcp__outlook-local__list_recent_emails_tool")` → 試讀近 1 日信。
   抓不到 → 回 Phase 1 檢查 server 名稱（必須 `outlook-local`）與路徑。
2. 對 Claude 說「**日報**」跑一次 `om-daily-work-log` skill，確認：
   - **日報 md 真的產出**在 `daily_proposal/daily_work_log_{今天日期}.md`；
   - 若主管當日有寄催辦信，能偵測到並引導你回覆。
3. 全綠 → onboarding 完成。之後每天說「日報」即可跑完整 coaching loop。
