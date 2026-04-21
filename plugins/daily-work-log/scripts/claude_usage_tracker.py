#!/usr/bin/env python3
"""
Claude Code 7d / 5h 用量查詢器

呼叫 undocumented endpoint `api.anthropic.com/api/oauth/usage`
（即 Claude Code `/usage` slash command 背後使用的 API），
回傳目前 5 小時 / 7 天滾動窗的用量百分比與重置時間。

僅限 Claude.ai 訂閱（Pro / Max),使用 API key 的用戶無法取得此資料。

Usage:
    python3 claude_usage_tracker.py           # 輸出 JSON 到 stdout
    python3 claude_usage_tracker.py --pretty  # 人類可讀摘要

輸出（成功）：
{
    "ok": true,
    "generated_at": "2026-04-21T10:30:00+08:00",
    "subscription_type": "max_5x",
    "five_hour":  { "utilization_pct": 13, "resets_at_utc": "...", "resets_at_local": "YYYY-MM-DD HH:MM GMT+8" },
    "seven_day":  { "utilization_pct": 38, "resets_at_utc": "...", "resets_at_local": "..." },
    "seven_day_sonnet": { ... } | null,
    "seven_day_opus":   { ... } | null,
    "raw": { ...原始 response... }    # 僅 --include-raw 時才有
}

輸出（失敗）：
{
    "ok": false,
    "error": "credentials_not_found" | "token_expired" | "endpoint_error" | "network_error",
    "detail": "..."
}

注意：此 endpoint 未被 Anthropic 公開文檔化，可能隨時變更 schema 或下線。
失敗時不拋 exception，只回傳 ok=false，讓 skill 端能優雅降級。
"""
from __future__ import annotations

import argparse
import json
import sys
import urllib.error
import urllib.request
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any

TZ_GMT8 = timezone(timedelta(hours=8))
CREDENTIALS_PATH = Path.home() / ".claude" / ".credentials.json"
USAGE_ENDPOINT = "https://api.anthropic.com/api/oauth/usage"
ANTHROPIC_BETA_HEADER = "oauth-2025-04-20"
REQUEST_TIMEOUT_SEC = 10


def _error(code: str, detail: str) -> dict:
    return {"ok": False, "error": code, "detail": detail}


def _load_credentials() -> dict:
    """讀 ~/.claude/.credentials.json，回傳 claudeAiOauth 區塊。"""
    if not CREDENTIALS_PATH.exists():
        raise FileNotFoundError(
            f"credentials 檔不存在：{CREDENTIALS_PATH}。請先在 Claude Code 登入訂閱帳號。"
        )
    try:
        data = json.loads(CREDENTIALS_PATH.read_text(encoding="utf-8"))
    except (json.JSONDecodeError, UnicodeDecodeError) as e:
        raise ValueError(f"credentials 檔格式錯誤：{e}")

    oauth = data.get("claudeAiOauth") or {}
    token = oauth.get("accessToken")
    if not token:
        raise ValueError("credentials 內找不到 claudeAiOauth.accessToken（是否使用 API key 而非訂閱？）")
    return oauth


def _token_expired(oauth: dict) -> bool:
    """以 expiresAt（毫秒 epoch）判斷 token 是否過期。"""
    expires_at = oauth.get("expiresAt")
    if not expires_at:
        return False  # 沒有 expiresAt 就交給 server 端判
    try:
        # expiresAt 是毫秒 epoch；用 float() 才能同時處理 "1745..." 和 "1745....0"
        exp_dt = datetime.fromtimestamp(float(expires_at) / 1000, tz=timezone.utc)
    except (ValueError, TypeError):
        return False
    return datetime.now(timezone.utc) >= exp_dt


def _call_usage_api(token: str) -> dict:
    """呼叫 oauth/usage endpoint。"""
    req = urllib.request.Request(
        USAGE_ENDPOINT,
        headers={
            "Authorization": f"Bearer {token}",
            "anthropic-beta": ANTHROPIC_BETA_HEADER,
            "Accept": "application/json",
        },
        method="GET",
    )
    with urllib.request.urlopen(req, timeout=REQUEST_TIMEOUT_SEC) as resp:
        body = resp.read().decode("utf-8")
    return json.loads(body)


def _format_reset(resets_at_iso: str | None) -> dict:
    """把 ISO-8601 時間串格式化為 UTC + 當地 (GMT+8)。"""
    if not resets_at_iso:
        return {"resets_at_utc": None, "resets_at_local": None}
    try:
        dt = datetime.fromisoformat(resets_at_iso.replace("Z", "+00:00"))
    except ValueError:
        return {"resets_at_utc": resets_at_iso, "resets_at_local": None}
    utc = dt.astimezone(timezone.utc).isoformat().replace("+00:00", "Z")
    local = dt.astimezone(TZ_GMT8).strftime("%Y-%m-%d %H:%M GMT+8")
    return {"resets_at_utc": utc, "resets_at_local": local}


def _normalize_window(window: Any) -> dict | None:
    """把 endpoint 的 {utilization, resets_at} 規範化為我們的 schema。"""
    if not isinstance(window, dict):
        return None
    util = window.get("utilization")
    if util is None:
        return None
    try:
        pct = round(float(util))
    except (TypeError, ValueError):
        return None
    return {
        "utilization_pct": pct,
        **_format_reset(window.get("resets_at")),
    }


def fetch_usage(include_raw: bool = False) -> dict:
    """主流程。失敗時回傳 {ok: false, error, detail}。

    include_raw=True 時才在成功輸出裡附上原始 API response（避免未來 endpoint
    schema 新增欄位時意外把敏感資料帶進 stdout / log）。
    """
    try:
        oauth = _load_credentials()
    except FileNotFoundError as e:
        return _error("credentials_not_found", str(e))
    except ValueError as e:
        return _error("credentials_invalid", str(e))

    if _token_expired(oauth):
        return _error(
            "token_expired",
            "OAuth token 已過期。請在 Claude Code 重新登入（/login 或重啟）以觸發 refresh。",
        )

    token = oauth["accessToken"]
    try:
        raw = _call_usage_api(token)
    except urllib.error.HTTPError as e:
        body = ""
        try:
            body = e.read().decode("utf-8", errors="replace")[:300]
        except OSError:
            pass
        return _error(
            "endpoint_error",
            f"HTTP {e.code}: {e.reason}. Body: {body or '(empty)'}",
        )
    except urllib.error.URLError as e:
        return _error("network_error", f"無法連線到 {USAGE_ENDPOINT}:{e.reason}")
    except json.JSONDecodeError as e:
        return _error("endpoint_error", f"回應非合法 JSON:{e}")
    except Exception as e:  # noqa: BLE001 — 任何其他錯誤都要降級，不可打斷 skill
        return _error("unknown_error", f"{type(e).__name__}: {e}")

    result: dict = {
        "ok": True,
        "generated_at": datetime.now(TZ_GMT8).isoformat(),
        "subscription_type": oauth.get("subscriptionType"),
        "rate_limit_tier": oauth.get("rateLimitTier"),
        "five_hour": _normalize_window(raw.get("five_hour")),
        "seven_day": _normalize_window(raw.get("seven_day")),
        "seven_day_sonnet": _normalize_window(raw.get("seven_day_sonnet")),
        "seven_day_opus": _normalize_window(raw.get("seven_day_opus")),
    }
    if include_raw:
        result["raw"] = raw
    return result


def _pretty_print(data: dict) -> str:
    if not data.get("ok"):
        return f"[無法取得 Claude Code 用量] {data.get('error')}: {data.get('detail', '')}"
    lines = [
        f"訂閱:{data.get('subscription_type') or 'N/A'}(tier: {data.get('rate_limit_tier') or 'N/A'})",
    ]

    def _fmt_window(label: str, w: dict | None) -> str | None:
        if not w:
            return None
        pct = w["utilization_pct"]
        reset = w.get("resets_at_local") or w.get("resets_at_utc") or "N/A"
        return f"{label}:{pct}%(重置 {reset})"

    for label, key in [
        ("5 小時窗", "five_hour"),
        ("7 天窗(總)", "seven_day"),
        ("7 天窗 — Sonnet", "seven_day_sonnet"),
        ("7 天窗 — Opus", "seven_day_opus"),
    ]:
        line = _fmt_window(label, data.get(key))
        if line:
            lines.append(line)
    return "\n".join(lines)


def main() -> int:
    parser = argparse.ArgumentParser(description="Claude Code 7d/5h 用量查詢")
    parser.add_argument("--pretty", action="store_true", help="輸出人類可讀摘要（預設 JSON）")
    parser.add_argument(
        "--include-raw",
        action="store_true",
        help="同時附上原始 API response；預設不附，避免未來 schema 變動把敏感資料帶進輸出",
    )
    args = parser.parse_args()

    result = fetch_usage(include_raw=args.include_raw)
    if args.pretty:
        print(_pretty_print(result))
    else:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    return 0 if result.get("ok") else 2


if __name__ == "__main__":
    sys.exit(main())
