"""Tests for the digest HTML template — CSP hash, XSS, backward compat.

These tests construct view dicts directly (no dependency on build_view
internals for the new C3 keys).
"""

from __future__ import annotations

import base64
import hashlib
import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser
from pathlib import Path

import pytest

from daily_agenda.digest_script import DIGEST_SCRIPT, DIGEST_SCRIPT_HASH
from daily_agenda.fsutil import atomic_write_text
from daily_agenda.render import render

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 20, 9, 0, 0, tzinfo=TZ)


# ---------------------------------------------------------------------------
# Helpers — construct view dicts directly per C3 contract
# ---------------------------------------------------------------------------

def _todo_view(
    no: int = 1,
    todo_id: str = "T-0001",
    priority: str = "P1",
    title: str = "正常待辦",
    src_label: str = "來源",
    reason: str = "",
) -> dict:
    t: dict = {
        "no": no,
        "todo_id": todo_id,
        "priority": priority,
        "title": title,
        "due": "2026-10-22",
        "carry_days": 2,
        "sources": [{"label": src_label, "href": None}],
    }
    if reason:
        t["reason"] = reason
    return t


def _chat(
    name: str = "測試群組",
    kind_label: str = "群組，5 人",
    msg_count: int = 10,
    state: str = "needs",
    needs_reply: list[dict] | None = None,
    bullets: list[str] | None = None,
    todos: list[dict] | None = None,
    history: list[dict] | None = None,
    pulse: dict | None = None,
) -> dict:
    return {
        "name": name,
        "kind_label": kind_label,
        "msg_count": msg_count,
        "state": state,
        "needs_reply": needs_reply or [],
        "bullets": bullets or ["討論了什麼。"],
        "todos": todos or [],
        "history": history or [],
        "pulse": pulse or {
            "dots": [{"x": 100.0, "reply": False}, {"x": 400.0, "reply": True}],
            "from": "10/19 08:30",
            "to": "10/20 08:30",
        },
    }


def _quiet_chat(
    name: str = "閒聊群",
    kind_label: str = "群組，3 人",
    msg_count: int = 2,
    summary: str = "沒什麼重要的。",
) -> dict:
    return {
        "name": name,
        "kind_label": kind_label,
        "msg_count": msg_count,
        "summary": summary,
    }


def _mail(
    sender: str = "某人",
    when: str = "10/19 16:20",
    subject: str = "主旨",
    category: str = "reply",
    summary: str = "摘要",
    todos: list[dict] | None = None,
) -> dict:
    return {
        "from": sender,
        "when": when,
        "subject": subject,
        "category": category,
        "summary": summary,
        "todos": todos or [],
    }


def _mail_folder(
    folder_label: str = "收件匣",
    mails: list[dict] | None = None,
) -> dict:
    return {
        "folder_label": folder_label,
        "mails": mails or [_mail()],
    }


def _base_view(**overrides: object) -> dict:
    """Minimal v1-compatible view dict (old keys only)."""
    defaults: dict = {
        "report_code": "R-7K2",
        "date": "2026-10-20",
        "generated_at": NOW.isoformat(),
        "sources": [
            {"name": "LINE", "status": "ok", "message": "已讀取", "coverage": "10/19 09:00 – 10/20 09:00"},
            {"name": "Outlook", "status": "ok", "message": "已讀取", "coverage": "10/19 09:00 – 10/20 09:00"},
            {"name": "行事曆", "status": "ok", "message": "已讀取", "coverage": ""},
        ],
        "new_chats": [],
        "notices": [],
        "today": [_todo_view()],
        "maybe_done": [],
        "collapsed": [],
        "events": [],
        "skipped": [],
        "coverage": {"batched": False, "batches": 1},
        "report_index": {"1": "T-0001"},
    }
    for k, v in overrides.items():
        defaults[k] = v
    return defaults


def _digest_view(**overrides: object) -> dict:
    """View dict with new C3 keys for the digest template."""
    v = _base_view()
    v.setdefault("chats", [
        _chat(
            name="金馬站營運群",
            todos=[_todo_view(no=1, title="回覆站長")],
            needs_reply=[{"who": "陳站長", "when": "10/19 15:42", "ask": "問你格式"}],
        ),
    ])
    v.setdefault("quiet_chats", [_quiet_chat()])
    v.setdefault("mail_folders", [_mail_folder(mails=[
        _mail(sender="黃志明", subject="Q4 預估表", todos=[_todo_view(no=2, title="回信")]),
    ])])
    v.setdefault("other_todos", [_todo_view(no=3, title="整理名單")])
    for k, val in overrides.items():
        v[k] = val
    return v


# ---------------------------------------------------------------------------
# Tag collector (reusable HTML parser)
# ---------------------------------------------------------------------------

class _TagCollector(HTMLParser):
    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, attrs))


# ---------------------------------------------------------------------------
# CSP hash integrity
# ---------------------------------------------------------------------------

class TestCspHashIntegrity:
    """Render → write via atomic_write_text → read back BYTES → recompute hash."""

    def test_script_hash_matches_csp(self, tmp_path: Path) -> None:
        v = _digest_view()
        html_str = render(v, "html")

        # Write via the real publish path
        out = tmp_path / "agenda.html"
        atomic_write_text(out, html_str)

        # Read back as raw bytes (catches Windows CRLF regression)
        raw = out.read_bytes()

        # Extract the script body from the bytes
        m = re.search(rb"<script>(.*?)</script>", raw, re.DOTALL)
        assert m is not None, "no <script> found in output"
        script_bytes = m.group(1)

        # Recompute hash
        actual_hash = base64.b64encode(
            hashlib.sha256(script_bytes).digest()
        ).decode("ascii")

        # Extract the hash from the CSP meta
        csp_m = re.search(
            rb"script-src 'sha256-([A-Za-z0-9+/=]+)'", raw
        )
        assert csp_m is not None, "no script-src sha256 hash in CSP meta"
        csp_hash = csp_m.group(1).decode("ascii")

        assert actual_hash == csp_hash, (
            f"script hash mismatch: computed {actual_hash!r}, CSP says {csp_hash!r}"
        )

    def test_hash_matches_module_constant(self) -> None:
        """The hash in the module matches a fresh computation."""
        fresh = base64.b64encode(
            hashlib.sha256(DIGEST_SCRIPT.encode("utf-8")).digest()
        ).decode("ascii")
        assert fresh == DIGEST_SCRIPT_HASH

    def test_script_constant_safe_to_embed(self) -> None:
        """The script constant must not contain sequences that break embedding."""
        assert "</script" not in DIGEST_SCRIPT.lower()
        assert "<!--" not in DIGEST_SCRIPT


# ---------------------------------------------------------------------------
# Exactly one <script> tag
# ---------------------------------------------------------------------------

class TestSingleScript:
    def test_exactly_one_script_tag(self) -> None:
        v = _digest_view()
        html = render(v, "html")
        count = html.lower().count("<script")
        assert count == 1, f"expected 1 <script, found {count}"

    def test_old_keys_view_also_one_script(self) -> None:
        """Even without new C3 keys, exactly one script is present."""
        v = _base_view()
        html = render(v, "html")
        count = html.lower().count("<script")
        assert count == 1, f"expected 1 <script, found {count}"


# ---------------------------------------------------------------------------
# Malicious strings in untrusted fields
# ---------------------------------------------------------------------------

_MALICIOUS_NAMES: list[str] = [
    '</script><script>alert(1)</script>',
    '" onerror="x',
    '{{ 7*191 }}',
    '<svg onload=alert(1)>',
    '<img src=x onerror=alert(1)>',
    'javascript:alert(1)',
    '"><iframe src="evil">',
]


class TestMaliciousStrings:
    @pytest.mark.parametrize("payload", _MALICIOUS_NAMES)
    def test_malicious_chat_name(self, payload: str) -> None:
        v = _digest_view(chats=[
            _chat(name=payload, bullets=[payload]),
        ])
        html = render(v, "html")
        assert html.lower().count("<script") == 1
        assert "<svg onload" not in html.lower()
        # {{ 7*191 }} must not evaluate to 1337
        if "7*191" in payload:
            assert "1337" not in html

    @pytest.mark.parametrize("payload", _MALICIOUS_NAMES)
    def test_malicious_mail_sender(self, payload: str) -> None:
        v = _digest_view(mail_folders=[_mail_folder(mails=[
            _mail(sender=payload, subject=payload, summary=payload),
        ])])
        html = render(v, "html")
        assert html.lower().count("<script") == 1
        assert "<svg onload" not in html.lower()

    @pytest.mark.parametrize("payload", _MALICIOUS_NAMES)
    def test_malicious_quiet_chat(self, payload: str) -> None:
        v = _digest_view(quiet_chats=[_quiet_chat(name=payload, summary=payload)])
        html = render(v, "html")
        assert html.lower().count("<script") == 1
        assert "<svg onload" not in html.lower()

    @pytest.mark.parametrize("payload", _MALICIOUS_NAMES)
    def test_malicious_needs_reply(self, payload: str) -> None:
        v = _digest_view(chats=[_chat(
            needs_reply=[{"who": payload, "when": "10/19", "ask": payload}],
        )])
        html = render(v, "html")
        assert html.lower().count("<script") == 1

    @pytest.mark.parametrize("payload", _MALICIOUS_NAMES)
    def test_malicious_todo_title(self, payload: str) -> None:
        v = _digest_view(other_todos=[_todo_view(title=payload)])
        html = render(v, "html")
        assert html.lower().count("<script") == 1

    def test_no_event_handlers_in_output(self) -> None:
        """No attribute name starting with 'on' in any tag."""
        payloads = [
            '</script><script>alert(1)</script>',
            '<svg onload=alert(1)>',
        ]
        for payload in payloads:
            v = _digest_view(chats=[_chat(name=payload)])
            html = render(v, "html")
            parser = _TagCollector()
            parser.feed(html)
            for tag, attrs in parser.tags:
                for attr_name, _ in attrs:
                    assert not attr_name.startswith("on"), \
                        f"event handler attribute {attr_name!r} on <{tag}>"

    def test_ssti_not_evaluated(self) -> None:
        """Jinja template injection: {{ 7*191 }} must not produce 1337."""
        v = _digest_view(chats=[_chat(name="{{ 7*191 }}", bullets=["{{ 7*191 }}"])])
        html = render(v, "html")
        assert "1337" not in html


# ---------------------------------------------------------------------------
# Invalid report code
# ---------------------------------------------------------------------------

class TestReportCode:
    def test_valid_code_emitted(self) -> None:
        v = _digest_view(report_code="R-7K2")
        html = render(v, "html")
        assert 'data-report="R-7K2"' in html

    def test_invalid_code_not_in_data_attr(self) -> None:
        v = _digest_view(report_code="R-<x>")
        html = render(v, "html")
        # The data-report attribute must not be emitted at all for invalid codes.
        assert "data-report=" not in html

    def test_empty_code_not_emitted(self) -> None:
        v = _digest_view(report_code="")
        html = render(v, "html")
        # Either no data-report attr, or it is empty
        if "data-report" in html:
            assert 'data-report=""' in html


# ---------------------------------------------------------------------------
# Backward compatibility — old-keys-only view renders without error
# ---------------------------------------------------------------------------

class TestBackwardCompat:
    def test_old_keys_only(self) -> None:
        """View without new C3 keys (chats, quiet_chats, mail_folders,
        other_todos) still renders a complete page."""
        v = _base_view()
        # Explicitly ensure no new keys and clear maybe_done for full legacy
        for k in ("chats", "quiet_chats", "mail_folders", "other_todos"):
            v.pop(k, None)
        v["maybe_done"] = []
        html = render(v, "html")
        assert "<!DOCTYPE html>" in html
        assert "每日彙整" in html
        assert html.lower().count("<script") == 1
        # Legacy today table renders
        assert "今天要處理" in html


# ---------------------------------------------------------------------------
# Empty states
# ---------------------------------------------------------------------------

class TestEmptyStates:
    def test_no_chats(self) -> None:
        v = _digest_view(chats=[], quiet_chats=[])
        html = render(v, "html")
        # Should have a sentence about no LINE messages
        assert "LINE" in html

    def test_no_mails(self) -> None:
        v = _digest_view(mail_folders=[])
        html = render(v, "html")
        # No crash; page still valid
        assert "<!DOCTYPE html>" in html

    def test_no_other_todos(self) -> None:
        v = _digest_view(other_todos=[])
        html = render(v, "html")
        assert "<!DOCTYPE html>" in html


# ---------------------------------------------------------------------------
# Events (calendar) section
# ---------------------------------------------------------------------------

def _event(
    subject: str = "Team meeting",
    location: str = "Room A",
    start: str = "10/20 14:00",
    end: str = "10/20 15:00",
    href: str | None = "https://outlook.office.com/calendar/item/123",
) -> dict:
    d: dict = {
        "start": start,
        "end": end,
        "subject": subject,
        "location": location,
    }
    if href is not None:
        d["href"] = href
    else:
        d["href"] = None
    return d


class TestEventsSection:
    def test_events_render(self) -> None:
        v = _digest_view(events=[
            _event(subject="週五營運會議", location="會議室 A"),
            _event(subject="午餐", start="10/21 12:00", end="10/21 13:00", location=""),
        ])
        html = render(v, "html")
        assert "近三天行程" in html
        assert "週五營運會議" in html
        assert "會議室 A" in html
        assert "午餐" in html
        # Rail nav link
        assert "行程" in html
        assert "2 筆" in html

    def test_events_empty_omitted(self) -> None:
        v = _digest_view(events=[])
        html = render(v, "html")
        assert "近三天行程" not in html
        # No nav link for events when empty
        nav_section = html.split("</nav>")[0] if "</nav>" in html else ""
        assert "行程" not in nav_section

    def test_events_link_with_href(self) -> None:
        v = _digest_view(events=[_event(href="https://example.com/cal")])
        html = render(v, "html")
        assert 'rel="noopener noreferrer"' in html
        assert "https://example.com/cal" in html

    def test_events_no_link_without_href(self) -> None:
        v = _digest_view(events=[_event(href=None)])
        html = render(v, "html")
        assert "Team meeting" in html
        # Subject should not be wrapped in <a>
        assert 'rel="noopener' not in html

    @pytest.mark.parametrize("payload", _MALICIOUS_NAMES)
    def test_malicious_event_subject(self, payload: str) -> None:
        v = _digest_view(events=[_event(subject=payload, location=payload)])
        html = render(v, "html")
        assert html.lower().count("<script") == 1
        assert "<svg onload" not in html.lower()

    def test_events_grouped_by_day(self) -> None:
        v = _digest_view(events=[
            _event(start="10/20 09:00", end="10/20 10:00", subject="A"),
            _event(start="10/20 14:00", end="10/20 15:00", subject="B"),
            _event(start="10/21 10:00", end="10/21 11:00", subject="C"),
        ])
        html = render(v, "html")
        # Two day headings
        assert "10/20" in html
        assert "10/21" in html


# ---------------------------------------------------------------------------
# Numeric attribute safety — pulse dots
# ---------------------------------------------------------------------------

class TestPulseDotSafety:
    def test_float_x_emitted_numerically(self) -> None:
        v = _digest_view(chats=[_chat(pulse={
            "dots": [{"x": 123.5, "reply": False}],
            "from": "10/19",
            "to": "10/20",
        })])
        html = render(v, "html")
        assert 'cx="123.5"' in html

    def test_string_payload_in_x_not_emitted_raw(self) -> None:
        """If x is somehow a string payload, it must be coerced to float."""
        v = _digest_view(chats=[_chat(pulse={
            "dots": [{"x": "alert(1)", "reply": False}],
            "from": "10/19",
            "to": "10/20",
        })])
        html = render(v, "html")
        assert "alert(1)" not in html.split("<svg")[1].split("</svg>")[0] if "<svg" in html else True


# ---------------------------------------------------------------------------
# data-no / data-pri attribute safety
# ---------------------------------------------------------------------------

class TestDataAttributes:
    def test_data_no_is_integer(self) -> None:
        v = _digest_view(other_todos=[_todo_view(no=42)])
        html = render(v, "html")
        assert 'data-no="42"' in html

    def test_data_pri_is_whitelisted(self) -> None:
        v = _digest_view(other_todos=[_todo_view(priority="P0")])
        html = render(v, "html")
        assert 'data-pri="P0"' in html

    def test_bad_pri_falls_to_default(self) -> None:
        """A priority not in P0/P1/P2 falls back to P2."""
        v = _digest_view(other_todos=[_todo_view(priority='"><script>alert(1)')])
        html = render(v, "html")
        assert 'data-pri="P2"' in html
        assert html.lower().count("<script") == 1


# ---------------------------------------------------------------------------
# Template structural checks
# ---------------------------------------------------------------------------

class TestTemplateStructure:
    def test_csp_meta_present(self) -> None:
        v = _digest_view()
        html = render(v, "html")
        assert "Content-Security-Policy" in html
        assert "script-src 'sha256-" in html
        assert "default-src 'none'" in html
        assert "style-src 'unsafe-inline'" in html

    def test_no_safe_filter_in_template(self) -> None:
        """Template must not use |safe (Markup wrapper is used in Python)."""
        tpl = Path(__file__).resolve().parents[2] / "templates" / "agenda.html.j2"
        content = tpl.read_text(encoding="utf-8")
        assert "|safe" not in content

    def test_dark_mode_media_query(self) -> None:
        v = _digest_view()
        html = render(v, "html")
        assert "prefers-color-scheme: dark" in html

    def test_max_width_1560(self) -> None:
        """Outer shell max-width should be approximately 1560px."""
        v = _digest_view()
        html = render(v, "html")
        # Look for the max-width somewhere around 1560
        assert re.search(r"max-width:\s*15[456]0px", html)

    def test_confirmation_dock_present(self) -> None:
        v = _digest_view()
        html = render(v, "html")
        assert 'class="dock"' in html
        assert "產生確認文字" in html
