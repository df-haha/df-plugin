"""Tests for trust probe with integrated main-database detection."""

from __future__ import annotations

import json
from datetime import datetime
from pathlib import Path
from typing import Any
from zoneinfo import ZoneInfo

import pytest

from daily_agenda import state
from daily_agenda.cli import main
from daily_agenda.cli._common import Deps

TAIPEI = ZoneInfo("Asia/Taipei")
NOW = datetime(2026, 10, 14, 9, 0, 0, tzinfo=TAIPEI)


def _fake_exe_info(**overrides: Any) -> dict[str, Any]:
    base = {
        "path": "C:\\LINE\\bin\\LINE.exe",
        "version": "26.5.0.3975",
        "bytes": 12345678,
        "sha256": "a" * 64,
        "signature_status": "Valid",
        "signer_org": "LY Corporation",
        "signer_subject": "O=LY Corporation",
        "thumbprint": "AABB" * 10,
    }
    base.update(overrides)
    return base


class _FakeLineForDetect:
    """Fake LINE source for detection probing."""

    def __init__(self, results: dict[str, dict[str, Any]]) -> None:
        self._results = results  # db_filename -> probe result
        self.probe_calls: list[str] = []

    def probe(self) -> dict[str, Any]:
        # This is called after construction with a specific main_database
        db = self._current_db
        self.probe_calls.append(db)
        return self._results.get(db, {"ok": False, "code": "LOCAL_READER_FAILED"})


def _line_factory(results: dict[str, dict[str, Any]]) -> Any:
    """Return a factory that creates FakeLineForDetect instances."""
    sources: list[_FakeLineForDetect] = []

    def factory(*, main_database: str, trusted_builds_path: str) -> _FakeLineForDetect:
        src = _FakeLineForDetect(results)
        src._current_db = main_database
        sources.append(src)
        return src

    factory._sources = sources
    return factory


def _deps(tmp_path: Path, *, exe_info: dict[str, Any] | None = None,
          line_results: dict[str, dict[str, Any]] | None = None,
          db_files: list[str] | None = None) -> Deps:
    info = exe_info or _fake_exe_info()
    db_dir = tmp_path / "line-db"
    db_dir.mkdir(exist_ok=True)
    for name in (db_files or []):
        (db_dir / name).write_bytes(b"fake-edb-content")

    factory = _line_factory(line_results or {})

    return Deps(
        home=tmp_path,
        now_factory=lambda: NOW,
        plugin_root=Path(__file__).resolve().parents[2],
        exe_info_reader=lambda _p: info,
        line_source_factory=factory,
        db_dir=db_dir,
    )


def _run(argv: list[str], deps: Deps, capsys: Any) -> tuple[int, dict]:
    code = main(argv, deps=deps)
    captured = capsys.readouterr()
    obj = json.loads(captured.out.strip().split("\n")[-1])
    return code, obj


class TestTrustProbe:
    def test_consent_required(self, tmp_path: Path, capsys: Any) -> None:
        deps = _deps(tmp_path)
        code, obj = _run(["trust", "probe"], deps, capsys)
        assert code == 1
        assert obj["code"] == "CONSENT_REQUIRED"

    def test_signature_invalid_shortcircuits(self, tmp_path: Path, capsys: Any) -> None:
        """Signature invalid → zero probe calls, zero detect calls."""
        state.accept_consent(tmp_path, NOW)
        deps = _deps(
            tmp_path,
            exe_info=_fake_exe_info(signature_status="NotSigned"),
            db_files=["current.edb"],
            line_results={"current.edb": {"ok": True, "code": None, "ms": 10}},
        )
        code, obj = _run(["trust", "probe"], deps, capsys)
        assert obj["code"] == "LINE_SIGNATURE_INVALID"
        # Factory should not have been called at all
        assert len(deps.line_source_factory._sources) == 0

    def test_single_db_ok(self, tmp_path: Path, capsys: Any) -> None:
        """Single DB → one probe call, config written, trust written."""
        state.accept_consent(tmp_path, NOW)
        deps = _deps(
            tmp_path,
            db_files=["current.edb"],
            line_results={"current.edb": {"ok": True, "code": None, "ms": 10}},
        )
        code, obj = _run(["trust", "probe"], deps, capsys)
        assert obj["ok"] is True
        assert obj["status"] == "trusted"

        # Config should have main_database set
        cfg = state.load_config(tmp_path)
        assert cfg["line"]["main_database"] == "current.edb"
        assert cfg["line"]["enabled"] is True

        # Trust file should exist
        assert (tmp_path / "trusted_builds.json").exists()

    def test_multi_db_one_ok(self, tmp_path: Path, capsys: Any) -> None:
        """Multi DB, one OK → auto-select, config written, trust written."""
        state.accept_consent(tmp_path, NOW)
        deps = _deps(
            tmp_path,
            db_files=["account1.edb", "account2.edb"],
            line_results={
                "account1.edb": {"ok": True, "code": None, "ms": 10},
                "account2.edb": {"ok": False, "code": "SESSION_KEY_UNAVAILABLE"},
            },
        )
        code, obj = _run(["trust", "probe"], deps, capsys)
        assert obj["ok"] is True
        assert obj["status"] == "trusted"

        cfg = state.load_config(tmp_path)
        assert cfg["line"]["main_database"] == "account1.edb"

    def test_multi_db_both_ok_returns_multiple(self, tmp_path: Path, capsys: Any) -> None:
        """Multi DB both OK → multiple, no trusted_builds.json, config unchanged."""
        state.accept_consent(tmp_path, NOW)
        deps = _deps(
            tmp_path,
            db_files=["account1.edb", "account2.edb"],
            line_results={
                "account1.edb": {"ok": True, "code": None, "ms": 10},
                "account2.edb": {"ok": True, "code": None, "ms": 10},
            },
        )
        code, obj = _run(["trust", "probe"], deps, capsys)
        assert obj["ok"] is False
        assert obj["code"] == "MULTIPLE_DATABASES_OK"
        assert len(obj["candidates"]) == 2
        assert all("name" in c for c in obj["candidates"])

        # No trust file written
        assert not (tmp_path / "trusted_builds.json").exists()

        # Config should NOT have main_database set
        cfg = state.load_config(tmp_path)
        assert not cfg.get("line", {}).get("main_database")

    def test_temp_trust_file_deleted_on_exception(self, tmp_path: Path, capsys: Any) -> None:
        """Temp trust file is always deleted even if probe raises."""
        state.accept_consent(tmp_path, NOW)

        def _raising_factory(*, main_database: str, trusted_builds_path: str) -> Any:
            class _Bad:
                def probe(self) -> dict:
                    raise RuntimeError("kaboom")
            return _Bad()

        deps = Deps(
            home=tmp_path,
            now_factory=lambda: NOW,
            plugin_root=Path(__file__).resolve().parents[2],
            exe_info_reader=lambda _p: _fake_exe_info(),
            line_source_factory=_raising_factory,
            db_dir=tmp_path / "line-db",
        )
        db_dir = tmp_path / "line-db"
        db_dir.mkdir(exist_ok=True)
        (db_dir / "test.edb").write_bytes(b"data")

        _run(["trust", "probe"], deps, capsys)

        # No temp trust files should remain in runs/
        runs_dir = tmp_path / "runs"
        if runs_dir.exists():
            trust_files = [f for f in runs_dir.iterdir() if f.name.startswith("trust-")]
            assert len(trust_files) == 0

    def test_no_trust_written_on_failed(self, tmp_path: Path, capsys: Any) -> None:
        """Failed detection → no trust file."""
        state.accept_consent(tmp_path, NOW)
        deps = _deps(
            tmp_path,
            db_files=["current.edb"],
            line_results={"current.edb": {"ok": False, "code": "SESSION_KEY_UNAVAILABLE"}},
        )
        code, obj = _run(["trust", "probe"], deps, capsys)
        assert obj["ok"] is False
        assert not (tmp_path / "trusted_builds.json").exists()

    def test_noninteractive_skips(self, tmp_path: Path, capsys: Any) -> None:
        """Noninteractive mode never writes trust."""
        state.accept_consent(tmp_path, NOW)
        deps = _deps(
            tmp_path,
            db_files=["current.edb"],
            line_results={"current.edb": {"ok": True, "code": None, "ms": 10}},
        )
        deps.noninteractive = True
        code, obj = _run(["trust", "probe"], deps, capsys)
        assert obj["code"] == "LINE_BUILD_UNVERIFIED"
        assert not (tmp_path / "trusted_builds.json").exists()

    def test_already_configured_skips_detect(self, tmp_path: Path, capsys: Any) -> None:
        """When main_database is already in config, skip detection entirely."""
        state.accept_consent(tmp_path, NOW)
        cfg = state.load_config(tmp_path)
        cfg.setdefault("line", {})["main_database"] = "existing.edb"
        cfg["line"]["enabled"] = True
        state.save_config(tmp_path, cfg)

        deps = _deps(
            tmp_path,
            db_files=["existing.edb"],
            line_results={"existing.edb": {"ok": True, "code": None, "ms": 10}},
        )
        code, obj = _run(["trust", "probe"], deps, capsys)
        assert obj["ok"] is True
        assert obj["status"] == "trusted"
