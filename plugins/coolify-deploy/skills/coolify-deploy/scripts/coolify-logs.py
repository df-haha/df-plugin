#!/usr/bin/env python3
"""coolify-logs.py — 安全讀取 Coolify deployment build log。

Coolify 把 build log 存在 application_deployment_queues.logs；API 在 token 無
read:sensitive 時會 makeHidden(['logs'])，所以讀 log 需要 read:sensitive token。
而 read:sensitive 同時能讀整個 team 的 env 明文與 SSH 私鑰，是高風險憑證。

安全策略（多層）：
  1. token 只從環境變數讀（COOLIFY_LOG_TOKEN），絕不寫死、絕不印出。
  2. 主防線：丟掉 Coolify 標記 hidden=true 的 entry——那些正是含密鑰的行，
     等同 Web UI 正常檢視看到的安全版（非 Show Debug Logs）。
  3. 第二層：對殘留的非-hidden 行跑 pattern 遮罩（前綴型 token、KEY=值/KEY: 值、
     CLI --token、連線字串、Bearer、PEM、長高熵字串）。遮罩是 best-effort，不是
     完整 secret scanner——真正的保證來自第 2 點 drop-hidden。

只輸出清洗後的 log，可安全貼給 AI / 寫進 transcript。

用法：
  export COOLIFY_URL='https://coolify.example.com'
  export COOLIFY_LOG_TOKEN='<read:sensitive token>'   # 建議短效、用完撤
  python coolify-logs.py --app <app-uuid>              # 抓最新一筆部署
  python coolify-logs.py --deployment <deployment-uuid>
"""
import argparse
import json
import os
import re
import ssl
import sys
import urllib.error
import urllib.request

# --- 遮罩規則（best-effort 第二層；主防線是 drop hidden）---

# 已知前綴型 secret（長度/字元集不一定命中通用長字串規則）
_KNOWN = re.compile(
    r"(?:"
    r"gh[opsur]_[A-Za-z0-9]{20,}"                 # GitHub PAT / OAuth
    r"|github_pat_[A-Za-z0-9_]{20,}"
    r"|A(?:KIA|SIA)[0-9A-Z]{16}"                  # AWS access key id
    r"|xox[baprs]-[A-Za-z0-9-]{10,}"              # Slack
    r"|AIza[0-9A-Za-z_\-]{35}"                    # Google API key
    r"|(?:sk|rk)_live_[0-9A-Za-z]{16,}"           # Stripe live
    r"|eyJ[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+\.[A-Za-z0-9_\-]+"  # JWT
    r")"
)

# 連線字串以拼接組出，避免原始碼留下連續的 proto://a:b@ 字面（過 secret 掃描）
_PROTO = "://"
_CRED = re.compile(_PROTO + r"[^\s:@/]+" + r":" + r"[^\s@]+" + r"@")

_BEARER = re.compile(r"(?i)(authorization:\s*bearer\s+)\S+")
_PEM = re.compile(r"-----BEGIN[^-]*PRIVATE KEY-----.*?-----END[^-]*PRIVATE KEY-----", re.S)

# 機密欄位名 → 遮值，涵蓋 KEY=value / KEY: value / "KEY": "value"（env / JSON / YAML）
_SECRET_NAME = (
    r"[A-Za-z0-9_]*(?:SECRET|PASSWORD|PASSWD|TOKEN|API[_-]?KEY|ACCESS[_-]?KEY"
    r"|PRIVATE[_-]?KEY|CREDENTIAL|DSN)[A-Za-z0-9_]*"
)
_KV = re.compile(r'(?i)("?\b' + _SECRET_NAME + r'\b"?\s*[=:]\s*)("[^"]*"|\'[^\']*\'|\S+)')
# CLI 旗標：--token value / --password=value
_CLI = re.compile(
    r"(?i)(--(?:password|passwd|token|secret|api[-_]?key|access[-_]?key)(?:=|\s+))"
    r"(\"[^\"]*\"|'[^']*'|\S+)"
)
# 通用長高熵字串（>=44）；不含 "/" 以免吃掉檔案路徑；40-hex commit SHA 不在範圍
_LONG = re.compile(r"\b[A-Za-z0-9+=_\-]{44,}\b")


def redact(text: str) -> str:
    text = _PEM.sub("[REDACTED-PRIVATE-KEY]", text)
    text = _KNOWN.sub("[REDACTED]", text)
    text = _CRED.sub(_PROTO + "[REDACTED]@", text)
    text = _BEARER.sub(r"\1[REDACTED]", text)
    text = _KV.sub(r"\1[REDACTED]", text)
    text = _CLI.sub(r"\1[REDACTED]", text)
    text = _LONG.sub("[REDACTED]", text)
    return text


def api_get(base_url: str, path: str, token: str) -> dict:
    req = urllib.request.Request(
        base_url.rstrip("/") + path,
        headers={"Authorization": "Bearer " + token, "Accept": "application/json"},
    )
    with urllib.request.urlopen(req, context=ssl.create_default_context(), timeout=30) as resp:
        return json.loads(resp.read().decode())


def render(logs_field, include_hidden: bool, do_redact: bool):
    """回傳 (text, dropped_hidden, total_entries)。對非預期 shape 容錯，不丟 traceback。"""
    entries = logs_field
    if isinstance(entries, str):
        try:
            entries = json.loads(entries)
        except (ValueError, TypeError):
            text = redact(entries) if do_redact else entries  # 非 JSON → 當純文字
            return text, 0, 0
    if not isinstance(entries, list):
        text = json.dumps(entries, ensure_ascii=False)
        return (redact(text) if do_redact else text), 0, 0

    def _order(e):
        v = e.get("order", 0) if isinstance(e, dict) else 0
        return v if isinstance(v, (int, float)) else 0

    entries = sorted(entries, key=_order)
    lines, dropped = [], 0
    for e in entries:
        if not isinstance(e, dict):
            lines.append(str(e))
            continue
        if e.get("hidden") and not include_hidden:
            dropped += 1
            continue
        cmd = e.get("command") or ""
        output = e.get("output") or ""
        if not isinstance(output, str):
            output = str(output)
        block = (("$ " + str(cmd) + "\n") if cmd else "") + output.rstrip("\n")
        lines.append(block.rstrip("\n"))
    text = "\n".join(lines)
    if do_redact:
        text = redact(text)
    return text, dropped, len(entries)


def main() -> int:
    ap = argparse.ArgumentParser(description="安全讀取 Coolify deployment log（預設丟 hidden 行 + 遮罩）")
    ap.add_argument("--app", help="application uuid（抓最新一筆部署）")
    ap.add_argument("--deployment", help="deployment uuid（指定某次部署）")
    ap.add_argument("--base-url", help="Coolify base URL（預設讀 COOLIFY_URL）")
    ap.add_argument(
        "--include-hidden",
        action="store_true",
        help="危險：連 hidden（含密鑰）也輸出，需同時加 --i-understand-secrets-may-leak",
    )
    ap.add_argument(
        "--i-understand-secrets-may-leak",
        dest="confirm_leak",
        action="store_true",
        help="確認你了解 --include-hidden 會輸出明文密鑰（僅本機自看，勿外流 / 勿貼 AI）",
    )
    ap.add_argument("--no-redact", action="store_true", help="關閉 pattern 遮罩（仍丟 hidden，除非也下 --include-hidden）")
    args = ap.parse_args()

    if args.include_hidden and not args.confirm_leak:
        sys.stderr.write(
            "拒絕：--include-hidden 會輸出含明文密鑰的行。若你確實了解風險"
            "（僅本機自看、勿貼進 AI / transcript / 外流），請同時加 "
            "--i-understand-secrets-may-leak\n"
        )
        return 2

    token = os.environ.get("COOLIFY_LOG_TOKEN") or os.environ.get("COOLIFY_TOKEN")
    base_url = args.base_url or os.environ.get("COOLIFY_URL")
    if not token:
        sys.stderr.write("錯誤：需要 COOLIFY_LOG_TOKEN（read:sensitive token）環境變數，勿寫死於檔案\n")
        return 2
    if not base_url:
        sys.stderr.write("錯誤：需要 COOLIFY_URL 或 --base-url\n")
        return 2

    dep = args.deployment
    try:
        if not dep:
            if not args.app:
                sys.stderr.write("錯誤：需 --deployment 或 --app\n")
                return 2
            data = api_get(base_url, f"/api/v1/deployments/applications/{args.app}?skip=0&take=1", token)
            deps = data.get("deployments") if isinstance(data, dict) else None
            if not deps:
                sys.stderr.write("找不到部署紀錄（app uuid 對嗎？）\n")
                return 1
            dep = deps[0].get("deployment_uuid")
        d = api_get(base_url, f"/api/v1/deployments/{dep}", token)
    except urllib.error.HTTPError as e:
        sys.stderr.write(f"API 錯誤 HTTP {e.code}：{e.reason}（token 權限不足或 uuid 錯？）\n")
        return 1
    except (urllib.error.URLError, ValueError) as e:
        sys.stderr.write(f"連線或解析失敗：{type(e).__name__}\n")
        return 1

    logs = d.get("logs") if isinstance(d, dict) else None
    if not logs:
        sys.stderr.write(
            "無 logs 內容。可能原因：token 缺 read:sensitive 權限被 makeHidden；"
            "deployment 還沒產生 log（極早期失敗）；uuid 錯；部署紀錄已被清理。"
            "先別急著升權，逐一排除。\n"
        )
        return 1

    if args.include_hidden:
        sys.stderr.write("⚠️  --include-hidden：輸出含 hidden 行（明文密鑰），切勿貼給 AI 或寫進 transcript\n")

    text, dropped, total = render(logs, args.include_hidden, not args.no_redact)
    note = "（含 hidden）" if args.include_hidden else "（已略過 hidden 行，殘留行盡力遮罩）"
    print(
        f"# deployment {dep}  status={d.get('status')}  app={d.get('application_name')}  "
        f"created={d.get('created_at')}  entries={total}  dropped_hidden={dropped} {note}"
    )
    print(text)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
