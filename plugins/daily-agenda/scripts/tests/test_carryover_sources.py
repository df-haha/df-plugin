"""Carried-over todos whose sources are not in this run still get readable labels and groups."""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from daily_agenda.render import build_view

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 20, 9, 0, tzinfo=TZ)
CHAT_A = "chat:aaaaaaaaaaaaaaaaaaaaaaaa"
CHAT_B = "chat:bbbbbbbbbbbbbbbbbbbbbbbb"


def _todo(tid: str, src_ids: list[str], priority: str = "P1") -> dict:
    return {
        "id": tid, "title": f"todo {tid}", "priority": priority, "due": None,
        "status": "open", "src_ids": src_ids,
        "created_at": (NOW - timedelta(days=3)).isoformat(), "updated_at": NOW.isoformat(),
        "maybe_done_since": None, "maybe_done_reason": None, "history": [],
    }


def _view(ledger: list[dict], known_chats: list[dict]) -> dict:
    return build_view(
        state={"report_code": "R-7K2", "report_index": {}, "known_chats": known_chats},
        ledger=ledger,
        bundle_meta={"sources": {"line": {"status": "ok"}, "mail": {"status": "ok"},
                                 "calendar": {"status": "ok"}}, "batch": {"index": 1, "total": 1}},
        events=[], new_chat_names=[], notices=[], now=NOW, report_code="R-7K2",
        records=[], chat_digests=[], mail_digests=[], folder_labels={}, chat_history={},
    )


def test_old_line_todos_group_by_known_chat_name() -> None:
    ledger = [
        _todo("T-1", [f"line:{CHAT_A}:m1"]),
        _todo("T-2", [f"line:{CHAT_A}:m2"]),
        _todo("T-3", [f"line:{CHAT_B}:m3"]),
    ]
    known = [{"ref": CHAT_A, "name": "金馬站營運群"}, {"ref": CHAT_B, "name": "廢鐵報價小組"}]
    v = _view(ledger, known)
    labels = {g["label"]: g["count"] for g in v["other_todo_groups"]}
    assert labels == {"金馬站營運群": 2, "廢鐵報價小組": 1}


def test_old_todo_source_label_never_shows_raw_src_id() -> None:
    ledger = [_todo("T-1", [f"line:{CHAT_A}:m1"]), _todo("T-2", ["mail:XYZ"]), _todo("T-3", ["event:E1"])]
    v = _view(ledger, [{"ref": CHAT_A, "name": "金馬站營運群"}])
    labels = [s["label"] for t in v["other_todos"] for s in t["sources"]]
    assert all(not lbl.startswith(("line:", "mail:", "event:")) for lbl in labels), labels
    groups = {g["label"] for g in v["other_todo_groups"]}
    assert groups == {"金馬站營運群", "信件", "行事曆"}


def test_message_ref_containing_colons_still_resolves_chat() -> None:
    ledger = [_todo("T-1", [f"line:{CHAT_A}:msg:12345"]), _todo("T-2", [f"line:{CHAT_A}:local:9:x"])]
    v = _view(ledger, [{"ref": CHAT_A, "name": "金馬站營運群"}])
    assert [(g["label"], g["count"]) for g in v["other_todo_groups"]] == [("金馬站營運群", 2)]


def test_unknown_chat_ref_falls_back_to_generic_line_group() -> None:
    v = _view([_todo("T-1", [f"line:{CHAT_B}:m1"])], [])
    assert [g["label"] for g in v["other_todo_groups"]] == ["LINE 對話"]
