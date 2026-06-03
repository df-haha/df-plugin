"""om-daily-work-log send_coaching_cards 純函式測試（Phase 3 directive contract + bug 修正）。

鎖住四項修正：
1. directive marker 契約（compose/reply 共用，屬下端可搜）
2. render_card_html 用 target_date（去寫死 "5/6"）+ 嵌 marker
3. build_reply_ps 嚴格 email 比對、**無** GetFirst 串錯人 fallback、找不到回 not_found
4. compose 路徑（CreateItem 新信）
"""

from __future__ import annotations

import re
import sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT / "scripts"))

import send_coaching_cards as scc  # noqa: E402


CARD = {
    "yaml": {
        "card_id": "card-abc-123",
        "target_work_date": "2026-06-02",
        "employee": {"name": "A Chen", "email": "a.chen@acme.example", "member_id": "a-chen"},
    },
    "body_md": "## Q1\n問題內容\n- 重點",
}


# --- directive marker 契約 ----------------------------------------------

def test_build_directive_marker_parseable():
    marker = scc.build_directive_marker("card-abc-123", "2026-06-02", "a-chen", "compose")
    m = scc.DIRECTIVE_MARKER_RE.search(marker)
    assert m is not None
    meta = dict(re.findall(r"(\w+)=(\S+)", m.group("meta")))
    assert meta["directive_id"] == "card-abc-123"
    assert meta["target_date"] == "2026-06-02"
    assert meta["employee_id"] == "a-chen"
    assert meta["source"] == "compose"


def test_build_compose_subject():
    subj = scc.build_compose_subject("【每日追蹤】", "A Chen", "2026-06-02")
    assert subj == "【每日追蹤】 A Chen 2026-06-02"


# --- render_card_html ---------------------------------------------------

def test_render_uses_target_date_not_hardcoded():
    html = scc.render_card_html(CARD, CARD["body_md"], "2026-06-02", source="reply")
    assert "2026-06-02" in html
    assert "5/6" not in html               # 去寫死


def test_render_embeds_marker_with_source():
    html = scc.render_card_html(CARD, CARD["body_md"], "2026-06-02", source="compose")
    m = scc.DIRECTIVE_MARKER_RE.search(html)
    assert m is not None
    assert "source=compose" in m.group(0)
    assert "directive_id=card-abc-123" in m.group(0)


# --- build_reply_ps：strict email、無 GetFirst、not_found ----------------

def test_reply_ps_strict_email_no_getfirst():
    ps = scc.build_reply_ps(
        "A Chen", "a.chen@acme.example", "每日工作報告 2026/06/02",
        "Daily Reports", "<b>hi</b>", "", "$reply.Display()", "draft",
    )
    assert "SenderEmailAddress" in ps              # 嚴格 email 比對
    assert "-ieq $email" in ps
    assert "GetFirst()" not in ps                  # 串錯人 fallback 已移除
    assert "not_found" in ps                       # 找不到回 not_found 供轉 compose


def test_reply_ps_honors_custom_folder_and_subject():
    """Codex F2：自訂 report folder / subject 須被嵌入 PS（非預設 tenant 才找得到原日報）。"""
    ps = scc.build_reply_ps(
        "A Chen", "a.chen@acme.example", "Daily Report 2026/06/02",
        "Custom Folder", "<b>hi</b>", "", "$reply.Display()", "draft",
    )
    assert "Custom Folder" in ps
    assert "Daily Report 2026/06/02" in ps
    assert "每日工作報告" not in ps          # 不應殘留中文預設


def test_reply_ps_name_fallback_when_no_email():
    """無 email 時退回 EndsWith 精確比對，仍不可用 *name* 子字串。"""
    ps = scc.build_reply_ps(
        "A Chen", "", "每日工作報告 2026/06/02", "Daily Reports",
        "x", "", "$reply.Display()", "draft",
    )
    assert ".EndsWith($name)" in ps
    assert "-like '*" not in ps                    # 不用危險子字串比對


# --- build_compose_ps ---------------------------------------------------

def test_compose_ps_creates_new_mail():
    ps = scc.build_compose_ps(
        "a.chen@acme.example", "【每日追蹤】 A Chen 2026-06-02",
        "<b>hi</b>", "", "$mail.Display()", "draft",
    )
    assert "CreateItem(0)" in ps                   # olMailItem 新信
    assert "$mail.To = 'a.chen@acme.example'" in ps
    assert "$mail.Subject = '【每日追蹤】 A Chen 2026-06-02'" in ps


def test_open_compose_requires_email():
    """缺 email 不可開新信（防寄到空地址）。"""
    res = scc.open_compose_draft("", "subj", "<b>x</b>", None, False)
    assert res["status"] == "failed"
    assert "email" in res["error"]
