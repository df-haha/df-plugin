"""fetch_daily_reports.py — 已退役（RETIRED）。

本腳本原透過 WSL wrapper 呼叫 PowerShell + Outlook COM 抓取團隊日報附件。
此路徑已遷移至 df-graph MCP（Microsoft Graph API）讀取路徑，
由 team-daily-fetcher skill 的 Step 3/4 直接走
mcp__df-graph__folder_list / mail_list_recent / mail_get / mail_download_attachment 完成。

遷移細節請見：
- skills/team-daily-fetcher/SKILL.md（read-side 執行流程）
- README.md（安裝 df-graph plugin 步驟）

如需重新啟用 Outlook COM 路徑（例如 Stage B send-side 測試），請從 git history 取回舊版。
"""

import sys


def main() -> int:
    print(
        "fetch_daily_reports.py 已退役；"
        "團隊日報擷取改由 team-daily-fetcher skill 走 df-graph MCP 工具。",
        file=sys.stderr,
    )
    return 1


if __name__ == "__main__":
    sys.exit(main())
