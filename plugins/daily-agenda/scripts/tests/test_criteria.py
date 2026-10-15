"""Tests for the criteria version management module."""

from __future__ import annotations

from pathlib import Path

import pytest

from daily_agenda import criteria


class TestCriteria:
    def test_empty_dir(self, tmp_path: Path) -> None:
        assert criteria.list_versions(tmp_path) == []
        assert criteria.latest_version(tmp_path) == 0
        assert criteria.read_latest(tmp_path) is None

    def test_save_and_read(self, tmp_path: Path) -> None:
        v = criteria.save(tmp_path, "# Criteria v1")
        assert v == 1
        assert criteria.read_version(tmp_path, 1) == "# Criteria v1"
        assert criteria.latest_version(tmp_path) == 1

    def test_multiple_versions(self, tmp_path: Path) -> None:
        criteria.save(tmp_path, "Version 1")
        criteria.save(tmp_path, "Version 2")
        criteria.save(tmp_path, "Version 3")
        assert criteria.list_versions(tmp_path) == [1, 2, 3]
        assert criteria.latest_version(tmp_path) == 3
        assert criteria.read_latest(tmp_path) == "Version 3"
        assert criteria.read_version(tmp_path, 1) == "Version 1"

    def test_restore(self, tmp_path: Path) -> None:
        criteria.save(tmp_path, "V1")
        criteria.save(tmp_path, "V2")
        new_v = criteria.restore(tmp_path, 1)
        assert new_v == 3
        assert criteria.read_version(tmp_path, 3) == "V1"

    def test_restore_nonexistent(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            criteria.restore(tmp_path, 99)

    def test_read_nonexistent_version(self, tmp_path: Path) -> None:
        assert criteria.read_version(tmp_path, 99) is None
