# scripts/mt_core/run.py
from __future__ import annotations

from datetime import date

from typing import Callable

from mt_core.digest import compose_digest, correlation_token
from mt_core.state import reminder_key, record_sent, already_processed_reply, record_reply
from mt_core.replies import attribute_reply, GmailMsg
from mt_core.timeutil import iso_week_str


def _mask(email: str) -> str:
    name, sep, dom = email.partition("@")
    return (name[0] + "***@" + dom) if (sep and name) else email


def send_digests(config, reminders, state: dict, adapter, today: date,
                 *, dry_run: bool = False, persist: Callable[[], None] | None = None) -> dict:
    """兩階段送信（P0-2 crash-window 防護）：
    1. 先把 idempotency key 寫成 status='pending' 並 persist（落盤）。
    2. 送信（idempotency_key 傳給 adapter，寄送端據此去重）。
    3. 標 status='sent' + provider id 並 persist。
    跳過條件只看 status=='sent'；'pending'（上次 crash 殘留）會重送，但 adapter 端靠 key 不重寄。
    `persist` 由呼叫端傳（CLI = lambda: save_state(...)）；測試可傳 None（純記憶體）。
    """
    week = iso_week_str(today)
    today_str = today.isoformat()
    persist = persist or (lambda: None)
    summary: dict = {"sent": 0, "skipped": 0, "owners": []}
    for r in reminders:
        key = reminder_key(config.tenant_id, f"digest:{r.owner.owner_id}", week, today_str)
        rec = state["sent_reminders"].get(key)
        if rec and rec.get("status") == "sent":
            summary["skipped"] += 1
            continue
        token = correlation_token(config.tenant_id, r.owner.owner_id, week)
        digest = compose_digest(r, config.tenant_id, week, token=token)
        if dry_run:
            summary["owners"].append({"owner_id": r.owner.owner_id,
                                      "to_masked": _mask(digest.to), "dry_run": True})
            continue
        # phase 1：pending 落盤
        record_sent(state, key, owner_id=r.owner.owner_id, week=week, date=today_str,
                    correlation_token=token, idempotency_key=key, status="pending")
        persist()
        # phase 2：送信（帶 idempotency_key 供寄送端去重）
        result = adapter.send(to=digest.to, subject=digest.subject,
                              body_text=digest.body_text, body_html=digest.body_html,
                              idempotency_key=key)
        # phase 3：標 sent 落盤
        state["sent_reminders"][key].update(status="sent",
                                            provider_message_id=result.provider_message_id,
                                            sent_at=today_str)
        for metric, _tracked in r.metrics:
            state.setdefault("metric_last_nudge", {})[metric.metric_id] = today_str
        persist()
        summary["sent"] += 1
        summary["owners"].append({"owner_id": r.owner.owner_id, "to_masked": _mask(digest.to),
                                  "provider_message_id": result.provider_message_id})
    return summary


def collect_replies(config, msgs: list[GmailMsg], state: dict,
                    *, current_week: str | None = None) -> tuple[list, dict]:
    """回傳 (本批所有可信回信的 attribution → 供 render, summary)。

    ⚠️ P0-3 修正：dedup（already_processed_reply）只決定「是否計為新 / record」，
    **不從 render 輸入排除舊回報**。否則 Routine 每跑重抓整週 Gmail 時，上次已處理的回信
    會被 dedup 掉，draft regenerate 後當週舊回報會消失。故所有可信回信都進 all_attrs。

    Codex WF2 #1 修正：`current_week` 透傳給 attribute_reply，讓「無 token 且 state 無歷史」
    的可信回信（首跑 / CC 代回）落到本週、不致 week="" 而被 render 分組丟棄。
    """
    all_attrs: list = []
    summary: dict = {"processed_new": 0, "already_seen": 0, "untrusted": 0}
    for m in msgs:
        attr = attribute_reply(m, config, state, current_week=current_week)
        if attr is None:
            summary["untrusted"] += 1
            continue
        all_attrs.append(attr)                       # 所有可信回信都進 render 輸入
        if already_processed_reply(state, m.msg_id):
            summary["already_seen"] += 1
            continue
        record_reply(state, m.msg_id, owner_id=attr.owner_id, week=attr.week,
                     metric_ids=attr.metric_ids, thread_id=attr.thread_id)
        summary["processed_new"] += 1
    return all_attrs, summary
