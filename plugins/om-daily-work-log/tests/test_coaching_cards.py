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


def test_reply_ps_navigates_configured_account():
    """Codex R2-F3：有 outlook_account → 導覽該帳號 store inbox（非只看 GetDefaultFolder）。"""
    ps = scc.build_reply_ps(
        "A", "a@x.example", "Sub", "Folder", "h", "", "$reply.Display()", "draft",
        outlook_account="boss@acme.example", inbox_name="收件匣",
    )
    assert "boss@acme.example" in ps
    assert "$namespace.Folders.Item($account)" in ps
    assert "GetDefaultFolder(6)" in ps          # 仍保留 fallback


def test_reply_ps_default_account_when_empty():
    ps = scc.build_reply_ps(
        "A", "a@x.example", "Sub", "Folder", "h", "", "$reply.Display()", "draft",
    )
    assert "GetDefaultFolder(6)" in ps


def test_reply_ps_name_fallback_when_no_email():
    """無 email 時退回 EndsWith 精確比對，仍不可用 *name* 子字串。"""
    ps = scc.build_reply_ps(
        "A Chen", "", "每日工作報告 2026/06/02", "Daily Reports",
        "x", "", "$reply.Display()", "draft",
    )
    assert ".EndsWith($name)" in ps
    assert "-like '*" not in ps                    # 不用危險子字串比對


# --- build_compose_ps ---------------------------------------------------

def test_reply_ps_escapes_single_quotes():
    """Codex R3-F3：含單引號的值不破壞 PS 語法（' → '')。"""
    ps = scc.build_reply_ps(
        "O'Connor", "a@x.example", "Sub's", "Fol'der", "h", "", "$reply.Display()", "draft",
        outlook_account="boss@acme.example", inbox_name="收件匣",
    )
    assert "O''Connor" in ps
    assert "Sub''s" in ps
    assert "Fol''der" in ps


def test_reply_ps_resolves_exchange_smtp():
    """Codex R3-F1：Exchange legacyDN → 多解 PrimarySmtpAddress 再比。"""
    ps = scc.build_reply_ps(
        "A", "a@x.example", "S", "F", "h", "", "$reply.Display()", "draft",
    )
    assert "GetExchangeUser().PrimarySmtpAddress" in ps
    assert "SenderEmailType" in ps


def test_compose_ps_escapes_single_quotes():
    """Codex R3-F3：compose 的 email/subject 含單引號也不破壞。"""
    ps = scc.build_compose_ps(
        "o'brien@x.example", "Today's Follow-up", "h", "", "$mail.Display()", "draft",
    )
    assert "o''brien@x.example" in ps
    assert "Today''s Follow-up" in ps


def test_resolve_report_subject_placeholders():
    """Codex R3-F2：{date}=ISO、{date_slash}=slash，兩種日期格式都精確。"""
    assert scc.resolve_report_subject("Daily Report {date}", "2026-06-02") == "Daily Report 2026-06-02"
    assert scc.resolve_report_subject("每日工作報告 {date_slash}", "2026-06-02") == "每日工作報告 2026/06/02"
    assert scc.resolve_report_subject(None, "2026-06-02") is None


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
