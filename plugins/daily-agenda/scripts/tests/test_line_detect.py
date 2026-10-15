"""Tests for line_detect.py — database auto-detection."""

from __future__ import annotations

from pathlib import Path
from typing import Any

import pytest

from daily_agenda.line_detect import detect_main_database


def _make_probe(results: dict[str, dict[str, Any]]):
    """Return a probe callable that maps filenames to results."""
    def probe(db_filename: str) -> dict[str, Any]:
        return results.get(db_filename, {"ok": False, "code": "LOCAL_READER_FAILED"})
    return probe


class TestDetectMainDatabase:
    def test_no_edb_files(self, tmp_path: Path) -> None:
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        result = detect_main_database(db_dir=db_dir, probe=_make_probe({}))
        assert result["status"] == "failed"
        assert result["code"] == "MAIN_DATABASE_NOT_FOUND"

    def test_dir_not_exists(self, tmp_path: Path) -> None:
        result = detect_main_database(
            db_dir=tmp_path / "nonexistent",
            probe=_make_probe({}),
        )
        assert result["status"] == "failed"
        assert result["code"] == "MAIN_DATABASE_NOT_FOUND"

    def test_single_success(self, tmp_path: Path) -> None:
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        (db_dir / "abc123.edb").write_bytes(b"data")
        result = detect_main_database(
            db_dir=db_dir,
            probe=_make_probe({"abc123.edb": {"ok": True, "code": None, "ms": 50}}),
        )
        assert result["status"] == "ok"
        assert result["database"] == "abc123.edb"

    def test_all_fail(self, tmp_path: Path) -> None:
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        (db_dir / "a.edb").write_bytes(b"data")
        (db_dir / "b.edb").write_bytes(b"data")
        result = detect_main_database(
            db_dir=db_dir,
            probe=_make_probe({
                "a.edb": {"ok": False, "code": "SESSION_KEY_UNAVAILABLE"},
                "b.edb": {"ok": False, "code": "SESSION_KEY_UNAVAILABLE"},
            }),
        )
        assert result["status"] == "failed"
        # When all candidates share the same error code, it becomes top-level
        assert result["code"] == "SESSION_KEY_UNAVAILABLE"
        assert len(result["candidates"]) == 2

    def test_multiple_success(self, tmp_path: Path) -> None:
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        (db_dir / "x.edb").write_bytes(b"data")
        (db_dir / "y.edb").write_bytes(b"data")
        result = detect_main_database(
            db_dir=db_dir,
            probe=_make_probe({
                "x.edb": {"ok": True, "code": None, "ms": 10},
                "y.edb": {"ok": True, "code": None, "ms": 20},
            }),
        )
        assert result["status"] == "multiple"
        assert result["code"] == "MULTIPLE_DATABASES_OK"
        assert len(result["candidates"]) == 2

    def test_excludes_album_chatstats_keep(self, tmp_path: Path) -> None:
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        (db_dir / "albumXYZ.edb").write_bytes(b"data")
        (db_dir / "chatStatsABC.edb").write_bytes(b"data")
        (db_dir / "keepDEF.edb").write_bytes(b"data")
        (db_dir / "main123.edb").write_bytes(b"data")
        result = detect_main_database(
            db_dir=db_dir,
            probe=_make_probe({
                "main123.edb": {"ok": True, "code": None, "ms": 10},
            }),
        )
        assert result["status"] == "ok"
        assert result["database"] == "main123.edb"

    def test_candidates_include_mtime(self, tmp_path: Path) -> None:
        db_dir = tmp_path / "db"
        db_dir.mkdir()
        (db_dir / "a.edb").write_bytes(b"data")
        (db_dir / "b.edb").write_bytes(b"data")
        result = detect_main_database(
            db_dir=db_dir,
            probe=_make_probe({
                "a.edb": {"ok": True, "code": None, "ms": 10},
                "b.edb": {"ok": True, "code": None, "ms": 20},
            }),
        )
        for c in result["candidates"]:
            assert "mtime" in c
            assert c["mtime"]  # non-empty
