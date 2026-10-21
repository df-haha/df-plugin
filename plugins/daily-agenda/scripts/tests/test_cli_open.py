"""`DA open` opens the latest published report via the OS default app."""

from __future__ import annotations

import json
import os
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

from daily_agenda import state
from daily_agenda.cli import main
from daily_agenda.cli._common import Deps
from daily_agenda.fsutil import atomic_write_json

NOW = datetime(2026, 10, 21, 9, 0, tzinfo=ZoneInfo("Asia/Taipei"))


def _deps(tmp_path: Path) -> Deps:
    return Deps(home=tmp_path, now_factory=lambda: NOW, plugin_root=Path(__file__).resolve().parents[2])


def _run(argv: list[str], deps: Deps, capsys: Any) -> tuple[int, dict]:
    code = main(argv, deps=deps)
    return code, json.loads(capsys.readouterr().out.strip().split("\n")[-1])


def _publish_receipt(home: Path, *files: Path) -> None:
    gen_dir = state._gen_dir(home, 1)
    gen_dir.mkdir(parents=True, exist_ok=True)
    atomic_write_json(gen_dir / "published", {"at": NOW.isoformat(),
                                               "targets": [{"path": str(f), "sha256": "x"} for f in files]})
    state._write_current(home, 1)


def test_no_output_yet(tmp_path: Path, capsys: Any) -> None:
    code, obj = _run(["open"], _deps(tmp_path), capsys)
    assert code == 1 and obj["code"] == "NO_OUTPUT_YET"


def test_opens_latest_html_with_startfile(tmp_path: Path, capsys: Any, monkeypatch: Any) -> None:
    html = tmp_path / "out" / "agenda-2026-10-21.html"
    md = tmp_path / "out" / "agenda-2026-10-21.md"
    html.parent.mkdir()
    html.write_text("<html></html>", encoding="utf-8")
    md.write_text("# x", encoding="utf-8")
    _publish_receipt(tmp_path, md, html)
    opened: list[str] = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)

    code, obj = _run(["open"], _deps(tmp_path), capsys)
    assert code == 0 and obj["opened"] is True and obj["path"] == str(html)
    assert opened == [str(html)]

    code, obj = _run(["open", "--format", "md"], _deps(tmp_path), capsys)
    assert obj["path"] == str(md) and opened[-1] == str(md)


def test_without_startfile_reports_path_only(tmp_path: Path, capsys: Any, monkeypatch: Any) -> None:
    html = tmp_path / "agenda.html"
    html.write_text("<html></html>", encoding="utf-8")
    _publish_receipt(tmp_path, html)
    monkeypatch.delattr(os, "startfile", raising=False)
    code, obj = _run(["open"], _deps(tmp_path), capsys)
    assert code == 0 and obj["opened"] is False and obj["path"] == str(html)
    assert str(html) in obj["message"]


def test_missing_file(tmp_path: Path, capsys: Any) -> None:
    _publish_receipt(tmp_path, tmp_path / "gone.html")
    code, obj = _run(["open"], _deps(tmp_path), capsys)
    assert code == 1 and obj["code"] == "OUTPUT_MISSING"


def test_open_never_takes_an_arbitrary_path(tmp_path: Path, capsys: Any, monkeypatch: Any) -> None:
    """Only receipt-recorded files are opened; a path argument is ignored."""
    html = tmp_path / "agenda.html"
    html.write_text("<html></html>", encoding="utf-8")
    _publish_receipt(tmp_path, html)
    opened: list[str] = []
    monkeypatch.setattr(os, "startfile", lambda p: opened.append(p), raising=False)
    _run(["open", "C:/evil.exe"], _deps(tmp_path), capsys)
    assert opened == [str(html)]
