"""Fixes from the 2026-10-21 Windows verification report (0.0.2-dev).

1. df-graph server that dies on start → DF_GRAPH_START_FAILED (not NOT_REGISTERED), stderr tail kept.
2. `chats list --brief` → one fenced numbered listing, refs persisted for `exclude`.
3. `exclude` accepts listing numbers, `--keep` (inverse), multi-ref `--add`; `--set`/`--keep`
   keep exclusions the listing never showed (quiet chats must not silently become readable).
4. `profile show` without a profile → PROFILE_MISSING.
5. `outlook-preview` reports tz and date range for the events it shows.
"""

from __future__ import annotations

import json
import sys
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from daily_agenda import state
from daily_agenda.cli import main
from daily_agenda.cli._common import Deps
from daily_agenda.fence import RAW_SENTINEL
from daily_agenda.graph_client import GraphClientError, open_graph
from tests.conftest import FakeLine, make_line_chat

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 21, 9, 0, 0, tzinfo=TAIPEI)
REF_A = "chat:aaaaaaaaaaaaaaaaaaaaaaaa"
REF_B = "chat:bbbbbbbbbbbbbbbbbbbbbbbb"
REF_C = "chat:cccccccccccccccccccccccc"
REF_OLD = "chat:dddddddddddddddddddddddd"  # excluded earlier, silent for 30+ days


def _deps(tmp_path: Path, **overrides: Any) -> Deps:
    return Deps(home=tmp_path, now_factory=lambda: NOW,
                plugin_root=Path(__file__).resolve().parents[2], **overrides)


def _run(argv: list[str], deps: Deps, capsys: Any) -> tuple[int, dict]:
    code = main(argv, deps=deps)
    obj = json.loads(capsys.readouterr().out.strip().split("\n")[-1])
    return code, obj


def _consented_line(tmp_path: Path) -> Deps:
    state.accept_consent(tmp_path, NOW)
    cfg = state.load_config(tmp_path)
    cfg["line"]["enabled"] = True
    cfg["line"]["main_database"] = "test.edb"
    state.save_config(tmp_path, cfg)
    fake = FakeLine(chats=[
        make_line_chat(ref=REF_A, name="金馬站營運群"),
        make_line_chat(ref=REF_B, name="家人"),
        make_line_chat(ref=REF_C, name="廢鐵報價小組"),
    ])
    return _deps(tmp_path, line_source_factory=lambda **kw: fake)


# ---------------------------------------------------------------------------
# 1. server dies on start
# ---------------------------------------------------------------------------

class TestGraphStartFailure:
    def _cfg(self, snippet: str) -> dict[str, Any]:
        return {"type": "stdio", "command": sys.executable, "args": ["-c", snippet]}

    def test_crash_on_start_is_start_failed_with_stderr_tail(self) -> None:
        cfg = self._cfg("import sys; sys.stderr.write('ModuleNotFoundError: No module named mcp.server.fastmcp\\n'); sys.exit(1)")
        with pytest.raises(GraphClientError) as ei:
            with open_graph(server_config=cfg):
                pass
        assert ei.value.code == "DF_GRAPH_START_FAILED"
        assert "ModuleNotFoundError" in ei.value.detail

    def test_server_stderr_not_forwarded_to_our_stderr(self, capfd: Any) -> None:
        cfg = self._cfg("import sys; sys.stderr.write('IncompleteFieldDefinitionWarning noise\\n'); sys.exit(1)")
        with pytest.raises(GraphClientError):
            with open_graph(server_config=cfg):
                pass
        assert "IncompleteFieldDefinitionWarning" not in capfd.readouterr().err

    def test_probe_reports_start_failed(self, tmp_path: Path, capsys: Any) -> None:
        state.accept_consent(tmp_path, NOW)

        def _factory() -> Any:
            raise GraphClientError("DF_GRAPH_START_FAILED", "boom")

        deps = _deps(tmp_path, graph_factory=_factory)
        _, obj = _run(["probe"], deps, capsys)
        assert obj["sources"]["mail"]["code"] == "DF_GRAPH_START_FAILED"
        assert obj["sources"]["calendar"]["code"] == "DF_GRAPH_START_FAILED"

    def test_message_exists(self) -> None:
        from daily_agenda.messages import humanize
        assert "df-graph-setup" in humanize("DF_GRAPH_START_FAILED")


# ---------------------------------------------------------------------------
# 2. chats list --brief
# ---------------------------------------------------------------------------

class TestChatsListBrief:
    def test_brief_is_one_fenced_numbered_block(self, tmp_path: Path, capsys: Any) -> None:
        deps = _consented_line(tmp_path)
        _, obj = _run(["chats", "list", "--days", "30", "--brief"], deps, capsys)
        assert obj["ok"] is True
        assert obj["count"] == 3
        assert "chats" not in obj  # no per-chat objects / refs in brief mode
        listing = obj["listing"]
        assert listing.count("<<<UNTRUSTED-") == 1
        assert RAW_SENTINEL in listing  # still taints the session
        assert "1. 金馬站營運群" in listing and "3. 廢鐵報價小組" in listing
        assert REF_A not in listing

    def test_brief_persists_listing_for_exclude(self, tmp_path: Path, capsys: Any) -> None:
        deps = _consented_line(tmp_path)
        _run(["chats", "list", "--days", "30", "--brief"], deps, capsys)
        saved = json.loads((tmp_path / "chat-listing.json").read_text(encoding="utf-8"))
        assert [i["ref"] for i in saved["items"]] == [REF_A, REF_B, REF_C]
        assert saved["items"][0]["n"] == 1

    def test_brief_marks_already_excluded(self, tmp_path: Path, capsys: Any) -> None:
        deps = _consented_line(tmp_path)
        _run(["exclude", "--set", REF_B], deps, capsys)
        _, obj = _run(["chats", "list", "--days", "30", "--brief"], deps, capsys)
        assert "2. 家人" in obj["listing"]
        assert "[已排除]" in obj["listing"]

    def test_brief_is_much_smaller_than_full(self, tmp_path: Path, capsys: Any) -> None:
        state.accept_consent(tmp_path, NOW)
        cfg = state.load_config(tmp_path)
        cfg["line"]["enabled"] = True
        state.save_config(tmp_path, cfg)
        fake = FakeLine(chats=[make_line_chat(ref=f"chat:{i:024x}", name=f"群組{i}") for i in range(171)])
        deps = _deps(tmp_path, line_source_factory=lambda **kw: fake)
        main(["chats", "list", "--days", "30"], deps=deps)
        full = len(capsys.readouterr().out.encode("utf-8"))
        main(["chats", "list", "--days", "30", "--brief"], deps=deps)
        brief = len(capsys.readouterr().out.encode("utf-8"))
        assert brief * 4 < full


# ---------------------------------------------------------------------------
# 3. exclude: numbers / --keep / multi --add / preserve unseen exclusions
# ---------------------------------------------------------------------------

def _excluded(tmp_path: Path) -> list[str]:
    return [c["ref"] for c in state.load_config(tmp_path)["line"].get("excluded_chats", [])]


class TestExcludeWithListing:
    def test_set_by_numbers(self, tmp_path: Path, capsys: Any) -> None:
        deps = _consented_line(tmp_path)
        _run(["chats", "list", "--days", "30", "--brief"], deps, capsys)
        _, obj = _run(["exclude", "--set", "2", "3"], deps, capsys)
        assert obj["ok"] is True
        assert _excluded(tmp_path) == [REF_B, REF_C]
        names = [c["name"] for c in state.load_config(tmp_path)["line"]["excluded_chats"]]
        assert names == ["家人", "廢鐵報價小組"]

    def test_keep_is_inverse_of_listing(self, tmp_path: Path, capsys: Any) -> None:
        deps = _consented_line(tmp_path)
        _run(["chats", "list", "--days", "30", "--brief"], deps, capsys)
        _, obj = _run(["exclude", "--keep", "1"], deps, capsys)
        assert obj["ok"] is True
        assert _excluded(tmp_path) == [REF_B, REF_C]

    def test_set_preserves_exclusions_not_in_listing(self, tmp_path: Path, capsys: Any) -> None:
        deps = _consented_line(tmp_path)
        _run(["exclude", "--set", REF_OLD], deps, capsys)
        _run(["chats", "list", "--days", "30", "--brief"], deps, capsys)
        _, obj = _run(["exclude", "--set", "2"], deps, capsys)
        assert set(_excluded(tmp_path)) == {REF_OLD, REF_B}
        assert obj["kept_unlisted"] == 1

    def test_keep_preserves_exclusions_not_in_listing(self, tmp_path: Path, capsys: Any) -> None:
        deps = _consented_line(tmp_path)
        _run(["exclude", "--set", REF_OLD], deps, capsys)
        _run(["chats", "list", "--days", "30", "--brief"], deps, capsys)
        _run(["exclude", "--keep", "1", "2", "3"], deps, capsys)
        assert _excluded(tmp_path) == [REF_OLD]

    def test_set_in_listing_can_unexclude_listed_chat(self, tmp_path: Path, capsys: Any) -> None:
        deps = _consented_line(tmp_path)
        _run(["exclude", "--set", REF_B], deps, capsys)
        _run(["chats", "list", "--days", "30", "--brief"], deps, capsys)
        _run(["exclude", "--set", "3"], deps, capsys)
        assert _excluded(tmp_path) == [REF_C]

    def test_number_out_of_range_is_arg_error(self, tmp_path: Path, capsys: Any) -> None:
        deps = _consented_line(tmp_path)
        _run(["chats", "list", "--days", "30", "--brief"], deps, capsys)
        code, obj = _run(["exclude", "--set", "9"], deps, capsys)
        assert code == 2 and obj["code"] == "ARG_ERROR"
        assert _excluded(tmp_path) == []

    def test_number_without_listing_is_arg_error(self, tmp_path: Path, capsys: Any) -> None:
        deps = _consented_line(tmp_path)
        code, obj = _run(["exclude", "--set", "1"], deps, capsys)
        assert code == 2 and obj["code"] == "ARG_ERROR"

    def test_keep_without_listing_is_arg_error(self, tmp_path: Path, capsys: Any) -> None:
        deps = _consented_line(tmp_path)
        code, obj = _run(["exclude", "--keep", REF_A], deps, capsys)
        assert code == 2 and obj["code"] == "ARG_ERROR"

    def test_add_accepts_multiple_refs(self, tmp_path: Path, capsys: Any) -> None:
        deps = _consented_line(tmp_path)
        _, obj = _run(["exclude", "--add", REF_A, REF_B], deps, capsys)
        assert obj["excluded_count"] == 2
        assert _excluded(tmp_path) == [REF_A, REF_B]

    def test_plain_refs_still_work_without_listing(self, tmp_path: Path, capsys: Any) -> None:
        deps = _consented_line(tmp_path)
        _, obj = _run(["exclude", "--set", REF_A, REF_B], deps, capsys)
        assert obj["excluded_count"] == 2


# ---------------------------------------------------------------------------
# 4. profile show
# ---------------------------------------------------------------------------

class TestProfileMissing:
    def test_show_without_profile(self, tmp_path: Path, capsys: Any) -> None:
        state.accept_consent(tmp_path, NOW)
        _, obj = _run(["profile", "show"], _deps(tmp_path), capsys)
        assert obj["ok"] is False
        assert obj["code"] == "PROFILE_MISSING"
        assert "個人工作檔案" in obj["message"]


# ---------------------------------------------------------------------------
# 5. outlook-preview range
# ---------------------------------------------------------------------------

class FakeGraph:
    def mail_list(self, *, days: int, folder: str = "inbox") -> list[dict[str, Any]]:
        return [{"from": "a@x", "subject": "s"}]

    def calendar_list(self, *, days_ahead: int = 3, days_back: int = 1) -> list[dict[str, Any]]:
        self.calendar_args = (days_ahead, days_back)
        return [{"subject": "meet", "start": "2026-10-22T10:00:00", "end": "2026-10-22T11:00:00"}]


class TestOutlookPreviewRange:
    def test_preview_reports_tz_and_range(self, tmp_path: Path, capsys: Any) -> None:
        state.accept_consent(tmp_path, NOW)
        fake = FakeGraph()
        deps = _deps(tmp_path, graph_factory=lambda: fake)
        _, obj = _run(["outlook-preview"], deps, capsys)
        assert obj["ok"] is True
        assert obj["events_tz"] == "Asia/Taipei"
        assert obj["events_range"] == {"from": "2026-10-21", "to": "2026-10-22"}
        assert fake.calendar_args == (1, 0)
