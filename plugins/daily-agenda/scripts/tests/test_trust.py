"""Tests for trust.py (task 2.5, D-11): LINE version trust bootstrap.

Every Windows / network behaviour is injected; these tests run on Linux.
"""

from __future__ import annotations

import json
import os
import stat
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pytest

from daily_agenda import trust

NOW = datetime(2026, 10, 8, 10, 0, 0, tzinfo=timezone.utc)
FAKE_EXE = Path("C:/LINE/bin/current/LINE.exe")

VALID_EXE_INFO: dict[str, Any] = {
    "path": str(FAKE_EXE),
    "version": "26.5.0.3975",
    "bytes": 30628288,
    "sha256": "efdfc8fc3a5dd59afc9bb561897b8defac59fd212a6775d4400146ae5ccd7040",
    "signature_status": "Valid",
    "signer_subject": "CN=LY Corporation, O=LY Corporation, C=JP",
    "signer_org": "LY Corporation",
    "thumbprint": "3E85990B906DD895BB28206319546E2EEBA81336",
}


def _make_reader(info: dict[str, Any] | None = None):
    """Return a fake exe_info_reader that yields the given info.

    Asserts it is called with an exe path (not the home directory).
    """
    data = info if info is not None else dict(VALID_EXE_INFO)
    calls: list[Path] = []

    def reader(exe: Path) -> dict[str, Any]:
        calls.append(exe)
        # Verify: must be called with the exe path, never the home directory
        assert exe.name == "LINE.exe", f"exe_info_reader called with {exe}, expected an exe path"
        return dict(data)

    reader.calls = calls  # type: ignore[attr-defined]
    return reader


def _make_probe(ok: bool = True, code: str | None = None):
    """Return a fake probe callable."""
    calls: list[dict] = []

    def probe(env_overrides: dict) -> dict:
        calls.append(env_overrides)
        return {"ok": ok, "code": code}

    probe.calls = calls  # type: ignore[attr-defined]
    return probe


# ---------------------------------------------------------------------------
# exe path routing
# ---------------------------------------------------------------------------


class TestExePathRouting:
    def test_reader_receives_exe_not_home(self, tmp_path: Path) -> None:
        reader = _make_reader()
        trust.bootstrap_trust(
            tmp_path, exe_info_reader=reader,
            probe=_make_probe(), now=NOW, noninteractive=False,
            exe=FAKE_EXE,
        )
        assert len(reader.calls) == 1
        assert reader.calls[0] == FAKE_EXE


# ---------------------------------------------------------------------------
# Signature validation — fail-closed
# ---------------------------------------------------------------------------


class TestSignatureValidation:
    def test_invalid_signature_rejects_and_writes_nothing(self, tmp_path: Path) -> None:
        info = {**VALID_EXE_INFO, "signature_status": "NotSigned"}
        result = trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(info),
            probe=_make_probe(), now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert result["ok"] is False
        assert result["code"] == "LINE_SIGNATURE_INVALID"
        assert not (tmp_path / "trusted_builds.json").exists()

    def test_wrong_signer_rejects(self, tmp_path: Path) -> None:
        info = {**VALID_EXE_INFO, "signer_org": "Evil Corp"}
        result = trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(info),
            probe=_make_probe(), now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert result["ok"] is False
        assert result["code"] == "LINE_SIGNATURE_INVALID"
        assert not (tmp_path / "trusted_builds.json").exists()

    def test_missing_signer_org_rejects(self, tmp_path: Path) -> None:
        info = {**VALID_EXE_INFO, "signer_org": None}
        result = trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(info),
            probe=_make_probe(), now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert result["ok"] is False
        assert result["code"] == "LINE_SIGNATURE_INVALID"


# ---------------------------------------------------------------------------
# Already trusted — no re-write
# ---------------------------------------------------------------------------


class TestAlreadyTrusted:
    def test_already_trusted_returns_early(self, tmp_path: Path) -> None:
        existing = {"builds": [{"version": "26.5.0.3975", "bytes": 30628288,
                                "sha256": VALID_EXE_INFO["sha256"],
                                "signer": "LY Corporation",
                                "thumbprint": "3E85990B906DD895BB28206319546E2EEBA81336",
                                "trusted_at": "2026-10-07T10:00:00+00:00"}]}
        (tmp_path / "trusted_builds.json").write_text(
            json.dumps(existing), encoding="utf-8")
        probe = _make_probe()
        result = trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=probe, now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert result["ok"] is True
        assert result["status"] == "already_trusted"
        assert len(probe.calls) == 0  # probe never called


# ---------------------------------------------------------------------------
# Noninteractive mode (D-14 / AS-15)
# ---------------------------------------------------------------------------


class TestNoninteractive:
    def test_skips_and_writes_nothing(self, tmp_path: Path) -> None:
        probe = _make_probe()
        result = trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=probe, now=NOW, noninteractive=True, exe=FAKE_EXE,
        )
        assert result["ok"] is False
        assert result["status"] == "skipped_noninteractive"
        assert result["code"] == "LINE_BUILD_UNVERIFIED"
        assert result["message"]  # non-empty human-readable message
        assert not (tmp_path / "trusted_builds.json").exists()
        assert len(probe.calls) == 0


# ---------------------------------------------------------------------------
# Successful trust bootstrap
# ---------------------------------------------------------------------------


class TestSuccessfulTrust:
    def test_probe_success_writes_formal_list(self, tmp_path: Path) -> None:
        probe = _make_probe(ok=True)
        result = trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=probe, now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert result["ok"] is True
        assert result["status"] == "trusted"
        # Formal file written
        formal = trust.load_trusted(tmp_path)
        assert len(formal["builds"]) == 1
        entry = formal["builds"][0]
        assert entry["sha256"] == VALID_EXE_INFO["sha256"]
        assert entry["version"] == "26.5.0.3975"
        assert entry["bytes"] == 30628288
        assert entry["signer"] == "LY Corporation"
        assert entry["thumbprint"] == VALID_EXE_INFO["thumbprint"]
        assert "trusted_at" in entry

    def test_probe_receives_temp_file_path(self, tmp_path: Path) -> None:
        probe = _make_probe(ok=True)
        trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=probe, now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert len(probe.calls) == 1
        env = probe.calls[0]
        assert "LINE_MCP_TRUSTED_BUILDS" in env
        # The temp file must NOT exist after bootstrap (cleaned up)
        temp_path = Path(env["LINE_MCP_TRUSTED_BUILDS"])
        assert not temp_path.exists()

    def test_temp_file_content_is_single_build(self, tmp_path: Path) -> None:
        """The temp file given to probe contains only the candidate build."""
        captured_content: list[dict] = []

        def probe(env_overrides: dict) -> dict:
            p = Path(env_overrides["LINE_MCP_TRUSTED_BUILDS"])
            captured_content.append(json.loads(p.read_text(encoding="utf-8")))
            return {"ok": True, "code": None}

        trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=probe, now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert len(captured_content) == 1
        assert len(captured_content[0]["builds"]) == 1
        assert captured_content[0]["builds"][0]["sha256"] == VALID_EXE_INFO["sha256"]


# ---------------------------------------------------------------------------
# Probe failure — formal list untouched
# ---------------------------------------------------------------------------


class TestProbeFailure:
    def test_probe_returns_failure(self, tmp_path: Path) -> None:
        probe = _make_probe(ok=False, code="SESSION_KEY_UNAVAILABLE")
        result = trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=probe, now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert result["ok"] is False
        assert result["code"] == "SESSION_KEY_UNAVAILABLE"
        assert not (tmp_path / "trusted_builds.json").exists()

    def test_probe_raises_exception(self, tmp_path: Path) -> None:
        def exploding_probe(env_overrides: dict) -> dict:
            raise RuntimeError("boom")

        result = trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=exploding_probe, now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert result["ok"] is False
        assert result["code"] == "LINE_PROBE_FAILED"
        assert "RuntimeError" in result.get("detail", "")
        assert not (tmp_path / "trusted_builds.json").exists()

    def test_probe_exception_still_cleans_temp(self, tmp_path: Path) -> None:
        """Even if probe crashes, the temporary trust file must be deleted."""
        temp_files_seen: list[Path] = []

        def crashing_probe(env_overrides: dict) -> dict:
            p = Path(env_overrides["LINE_MCP_TRUSTED_BUILDS"])
            temp_files_seen.append(p)
            assert p.exists()  # it was written before probe was called
            raise ValueError("crash")

        trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=crashing_probe, now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert len(temp_files_seen) == 1
        assert not temp_files_seen[0].exists()


# ---------------------------------------------------------------------------
# Temp file cleanup — success, failure, exception
# ---------------------------------------------------------------------------


class TestTempFileCleanup:
    def _count_temp_files(self, h: Path) -> int:
        runs = h / "runs"
        if not runs.exists():
            return 0
        return len([f for f in runs.iterdir() if f.name.startswith("trust-probe-")])

    def test_cleaned_on_success(self, tmp_path: Path) -> None:
        trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=_make_probe(ok=True), now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert self._count_temp_files(tmp_path) == 0

    def test_cleaned_on_failure(self, tmp_path: Path) -> None:
        trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=_make_probe(ok=False, code="X"), now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert self._count_temp_files(tmp_path) == 0

    def test_cleaned_on_exception(self, tmp_path: Path) -> None:
        def boom(env: dict) -> dict:
            raise OSError("disk full")

        trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=boom, now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert self._count_temp_files(tmp_path) == 0


# ---------------------------------------------------------------------------
# Merging into existing list + dedup
# ---------------------------------------------------------------------------


class TestMergeAndDedup:
    def test_merges_with_existing_entries(self, tmp_path: Path) -> None:
        old_entry = {"version": "25.0.0", "bytes": 1000, "sha256": "a" * 64,
                     "signer": "LY Corporation", "thumbprint": "OLD",
                     "trusted_at": "2026-09-01T00:00:00+00:00"}
        (tmp_path / "trusted_builds.json").write_text(
            json.dumps({"builds": [old_entry]}), encoding="utf-8")

        trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=_make_probe(ok=True), now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        formal = trust.load_trusted(tmp_path)
        assert len(formal["builds"]) == 2
        hashes = {b["sha256"] for b in formal["builds"]}
        assert "a" * 64 in hashes
        assert VALID_EXE_INFO["sha256"] in hashes

    def test_dedup_by_sha256(self, tmp_path: Path) -> None:
        """If the same sha256 is already trusted, don't duplicate."""
        old_entry = {"version": "26.5.0.3975", "bytes": 30628288,
                     "sha256": VALID_EXE_INFO["sha256"],
                     "signer": "LY Corporation", "thumbprint": "3E85990B",
                     "trusted_at": "2026-09-01T00:00:00+00:00"}
        (tmp_path / "trusted_builds.json").write_text(
            json.dumps({"builds": [old_entry]}), encoding="utf-8")

        result = trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=_make_probe(ok=True), now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        # Should be already_trusted
        assert result["status"] == "already_trusted"


# ---------------------------------------------------------------------------
# Corrupt trusted_builds.json — fail-closed
# ---------------------------------------------------------------------------


class TestCorruptTrustedBuilds:
    def test_corrupt_json_raises(self, tmp_path: Path) -> None:
        (tmp_path / "trusted_builds.json").write_text("{invalid", encoding="utf-8")
        with pytest.raises(trust.TrustedBuildsCorrupt):
            trust.load_trusted(tmp_path)

    def test_missing_builds_key_raises(self, tmp_path: Path) -> None:
        (tmp_path / "trusted_builds.json").write_text("{}", encoding="utf-8")
        with pytest.raises(trust.TrustedBuildsCorrupt):
            trust.load_trusted(tmp_path)

    def test_malformed_entry_raises(self, tmp_path: Path) -> None:
        bad = {"builds": [{"version": "1", "bytes": "not_int", "sha256": "short"}]}
        (tmp_path / "trusted_builds.json").write_text(
            json.dumps(bad), encoding="utf-8")
        with pytest.raises(trust.TrustedBuildsCorrupt):
            trust.load_trusted(tmp_path)

    def test_bootstrap_with_corrupt_file_returns_error(self, tmp_path: Path) -> None:
        (tmp_path / "trusted_builds.json").write_text("GARBAGE", encoding="utf-8")
        result = trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=_make_probe(), now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert result["ok"] is False
        assert result["code"] == "TRUSTED_BUILDS_CORRUPT"

    def test_corrupt_detected_before_noninteractive_skip(self, tmp_path: Path) -> None:
        """Even in noninteractive mode, a corrupt file should be reported."""
        (tmp_path / "trusted_builds.json").write_text("BAD", encoding="utf-8")
        result = trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=_make_probe(), now=NOW, noninteractive=True, exe=FAKE_EXE,
        )
        assert result["ok"] is False
        assert result["code"] == "TRUSTED_BUILDS_CORRUPT"


# ---------------------------------------------------------------------------
# File permissions (POSIX only)
# ---------------------------------------------------------------------------


@pytest.mark.skipif(os.name == "nt", reason="POSIX permissions test")
class TestFilePermissions:
    def test_formal_file_is_0600(self, tmp_path: Path) -> None:
        trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=_make_probe(ok=True), now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        p = tmp_path / "trusted_builds.json"
        assert p.exists()
        mode = p.stat().st_mode & 0o777
        assert mode == 0o600


# ---------------------------------------------------------------------------
# load_trusted + trusted_builds_path
# ---------------------------------------------------------------------------


class TestLoadTrusted:
    def test_path_helper(self, tmp_path: Path) -> None:
        assert trust.trusted_builds_path(tmp_path) == tmp_path / "trusted_builds.json"

    def test_missing_file_raises(self, tmp_path: Path) -> None:
        with pytest.raises(FileNotFoundError):
            trust.load_trusted(tmp_path)

    def test_valid_file_loads(self, tmp_path: Path) -> None:
        data = {"builds": [{"version": "1.0", "bytes": 100, "sha256": "a" * 64,
                            "signer": "X", "thumbprint": "T",
                            "trusted_at": "2026-01-01T00:00:00+00:00"}]}
        (tmp_path / "trusted_builds.json").write_text(
            json.dumps(data), encoding="utf-8")
        loaded = trust.load_trusted(tmp_path)
        assert loaded == data


# ---------------------------------------------------------------------------
# humanize integration
# ---------------------------------------------------------------------------


class TestHumanize:
    def test_result_has_message(self, tmp_path: Path) -> None:
        info = {**VALID_EXE_INFO, "signature_status": "NotSigned"}
        result = trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(info),
            probe=_make_probe(), now=NOW, noninteractive=False, exe=FAKE_EXE,
        )
        assert result["message"]  # non-empty string

    def test_noninteractive_has_message(self, tmp_path: Path) -> None:
        result = trust.bootstrap_trust(
            tmp_path, exe_info_reader=_make_reader(),
            probe=_make_probe(), now=NOW, noninteractive=True, exe=FAKE_EXE,
        )
        assert result["message"]  # LINE_BUILD_UNVERIFIED has a human-readable message
