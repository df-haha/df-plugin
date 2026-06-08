#!/usr/bin/env python3
"""Stop — coaching 防跳過守衛（關卡 A）。

判準（避免誤擋合法 no-op）：只有當「今日 daily_proposal 含待釐清訊號」**且**
「對應 target_date 的引導卡檔不存在」**且**「未標 coaching_waived」時才 block 逼補。

防迴圈：同 (session_id, target_date, cwd) 已擋過一次 → 放行（讓使用者補完/說明後能收尾）。
找不到 config（非此 tenant）/ 今日無 daily_proposal / 任何內部錯誤 → 放行（fail-open）。
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import sys
import tempfile
from datetime import date, datetime
from pathlib import Path
from zoneinfo import ZoneInfo

sys.path.insert(0, str(Path(__file__).resolve().parent))
sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
from _coaching_hooklib import find_config_path, load_config_safe  # noqa: E402

# 防迴圈 state（同 OS session 跨 Stop 持久）。env 可覆寫，供測試隔離。
_STATE = Path(
    os.environ.get("OM_COCKPIT_STOP_GUARD_STATE")
    or str(Path(tempfile.gettempdir()) / "om_cockpit_stop_guard_blocked.json")
)

_WAIVED = re.compile(r"coaching_waived:\s*true", re.IGNORECASE)


def has_clarification_signal(text: str) -> bool:
    """daily_proposal 是否含「待釐清訊號」（❓ 待釐清疑問 區塊 / coaching_required marker）。"""
    return ("❓ 待釐清疑問" in text) or ("coaching_required" in text)


def is_coaching_waived(text: str) -> bool:
    return bool(_WAIVED.search(text))


def stop_should_block(proposal_text: str | None, card_exists: bool) -> tuple[bool, str]:
    """純判準：回 (should_block, short_reason_tag)。"""
    if proposal_text is None:
        return (False, "no_proposal")          # 今日無 daily_proposal → 不關 /hi 的事
    if is_coaching_waived(proposal_text):
        return (False, "waived")               # 明示豁免
    if not has_clarification_signal(proposal_text):
        return (False, "no_signal")            # 合法 no-op：沒有待釐清就不需卡片
    if card_exists:
        return (False, "card_present")         # 已補卡
    return (True, "missing_card")


def _block_key(session_id: str, target_date: str, cwd: str) -> str:
    cwd_hash = hashlib.sha1(cwd.encode("utf-8")).hexdigest()[:12]
    return f"{session_id}:{target_date}:{cwd_hash}"


def _already_blocked(key: str) -> bool:
    try:
        return key in set(json.loads(_STATE.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        return False


def _record_blocked(key: str) -> None:
    try:
        seen = set(json.loads(_STATE.read_text(encoding="utf-8")))
    except Exception:  # noqa: BLE001
        seen = set()
    seen.add(key)
    try:
        _STATE.write_text(json.dumps(sorted(seen)), encoding="utf-8")
    except Exception:  # noqa: BLE001
        pass


def _compute_dates(tz_name: str) -> tuple[date, date]:
    """回 (today, target_date)；target_date = today 的前一個工作日。"""
    from last_workday import get_last_workday  # noqa: E402

    tz = ZoneInfo(tz_name)
    today = datetime.now(tz).date()
    return today, get_last_workday(today)


def main() -> None:
    raw = sys.stdin.read()
    data = json.loads(raw) if raw.strip() else {}

    # 防迴圈保命索（不依賴 state 檔健康）：Claude Code 因前一個 Stop block 而重觸 Stop 時，
    # 會設 stop_hook_active=true。直接放行，避免 state 寫入失敗時無限 re-block 卡死 session。
    if data.get("stop_hook_active"):
        sys.exit(0)

    session_id = str(data.get("session_id", ""))
    cwd = data.get("cwd") or "."

    cfg_path = find_config_path(Path(cwd))
    if not cfg_path:
        sys.exit(0)  # 非此 tenant，靜默放行
    config = load_config_safe(cfg_path)
    if config is None:
        sys.exit(0)

    today, target_date = _compute_dates(config.timezone)
    tenant_root = cfg_path.parent.parent  # {root}/.om-cockpit/config.md → {root}
    proposal_dir = tenant_root / config.paths.daily_proposal_dir

    proposal_path = proposal_dir / f"daily_proposal_{today.isoformat()}.md"
    proposal_text = proposal_path.read_text(encoding="utf-8") if proposal_path.is_file() else None
    card_path = proposal_dir / f"team_coaching_cards_{target_date.isoformat()}.md"

    should_block, _tag = stop_should_block(proposal_text, card_path.is_file())
    if not should_block:
        sys.exit(0)

    key = _block_key(session_id, target_date.isoformat(), cwd)
    if _already_blocked(key):
        sys.exit(0)  # 同 session+date 已擋過 → 放行，避免卡死
    _record_blocked(key)

    reason = (
        f"daily_proposal_{today.isoformat()} 有待釐清事項（❓ 待釐清疑問 / coaching_required），"
        f"但缺 {target_date.isoformat()} 引導信草稿（team_coaching_cards_{target_date.isoformat()}.md）。"
        f"請呼叫 team-coaching-cards 補產草稿；若確實不需要，向使用者說明後在報告加一行 "
        f"`coaching_waived: true`。"
    )
    print(json.dumps({"decision": "block", "reason": reason}, ensure_ascii=False))
    sys.exit(0)


if __name__ == "__main__":
    try:
        main()
    except SystemExit:
        raise
    except Exception as e:  # noqa: BLE001 — fail-open：Stop 守衛壞掉不可卡死收尾
        print(f"[stop_coaching_guard] 內部錯誤，放行：{e}", file=sys.stderr)
        sys.exit(0)
