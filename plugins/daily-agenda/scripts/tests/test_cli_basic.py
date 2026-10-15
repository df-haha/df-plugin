"""CLI basic tests: dispatch, JSON shape, exit codes, consent gating."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from daily_agenda.cli import main
from daily_agenda.cli._common import Deps

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 8, 9, 0, 0, tzinfo=TAIPEI)


def _deps(tmp_path: Path, **overrides: Any) -> Deps:
    return Deps(
        home=tmp_path,
        now_factory=lambda: NOW,
        plugin_root=Path(__file__).resolve().parents[2],
        **overrides,
    )


def _run_cli(argv: list[str], deps: Deps, capsys: Any) -> tuple[int, dict]:
    """Run main(), capture stdout, parse JSON."""
    code = main(argv, deps=deps)
    captured = capsys.readouterr()
    obj = json.loads(captured.out.strip().split("\n")[-1])
    return code, obj


class TestDispatch:
    def test_no_command(self, tmp_path: Path, capsys: Any) -> None:
        code, obj = _run_cli([], _deps(tmp_path), capsys)
        assert code == 2
        assert obj["ok"] is False
        assert obj["code"] == "ARG_ERROR"

    def test_unknown_command(self, tmp_path: Path, capsys: Any) -> None:
        code, obj = _run_cli(["xyzzy"], _deps(tmp_path), capsys)
        assert code == 2
        assert obj["ok"] is False

    def test_json_shape(self, tmp_path: Path, capsys: Any) -> None:
        """Every output must have ok, code, message."""
        code, obj = _run_cli(["envcheck"], _deps(tmp_path), capsys)
        assert "ok" in obj
        assert "message" in obj


class TestStartupFailure:
    def test_production_deps_failure_still_emits_json(self, monkeypatch: Any, capsys: Any) -> None:
        # Real Windows run 2026-10-14: ZoneInfo("Asia/Taipei") raised before dispatch
        # and printed a traceback instead of one JSON object.
        def _boom() -> Deps:
            raise RuntimeError("No time zone found with key Asia/Taipei")

        monkeypatch.setattr(Deps, "production", staticmethod(_boom))
        rc = main(["envcheck"])
        out = json.loads(capsys.readouterr().out)
        assert rc == 1
        assert out["ok"] is False
        assert out["code"] == "INTERNAL_ERROR"
        assert "Asia/Taipei" not in json.dumps(out)


class TestConsentGating:
    """Commands that read LINE/mail/calendar must refuse without consent."""

    GATED_COMMANDS: list[list[str]] = [
        ["probe"],
        ["outlook-preview"],
        ["chats", "list", "--days", "30"],
        ["line-detect"],
        ["trust", "probe"],
        ["mail", "folders"],
        ["mail", "select", "--set", "inbox"],
        ["criteria", "draft", "--days", "7"],
        ["refine", "draft"],
        ["run"],
    ]

    @pytest.mark.parametrize("argv", GATED_COMMANDS, ids=lambda a: " ".join(a))
    def test_consent_required(self, tmp_path: Path, capsys: Any, argv: list[str]) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(argv, deps, capsys)
        assert code == 1
        assert obj["ok"] is False
        assert obj["code"] == "CONSENT_REQUIRED"


class TestConsentCommand:
    def test_accept_and_status(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["consent", "--accept"], deps, capsys)
        assert code == 0
        assert obj["ok"] is True
        assert "accepted_at" in obj

        code2, obj2 = _run_cli(["consent", "--status"], deps, capsys)
        assert obj2["accepted"] is True

    def test_revoke(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        _run_cli(["consent", "--accept"], deps, capsys)
        code, obj = _run_cli(["consent", "--revoke"], deps, capsys)
        assert obj["ok"] is True
        assert obj["revoked"] is True

        code2, obj2 = _run_cli(["consent", "--status"], deps, capsys)
        assert obj2["accepted"] is False


class TestEnvcheck:
    def test_linux_not_windows(self, tmp_path: Path, capsys: Any) -> None:
        code, obj = _run_cli(["envcheck"], _deps(tmp_path), capsys)
        # On Linux, should fail with NOT_WINDOWS
        assert obj["ok"] is False
        checks = obj["checks"]
        assert any(c["code"] == "NOT_WINDOWS" for c in checks)


class TestConfigCommand:
    def test_get(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["config", "get"], deps, capsys)
        assert obj["ok"] is True
        assert "config" in obj

    def test_set_and_get(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["config", "set", "output.formats=html,md"], deps, capsys)
        assert obj["ok"] is True

        code2, obj2 = _run_cli(["config", "get"], deps, capsys)
        assert obj2["config"]["output.formats"] == ["html", "md"]

    def test_invalid_key(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["config", "set", "bad.key=x"], deps, capsys)
        assert code == 2
        assert "unknown config key" in obj["detail"]

    def test_invalid_format(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["config", "set", "output.formats=pdf"], deps, capsys)
        assert code == 2


class TestExclude:
    def test_set_exclude(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["exclude", "--set", "chat:aaa", "chat:bbb"], deps, capsys)
        assert obj["ok"] is True
        assert obj["excluded_count"] == 2

    def test_add_remove(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        _run_cli(["exclude", "--set", "chat:aaa"], deps, capsys)
        _run_cli(["exclude", "--add", "chat:bbb"], deps, capsys)
        code, obj = _run_cli(["exclude", "--remove", "chat:aaa"], deps, capsys)
        assert obj["excluded_count"] == 1


class TestCtxIndex:
    def test_skip(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["ctx", "index", "--skip"], deps, capsys)
        assert obj["ok"] is True
        assert obj["files"] == 0

    def test_dir(self, tmp_path: Path, capsys: Any) -> None:
        ctx_dir = tmp_path / "ctx"
        ctx_dir.mkdir()
        (ctx_dir / "note.md").write_text("# Hello\nWorld", encoding="utf-8")

        deps = _deps(tmp_path)
        code, obj = _run_cli(["ctx", "index", "--dir", str(ctx_dir)], deps, capsys)
        assert obj["ok"] is True
        assert obj["files"] == 1


class TestCriteria:
    def test_list_empty(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["criteria", "list"], deps, capsys)
        assert obj["ok"] is True
        assert obj["versions"] == []

    def test_save_and_show(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        # criteria show requires consent (★ command)
        from daily_agenda import state as _st
        _st.accept_consent(tmp_path, NOW)

        draft = tmp_path / "draft.md"
        draft.write_text("# My criteria\nTest content", encoding="utf-8")

        code, obj = _run_cli(["criteria", "save", "--file", str(draft)], deps, capsys)
        assert obj["ok"] is True
        assert obj["version"] == 1

        code2, obj2 = _run_cli(["criteria", "show"], deps, capsys)
        assert obj2["ok"] is True
        assert obj2["version"] == 1
        assert "My criteria" in obj2["text"]

    def test_restore(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        d1 = tmp_path / "d1.md"
        d1.write_text("Version 1", encoding="utf-8")
        d2 = tmp_path / "d2.md"
        d2.write_text("Version 2", encoding="utf-8")

        _run_cli(["criteria", "save", "--file", str(d1)], deps, capsys)
        _run_cli(["criteria", "save", "--file", str(d2)], deps, capsys)

        code, obj = _run_cli(["criteria", "restore", "--version", "1"], deps, capsys)
        assert obj["ok"] is True
        assert obj["version"] == 3
        assert obj["restored_from"] == 1


class TestLineSelect:
    def test_valid(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["line-select", "--database", "abc123.edb"], deps, capsys)
        assert obj["ok"] is True
        assert obj["database"] == "abc123.edb"

    def test_no_edb(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["line-select", "--database", "abc123.txt"], deps, capsys)
        assert code == 2

    def test_path_separator(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run_cli(["line-select", "--database", "../abc.edb"], deps, capsys)
        assert code == 2


class TestNoRawContent:
    """Non-★ commands must not leak untrusted content to stdout."""

    SENTINEL = "RAW_SENTINEL_xq9"

    def _make_fake_line(self) -> Any:
        from tests.conftest import FakeLine, make_line_chat, make_line_message
        return FakeLine(
            chats=[make_line_chat(name=self.SENTINEL)],
            messages=[make_line_message(text=self.SENTINEL, sender=self.SENTINEL)],
        )

    def test_probe_no_raw(self, tmp_path: Path, capsys: Any) -> None:
        from daily_agenda import state
        state.accept_consent(tmp_path, NOW)
        cfg = state.load_config(tmp_path)
        cfg["line"]["enabled"] = True
        cfg["line"]["main_database"] = "test.edb"
        state.save_config(tmp_path, cfg)

        fake_line = self._make_fake_line()
        deps = _deps(tmp_path, line_source_factory=lambda **kw: fake_line)
        code, obj = _run_cli(["probe"], deps, capsys)
        full_output = capsys.readouterr().out + json.dumps(obj, ensure_ascii=False)
        assert self.SENTINEL not in full_output
