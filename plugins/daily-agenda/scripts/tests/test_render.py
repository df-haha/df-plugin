"""Tests for daily_agenda.render — view model builder + safe HTML/Markdown output."""

from __future__ import annotations

import re
from datetime import datetime, timedelta, timezone
from html.parser import HTMLParser

import pytest

from daily_agenda.render import _md_escape, _sanitize_href, build_view, render
from tests.fixtures.xss_strings import MALICIOUS_HREFS, SAFE_HREFS, XSS_PAYLOADS

TZ = timezone(timedelta(hours=8))
NOW = datetime(2026, 10, 8, 9, 0, 0, tzinfo=TZ)


# ---------------------------------------------------------------------------
# Helpers
# ---------------------------------------------------------------------------

def _bundle_meta(
    line_status: str = "ok",
    mail_status: str = "ok",
    cal_status: str = "ok",
) -> dict:
    return {
        "sources": {
            "line": {"status": line_status, "from": "2026-10-07T09:00:00+08:00",
                     "to": "2026-10-08T09:00:00+08:00"},
            "mail": {"status": mail_status, "from": "2026-10-07T09:00:00+08:00",
                     "to": "2026-10-08T09:00:00+08:00"},
            "calendar": {"status": cal_status},
        },
        "batch": {"index": 1, "total": 1},
    }


def _ledger_item(
    tid: str = "T-0001",
    title: str = "正常待辦",
    status: str = "open",
    priority: str = "P1",
    src_ids: list[str] | None = None,
    maybe_done_since: str | None = None,
    maybe_done_reason: str | None = None,
) -> dict:
    return {
        "id": tid,
        "title": title,
        "priority": priority,
        "due": "2026-10-10",
        "status": status,
        "src_ids": src_ids or ["mail:AAA"],
        "created_at": (NOW - timedelta(days=2)).isoformat(),
        "updated_at": NOW.isoformat(),
        "maybe_done_since": maybe_done_since,
        "maybe_done_reason": maybe_done_reason,
        "history": [],
    }


def _event(subject: str = "Team meeting", location: str = "Room A") -> dict:
    return {
        "src_id": "event:ev1",
        "start": "2026-10-08T14:00:00+08:00",
        "end": "2026-10-08T15:00:00+08:00",
        "web_link": "https://outlook.office.com/calendar/item/123",
        "untrusted": {"subject": subject, "location": location},
    }


def _make_view(**overrides) -> dict:
    defaults = dict(
        state={"report_code": "R-7K2", "report_index": {}},
        ledger=[_ledger_item()],
        bundle_meta=_bundle_meta(),
        events=[_event()],
        new_chat_names=["同事群組"],
        notices=[],
        now=NOW,
        report_code="R-7K2",
    )
    defaults.update(overrides)
    return build_view(**defaults)


# ---------------------------------------------------------------------------
# build_view
# ---------------------------------------------------------------------------

class TestBuildView:
    def test_basic_structure(self) -> None:
        v = _make_view()
        assert v["report_code"] == "R-7K2"
        assert v["date"] == "2026-10-08"
        assert len(v["sources"]) == 3
        assert len(v["today"]) == 1
        assert v["today"][0]["no"] == 1
        assert v["today"][0]["carry_days"] == 2
        assert len(v["events"]) == 1
        assert v["report_index"]["1"] == "T-0001"

    def test_maybe_done_and_collapsed(self) -> None:
        ledger = [
            _ledger_item(tid="T-0001", status="open"),
            _ledger_item(tid="T-0002", status="maybe_done",
                         maybe_done_reason="reply seen"),
            _ledger_item(tid="T-0003", status="collapsed",
                         maybe_done_reason="old"),
        ]
        v = _make_view(ledger=ledger)
        assert len(v["today"]) == 1
        assert len(v["maybe_done"]) == 1
        assert v["maybe_done"][0]["reason"] == "reply seen"
        assert len(v["collapsed"]) == 1
        # Numbering is sequential across sections.
        assert v["today"][0]["no"] == 1
        assert v["maybe_done"][0]["no"] == 2
        assert v["collapsed"][0]["no"] == 3

    def test_source_failed(self) -> None:
        meta = _bundle_meta(line_status="failed")
        meta["sources"]["line"]["code"] = "LINE_NOT_RUNNING"
        v = _make_view(bundle_meta=meta)
        line_src = v["sources"][0]
        assert line_src["status"] == "failed"
        assert "LINE 沒有在執行" in line_src["message"]

    def test_new_chats(self) -> None:
        v = _make_view(new_chat_names=["群組A", "群組B"])
        assert len(v["new_chats"]) == 2

    def test_batched(self) -> None:
        meta = _bundle_meta()
        meta["batch"]["total"] = 3
        v = _make_view(bundle_meta=meta)
        assert v["coverage"]["batched"] is True
        assert v["coverage"]["batches"] == 3


# ---------------------------------------------------------------------------
# href sanitisation
# ---------------------------------------------------------------------------

class TestSanitizeHref:
    @pytest.mark.parametrize("href", MALICIOUS_HREFS)
    def test_malicious_blocked(self, href: str) -> None:
        assert _sanitize_href(href) is None

    @pytest.mark.parametrize("href", SAFE_HREFS)
    def test_safe_allowed(self, href: str) -> None:
        assert _sanitize_href(href) == href

    def test_none(self) -> None:
        assert _sanitize_href(None) is None

    def test_non_string(self) -> None:
        assert _sanitize_href(42) is None


# ---------------------------------------------------------------------------
# Markdown escaping
# ---------------------------------------------------------------------------

class TestMdEscape:
    def test_special_chars(self) -> None:
        result = _md_escape("*bold* and `code` and [link](url)")
        assert "\\*" in result
        assert "\\`" in result
        assert "\\[" in result
        assert "\\(" in result

    def test_newline_collapsed(self) -> None:
        result = _md_escape("line1\nline2\ttab\rcarriage")
        assert "\n" not in result
        assert "\t" not in result
        assert "\r" not in result

    def test_none(self) -> None:
        assert _md_escape(None) == ""


# ---------------------------------------------------------------------------
# HTML render
# ---------------------------------------------------------------------------

class TestHtmlRender:
    def test_renders(self) -> None:
        v = _make_view()
        html = render(v, "html")
        assert "每日彙整" in html
        assert "R-7K2" in html
        assert "T-0001" in html

    def test_csp_meta(self) -> None:
        v = _make_view()
        html = render(v, "html")
        expected_csp = (
            '<meta http-equiv="Content-Security-Policy" '
            'content="default-src \'none\'; style-src \'unsafe-inline\'; '
            'img-src data:; base-uri \'none\'; form-action \'none\'">'
        )
        assert expected_csp in html

    def test_no_script_tag_in_template(self) -> None:
        v = _make_view()
        html = render(v, "html")
        # Template itself should not contain <script>.
        assert "<script" not in html.lower()

    def test_collapsed_uses_details(self) -> None:
        ledger = [_ledger_item(tid="T-0001", status="collapsed",
                               maybe_done_reason="old")]
        v = _make_view(ledger=ledger)
        html = render(v, "html")
        assert "<details>" in html
        assert "<summary>" in html

    def test_events_formatted_time(self) -> None:
        """Event start/end should be formatted as MM/DD HH:MM, not raw ISO."""
        v = _make_view()
        html = render(v, "html")
        assert "10/08 14:00" in html
        assert "10/08 15:00" in html


# ---------------------------------------------------------------------------
# Markdown render
# ---------------------------------------------------------------------------

class TestMdRender:
    def test_renders(self) -> None:
        v = _make_view()
        md = render(v, "md")
        assert "每日彙整" in md
        assert "R-7K2" in md

    def test_md_filter_applied(self) -> None:
        """Verify |md filter is used in the template by checking escaped output."""
        ledger = [_ledger_item(title="Task *bold* and [link]")]
        v = _make_view(ledger=ledger)
        md = render(v, "md")
        # The * and [ should be escaped.
        assert "\\*bold\\*" in md
        assert "\\[link\\]" in md


# ---------------------------------------------------------------------------
# XSS fixture: malicious strings in EVERY rendered field
# ---------------------------------------------------------------------------

class _TagCollector(HTMLParser):
    """Collect all tags and their attributes from HTML."""

    def __init__(self) -> None:
        super().__init__()
        self.tags: list[tuple[str, list[tuple[str, str | None]]]] = []

    def handle_starttag(self, tag: str, attrs: list[tuple[str, str | None]]) -> None:
        self.tags.append((tag, attrs))


_FORBIDDEN_TAGS = {"script", "img", "iframe", "object", "embed", "svg"}


class TestXssHtml:
    """XSS payloads placed in every untrusted field → HTML output is safe."""

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_xss_in_all_fields(self, payload: str) -> None:
        """Place payload in todo title, source label, event subject/location,
        new chat name, notice — assert HTML output is safe."""
        ledger = [
            _ledger_item(title=payload, src_ids=["mail:AAA"]),
            _ledger_item(tid="T-0002", status="maybe_done",
                         maybe_done_reason=payload,
                         src_ids=["mail:BBB"]),
            _ledger_item(tid="T-0003", status="collapsed",
                         maybe_done_reason=payload),
        ]
        src_index = {
            "mail:AAA": {"label": payload, "href": "https://safe.example"},
            "mail:BBB": {"label": payload, "href": payload},  # href is malicious
        }
        events = [_event(subject=payload, location=payload)]
        # Malicious href in event.
        events[0]["web_link"] = payload

        skipped = [{"src_ids": ["mail:AAA"], "reason": payload}]

        v = build_view(
            state={"report_code": "R-7K2", "report_index": {}},
            ledger=ledger,
            bundle_meta=_bundle_meta(),
            events=events,
            new_chat_names=[payload],
            notices=[payload],
            now=NOW,
            report_code="R-7K2",
            src_index=src_index,
            skipped=skipped,
        )
        html = render(v, "html")

        # Parse HTML and check tags.
        parser = _TagCollector()
        parser.feed(html)
        for tag, attrs in parser.tags:
            assert tag not in _FORBIDDEN_TAGS, f"forbidden tag <{tag}> found"
            for attr_name, attr_val in attrs:
                if attr_name == "href" and attr_val:
                    assert attr_val.startswith(("https://", "mailto:")), \
                        f"unsafe href: {attr_val}"
                if attr_name == "src":
                    # Only data: images allowed by CSP; shouldn't appear in output.
                    assert False, f"unexpected src attribute: {attr_val}"

        # No raw <script>, event handlers.
        assert "<script>" not in html
        assert "<link" not in html.split("</head>")[-1]  # no link tags in body
        assert "url(" not in html.split("</style>")[-1]  # no url() outside style
        assert "@import" not in html.split("</style>")[-1]

    def test_no_safe_filter_in_template(self) -> None:
        """Template must not use |safe filter."""
        template_path = (
            __import__("pathlib").Path(__file__).resolve().parents[2]
            / "templates" / "agenda.html.j2"
        )
        content = template_path.read_text(encoding="utf-8")
        assert "|safe" not in content
        assert "autoescape false" not in content.lower()


class TestXssMd:
    """XSS payloads in Markdown output — no active injection."""

    @pytest.mark.parametrize("payload", XSS_PAYLOADS)
    def test_xss_in_all_fields(self, payload: str) -> None:
        ledger = [
            _ledger_item(title=payload, src_ids=["mail:AAA"]),
            _ledger_item(tid="T-0002", status="maybe_done",
                         maybe_done_reason=payload),
        ]
        src_index = {
            "mail:AAA": {"label": payload, "href": payload},  # malicious href
        }
        events = [_event(subject=payload, location=payload)]
        events[0]["web_link"] = payload
        skipped = [{"src_ids": ["mail:AAA"], "reason": payload}]

        v = build_view(
            state={"report_code": "R-7K2", "report_index": {}},
            ledger=ledger,
            bundle_meta=_bundle_meta(),
            events=events,
            new_chat_names=[payload],
            notices=[payload],
            now=NOW,
            report_code="R-7K2",
            src_index=src_index,
            skipped=skipped,
        )
        md = render(v, "md")

        # No unescaped Markdown image injection.
        assert "![" not in md
        # No javascript: in link targets.
        assert "](javascript:" not in md.lower()
        # No raw HTML script tag.
        assert "<script>" not in md

    def test_md_template_uses_md_filter(self) -> None:
        """Every {{ }} in the md template should use |md or |md_href (or be trusted)."""
        template_path = (
            __import__("pathlib").Path(__file__).resolve().parents[2]
            / "templates" / "agenda.md.j2"
        )
        content = template_path.read_text(encoding="utf-8")
        # Find all {{ expr }} blocks.
        trusted_vars = {
            "v.report_code", "v.date", "v.generated_at",
            "v.coverage.batches", "item.no", "item.carry_days",
            "v.collapsed|length", "v.skipped|length",
        }
        pattern = re.compile(r"\{\{\s*(.+?)\s*\}\}")
        for m in pattern.finditer(content):
            expr = m.group(1).strip()
            if expr in trusted_vars:
                continue
            # Control flow / loop vars that are already filtered or safe.
            if "loop." in expr:
                continue
            # Must use |md or |md_href filter.
            assert "|md" in expr or "|md_href" in expr, \
                f"unfiltered expression in md template: {{{{ {expr} }}}}"


# ---------------------------------------------------------------------------
# Template contract assertions
# ---------------------------------------------------------------------------

class TestTemplateContract:
    def test_html_no_external_resources(self) -> None:
        v = _make_view()
        html = render(v, "html")
        body = html.split("</head>")[-1] if "</head>" in html else html
        assert "src=" not in body
        # No link tags loading external resources in body.
        assert "<link" not in body

    def test_html_no_script(self) -> None:
        v = _make_view()
        html = render(v, "html")
        assert "<script" not in html.lower()
