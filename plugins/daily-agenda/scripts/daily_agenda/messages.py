"""Error code → plain-language (繁體中文) message. Codes go to logs; users see these sentences."""

from __future__ import annotations

_MESSAGES: dict[str, str] = {
    # consent / setup
    "CONSENT_REQUIRED": "尚未完成資料外送說明與同意，請先執行 /agenda-setup。",
    "CONFIG_MISSING": "還沒有設定，請先執行 /agenda-setup。",
    # LINE
    "LINE_NOT_RUNNING": "LINE 沒有在執行。請打開 LINE 並登入後再試。",
    "SESSION_KEY_UNAVAILABLE": "讀不到 LINE 的登入資料。請打開 LINE 視窗（不要只縮在右下角）、確認已登入後再試。",
    "SESSION_KEY_CHANGED": "LINE 在讀取途中重新登入或切換帳號了，請稍後再試。",
    "LINE_BUILD_UNVERIFIED": "LINE 更新到新版本，需要你確認一次才能繼續讀取（手動執行 /agenda 會帶你完成）。",
    "MAIN_DATABASE_AMBIGUOUS": "這台電腦有多份 LINE 資料，無法自動判斷是哪一個帳號，請重跑 /agenda-setup。",
    "MAIN_DATABASE_NOT_FOUND": "找不到設定中的 LINE 資料檔，可能換過帳號，請重跑 /agenda-setup。",
    "LINE_SIGNATURE_INVALID": "LINE 程式的數位簽章無法驗證，為了安全先不讀取 LINE。",
    "LINE_RUNTIME_MISSING": "LINE 讀取元件還沒安裝好，請重跑 /agenda-setup。",
    "RECENT_SCOPE_TOO_LARGE": "最近的 LINE 對話超過 1000 個，超出可讀範圍。",
    "RESULT_TOO_LARGE": "某個 LINE 對話的單則內容太大，無法讀取。",
    "LOCAL_READER_FAILED": "LINE 讀取失敗，原因不明；其他來源照常彙整，下次會補讀。",
    # Outlook / df-graph
    "DF_GRAPH_NOT_REGISTERED": "還沒安裝或登入 df-graph（讀 Outlook 用）。請先執行 /df-graph-setup。",
    "DF_GRAPH_TOO_OLD": "df-graph 版本太舊，缺少本工具需要的功能，請更新 df-graph plugin。",
    "DF_GRAPH_AUTH": "Outlook 登入已過期，請重新執行 /df-graph-setup 的登入步驟。",
    "GRAPH_FAILED": "讀取 Outlook 失敗；其他來源照常彙整，下次會補讀。",
    # analysis / output
    "ANALYZER_TOOLS_NOT_EMPTY": "分析程序意外載入了工具，為了安全已中止，這次不產生彙整。",
    "ANALYZER_FAILED": "AI 分析失敗，這次不產生彙整；資料進度不會前進，下次會重讀。",
    "ANALYZER_TIMEOUT": "AI 分析逾時，這次不產生彙整；下次會重讀。",
    "MODEL_OUTPUT_INVALID": "AI 回傳的格式不正確，這次不產生彙整；下次會重讀。",
    "PUBLISH_FAILED": "彙整檔寫到輸出資料夾失敗（資料已保存），下次執行會先補寫。",
    "STALE_REPORT": "你指的不是最新一份彙整，為避免改錯，請改用待辦代碼（T-xxxx）或重新確認。",
    "CONFIRM_INVALID": "確認內容有誤，請檢查後重新貼上（所有項目均未變更）。",
    "LOCKED": "已經有一個彙整正在執行，請稍後再試。",
    # environment
    "NODE_MISSING": "需要 Node.js 24 以上。請在 PowerShell 執行：winget install OpenJS.NodeJS.LTS",
    "NODE_TOO_OLD": "Node.js 版本太舊（需要 24 以上）。請在 PowerShell 執行：winget upgrade OpenJS.NodeJS.LTS",
    "UV_MISSING": "需要 uv。請在 PowerShell 執行：powershell -ExecutionPolicy ByPass -c \"irm https://astral.sh/uv/install.ps1 | iex\"",
    "CLAUDE_FLAGS_MISSING": "Claude Code 版本太舊，缺少安全分析需要的功能，請更新 Claude Code。",
    "NOT_WINDOWS": "本工具目前只支援 Windows 原生 Claude Code。",
    "LOCK_HOOK_UNAVAILABLE": "安全鎖無法執行（找不到 node 或檢查失敗），為了安全不能繼續設定。請安裝 Node.js 24 以上後重試。",
    "GRAPH_TRUNCATED": "這段期間的信件太多，超過一次可安全讀取的上限；為了不漏信，這次先不彙整信件，請把較舊的信整理或封存後再試。",
    "MAIL_FOLDER_MISSING": "有些選取的信箱資料夾已不存在（可能被刪除或改名了），這些資料夾的信件這次沒讀到。請執行 /agenda-setup → 只改信箱資料夾 來更新。",
    "MAIL_FOLDER_PARTIAL": "部分信箱資料夾讀取失敗，這些資料夾的信件這次沒讀到；其他資料夾照常讀取，下次會補讀。",
    "LINE_TIMEOUT": "讀取 LINE 逾時；其他來源照常彙整，下次會補讀。",
    "MULTIPLE_DATABASES_OK": "這台電腦有多份 LINE 資料都讀得到，需要你選擇最近在用的那一份。",
    # trust (D-11)
    "TRUSTED_BUILDS_CORRUPT": "LINE 信任清單檔案損毀。請備份後刪除該檔，再重跑 /agenda-setup。",
    "LINE_PROBE_FAILED": "LINE 試讀失敗，無法確認讀取功能正常；信任清單未變更。",
    # runtime (D-5)
    "RUNTIME_HASH_MISMATCH": "下載的檔案雜湊不符，可能遭竄改，已刪除。請重試，若持續失敗請回報。",
    "RUNTIME_OUTDATED": "LINE 讀取元件版本與 plugin 不同步，請重跑 /agenda-setup 的安裝步驟。",
    "RUNTIME_DOWNLOAD_FAILED": "下載元件失敗，請確認網路連線後重試。",
    "RUNTIME_ZIP_UNSAFE": "下載的壓縮檔含有不安全的路徑，已拒絕解壓。請回報此問題。",
    "RUNTIME_PLATFORM_MISMATCH": "Python 環境不是 Windows 64 位元，無法安裝所需元件。",
    "RUNTIME_INSTALL_FAILED": "元件安裝失敗，舊版本已保留。請確認磁碟空間後重試。",
    # refine
    "NO_FEEDBACK": "最近沒有任何標記紀錄，無法自動產生修正建議。",
    "REFINE_NONINTERACTIVE": "排程執行不會觸發判斷準則修正，請手動執行。",
    # CLI
    "ARG_ERROR": "指令參數不正確，請確認用法。",
    "INTERNAL_ERROR": "發生內部錯誤。請把這個訊息告訴維護者。",
}


def humanize(code: str | None) -> str:
    """Return a plain-language sentence for ``code``; unknown codes get a generic sentence."""
    if not code:
        return ""
    return _MESSAGES.get(code, f"發生未預期的狀況（代碼 {code}），請把這個代碼告訴維護者。")


def known_codes() -> frozenset[str]:
    return frozenset(_MESSAGES)
