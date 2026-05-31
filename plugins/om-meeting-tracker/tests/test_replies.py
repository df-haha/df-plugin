# tests/test_replies.py
from __future__ import annotations
from datetime import date
from mt_core.config import Config, Owner, Metric, Paths, SendCfg
from mt_core.replies import parse_token, attribute_reply, GmailMsg, ReplyAttribution

def _cfg():
    return Config(1, "acme", "Asia/Taipei", "monday", "wednesday",
                  ["mon","tue","wed","thu","fri"],
                  Paths("t.md","d/","c/","state/s.json","run-log/"), SendCfg("n8n_webhook"),
                  [Owner("alice","Alice","alice@example.com",["alice.work@example.com"]),
                   Owner("bob","Bob","bob@example.com",[])],
                  [Metric("g1","alice","G1",date(2026,5,31),"daily","mtg")])

def test_parse_token_in_subject():
    s = "Re: 進度回報 (2026-W22) [#MTD1.acme.alice.2026-W22.ab12cd]"
    assert parse_token(s) == "MTD1.acme.alice.2026-W22.ab12cd"

def test_attribute_trusted_sender_with_token():
    msg = GmailMsg("g1","t1","Alice <alice@example.com>",
                   "Re: (2026-W22) [#MTD1.acme.alice.2026-W22.ab12cd]",
                   "進度更新 [#metric:g1] 完成投標")
    attr = attribute_reply(msg, _cfg(), {"sent_reminders": {}})
    assert attr is not None
    assert attr.owner_id == "alice" and attr.week == "2026-W22"
    assert attr.metric_ids == ["g1"]

def test_untrusted_sender_returns_none():
    msg = GmailMsg("g2","t2","stranger@evil.com",
                   "[#MTD1.acme.alice.2026-W22.ab12cd]", "hi")
    assert attribute_reply(msg, _cfg(), {"sent_reminders": {}}) is None

def test_alias_sender_matched():
    msg = GmailMsg("g3","t3","alice.work@example.com",
                   "[#MTD1.acme.alice.2026-W22.ab12cd]", "ok")
    attr = attribute_reply(msg, _cfg(), {"sent_reminders": {}})
    assert attr is not None and attr.owner_id == "alice"

def test_late_reply_uses_token_week_not_current():
    # token 指向 W21（上週）→ 歸 W21，不誤記本週
    msg = GmailMsg("g4","t4","alice@example.com",
                   "[#MTD1.acme.alice.2026-W21.ab12cd]", "補上週進度")
    attr = attribute_reply(msg, _cfg(), {"sent_reminders": {}})
    assert attr.week == "2026-W21"

def test_no_token_fallback_to_latest_sent_week():
    msg = GmailMsg("g5","t5","alice@example.com", "改了主旨沒 token", "進度更新")
    state = {"sent_reminders": {
        "k1": {"owner_id": "alice", "week": "2026-W20"},
        "k2": {"owner_id": "alice", "week": "2026-W22"},
    }}
    attr = attribute_reply(msg, _cfg(), state)
    assert attr.week == "2026-W22"  # 取該 owner 最近一筆

def test_metric_ids_dedup():
    msg = GmailMsg("g6","t6","alice@example.com",
                   "[#MTD1.acme.alice.2026-W22.ab12cd]",
                   "[#metric:g1] ... [#metric:g1] 重複")
    attr = attribute_reply(msg, _cfg(), {"sent_reminders": {}})
    assert attr.metric_ids == ["g1"]

def test_token_owner_mismatch_uses_sender_and_flags():
    # alice 轉寄了帶 bob token 的信 → 不信任 bob 的週、以 sender 為準、標 mismatch。
    state = {"sent_reminders": {"k": {"owner_id": "alice", "week": "2026-W20"}}}
    msg = GmailMsg("g7","t7","alice@example.com",
                   "Fwd: [#MTD1.acme.bob.2026-W22.ab12cd]", "代轉內容")
    attr = attribute_reply(msg, _cfg(), state)
    assert attr is not None and attr.owner_id == "alice"
    assert attr.week == "2026-W20"   # sender 自己的週，不是 bob token 的 W22
    assert attr.mismatch is True

def test_token_cross_tenant_rejected():
    # token tenant 與 config.tenant_id 不符（acme）→ 不採信、標 mismatch、退回 current_week。
    msg = GmailMsg("g8","t8","alice@example.com",
                   "[#MTD1.other-co.alice.2026-W22.ab12cd]", "x")
    attr = attribute_reply(msg, _cfg(), {"sent_reminders": {}}, current_week="2026-W30")
    assert attr is not None and attr.mismatch is True
    assert attr.week == "2026-W30"

def test_metric_ownership_filter_drops_foreign_metric():
    # alice 在內文引用 bob 名下指標（不在 alice 名下）→ 必須被濾掉，不可越權塞別人格子。
    cfg = _cfg()  # config 只定義 g1（alice 名下）；bob 無 metric
    msg = GmailMsg("g9","t9","alice@example.com",
                   "[#MTD1.acme.alice.2026-W22.ab12cd]",
                   "[#metric:g1] 我的 [#metric:bob-secret] 想塞別人的")
    attr = attribute_reply(msg, cfg, {"sent_reminders": {}})
    assert attr.metric_ids == ["g1"]   # bob-secret 被濾掉

def test_no_token_no_history_falls_back_to_current_week():
    msg = GmailMsg("g10","t10","alice@example.com", "進度", "沒 token 也沒歷史")
    attr = attribute_reply(msg, _cfg(), {"sent_reminders": {}}, current_week="2026-W30")
    assert attr is not None and attr.week == "2026-W30" and attr.mismatch is False
