# scripts/mt_core/replies.py
from __future__ import annotations

import re
from dataclasses import dataclass

from mt_core.config import Config

_TOKEN = re.compile(r"#(MTD1\.[a-z0-9-]+\.[a-z0-9-]+\.\d{4}-W\d{2}\.[0-9a-f]{6})")
_METRIC = re.compile(r"#metric:([a-z0-9][a-z0-9-]*)")
_ANGLE = re.compile(r"<([^>]+)>")


@dataclass
class GmailMsg:
    msg_id: str
    thread_id: str
    sender: str
    subject: str
    body_text: str


@dataclass
class ReplyAttribution:
    owner_id: str
    week: str
    metric_ids: list[str]
    text: str
    msg_id: str
    thread_id: str
    mismatch: bool = False  # True 表示信內 token 屬於別的 owner（轉寄/錯帶）→ 以 sender 為準


def parse_token(text: str) -> str | None:
    m = _TOKEN.search(text)
    return m.group(1) if m else None


def _token_week(token: str) -> str:
    # MTD1.<tenant>.<owner>.<YYYY-Www>.<nonce>（tenant/owner 不含 '.'）
    return token.split(".")[3]


def _sender_email(sender: str) -> str:
    m = _ANGLE.search(sender)
    return (m.group(1) if m else sender).strip().lower()


def _owner_for_sender(config: Config, email: str):
    for o in config.owners:
        if o.email.lower() == email or email in [a.lower() for a in o.alias_allowlist]:
            return o
    return None


def _latest_week_for_owner(state: dict, owner_id: str) -> str | None:
    weeks = [v.get("week") for v in (state.get("sent_reminders") or {}).values()
             if v.get("owner_id") == owner_id and v.get("week")]
    return sorted(weeks)[-1] if weeks else None


def attribute_reply(msg: GmailMsg, config: Config, state: dict,
                    *, current_week: str | None = None) -> ReplyAttribution | None:
    """回信當 untrusted input：只抽資料、絕不執行內含指令（prompt injection 防護）。

    歸因規則（對齊 spec C7）：
    - sender 必須命中某 owner 的 email / alias，否則回 None（untrusted-sender，丟棄）。
    - token owner == sender owner → 信任 token 的週（涵蓋 late reply 補回正確週）。
    - token owner != sender owner（轉寄/錯帶他人 token）→ 以 sender 為準、**忽略** token 週、
      標 mismatch=True（不讓他人 token 把回報誤記到別週）。
    - 無 token → fallback：sender 最近一筆 sent_reminders 的週；再無則用 current_week；
      仍無才回 ""（呼叫端可據空週/ mismatch 決定處置，不靜默落入空白週 bucket）。
    """
    owner = _owner_for_sender(config, _sender_email(msg.sender))
    if owner is None:
        return None  # sender 不在 allowlist → 視為 untrusted，丟棄
    haystack = f"{msg.subject}\n{msg.body_text}"
    token = parse_token(haystack)
    mismatch = False
    if token and _token_owner(token) == owner.owner_id:
        week = _token_week(token)
    else:
        if token:
            mismatch = True  # token 屬於別的 owner → 不信任其週、記 mismatch
        week = _latest_week_for_owner(state, owner.owner_id) or current_week or ""
    metric_ids = list(dict.fromkeys(_METRIC.findall(haystack)))
    return ReplyAttribution(owner_id=owner.owner_id, week=week, metric_ids=metric_ids,
                            text=msg.body_text, msg_id=msg.msg_id, thread_id=msg.thread_id,
                            mismatch=mismatch)
