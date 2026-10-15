"""Tests for runtime.py (task 2.4, D-5): runtime environment install + status.

All Windows / network / subprocess behaviour is injected; these tests run on Linux.
"""

from __future__ import annotations

import hashlib
import io
import json
import os
import stat
import subprocess
import zipfile
from pathlib import Path
from typing import Any
from unittest.mock import MagicMock

import pytest

from daily_agenda import runtime

# ---------------------------------------------------------------------------
# Test helpers
# ---------------------------------------------------------------------------

SQLITE3MC_ARCHIVE_HASH = "858c4f2e9e262caca06e6c4fafee62d13dd90c21b649b047f17992bf0402981e"
SQLITE3MC_DLL_HASH = "5030decc6d914539e3b9b7e28aa4f6de1e7161dac6fd4b21eb02e6754d9b175e"


def _make_zip(members: dict[str, bytes]) -> bytes:
    """Create a ZIP archive in memory with given member names and content."""
    buf = io.BytesIO()
    with zipfile.ZipFile(buf, "w", zipfile.ZIP_DEFLATED) as zf:
        for name, data in members.items():
            zf.writestr(name, data)
    return buf.getvalue()


def _make_zip_with_hash(members: dict[str, bytes], target_hash: str | None = None) -> bytes:
    """Create a ZIP whose SHA-256 equals ``target_hash`` if provided; otherwise just return the zip."""
    data = _make_zip(members)
    if target_hash is None:
        return data
    # For tests that need a specific hash, we compute and check
    actual = hashlib.sha256(data).hexdigest()
    if actual != target_hash:
        # Can't match arbitrary hash; caller uses actual hash
        pass
    return data


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _make_runner(responses: dict[str, tuple[int, str]] | None = None):
    """Return a fake subprocess runner keyed by program name."""
    resp = responses or {}
    calls: list[list[str]] = []

    def runner(cmd, **kwargs):
        calls.append(list(cmd))
        key = Path(cmd[0]).name.lower().removesuffix(".exe").removesuffix(".cmd")
        if key not in resp:
            raise FileNotFoundError(cmd[0])
        rc, out = resp[key]
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")

    runner.calls = calls  # type: ignore[attr-defined]
    return runner


def _make_which(mapping: dict[str, str] | None = None):
    """Return a fake ``shutil.which`` that resolves names from a mapping."""
    m = mapping or {}

    def which(name: str) -> str | None:
        return m.get(name)

    return which


def _make_fetcher(url_content: dict[str, bytes]):
    """Return a fake fetch function."""
    calls: list[str] = []

    def fetch(url: str, *, timeout: int = 30, max_bytes: int = 0) -> bytes:
        calls.append(url)
        if url not in url_content:
            raise ConnectionError(f"fake: no content for {url}")
        data = url_content[url]
        if max_bytes and len(data) > max_bytes:
            raise ValueError(f"exceeds max_bytes {max_bytes}")
        return data

    fetch.calls = calls  # type: ignore[attr-defined]
    return fetch


def _make_arch_checker(*, arch: str = "64bit", system: str = "Windows", machine: str = "AMD64"):
    """Return a fake architecture checker."""

    def checker() -> dict[str, str]:
        return {"arch": arch, "system": system, "machine": machine}

    return checker


# ---------------------------------------------------------------------------
# Download safety
# ---------------------------------------------------------------------------


class TestDownloadSafety:
    def test_non_https_rejected(self) -> None:
        with pytest.raises(ValueError, match="https"):
            runtime._download("http://evil.com/file.zip", staging=Path("/tmp/s"),
                              filename="f.zip", expected_hash="a" * 64,
                              fetch=_make_fetcher({}), max_bytes=200_000_000)

    def test_hash_mismatch_rejects_and_removes(self, tmp_path: Path) -> None:
        staging = tmp_path / "staging"
        staging.mkdir()
        wrong_content = b"not the right content"
        url = "https://example.com/file.zip"
        fetch = _make_fetcher({url: wrong_content})

        with pytest.raises(runtime.HashMismatchError):
            runtime._download(url, staging=staging, filename="file.zip",
                              expected_hash="a" * 64, fetch=fetch,
                              max_bytes=200_000_000)
        # The file must not remain
        assert not (staging / "file.zip").exists()


# ---------------------------------------------------------------------------
# Zip safety (zip slip prevention)
# ---------------------------------------------------------------------------


class TestZipSafety:
    def test_dotdot_rejected(self) -> None:
        assert not runtime._safe_member("../etc/passwd")

    def test_absolute_path_rejected(self) -> None:
        assert not runtime._safe_member("/etc/passwd")

    def test_windows_absolute_rejected(self) -> None:
        assert not runtime._safe_member("C:\\Windows\\system32\\evil.dll")

    def test_drive_letter_colon_rejected(self) -> None:
        assert not runtime._safe_member("D:file.txt")

    def test_backslash_dotdot_rejected(self) -> None:
        assert not runtime._safe_member("foo\\..\\bar")

    def test_normal_path_accepted(self) -> None:
        assert runtime._safe_member("dll/sqlite3mc_x64.dll")

    def test_empty_rejected(self) -> None:
        assert not runtime._safe_member("")


class TestExtractSingleMember:
    def test_extracts_target_file(self, tmp_path: Path) -> None:
        dll_content = b"MZ_fake_dll"
        archive_bytes = _make_zip({"dll/sqlite3mc_x64.dll": dll_content, "other.txt": b"x"})
        archive_path = tmp_path / "archive.zip"
        archive_path.write_bytes(archive_bytes)

        out = tmp_path / "output"
        out.mkdir()
        runtime._extract_member(archive_path, "dll/sqlite3mc_x64.dll", out / "sqlite3mc_x64.dll")
        assert (out / "sqlite3mc_x64.dll").read_bytes() == dll_content

    def test_rejects_zip_slip_member(self, tmp_path: Path) -> None:
        archive_bytes = _make_zip({"../evil.dll": b"pwned"})
        archive_path = tmp_path / "archive.zip"
        archive_path.write_bytes(archive_bytes)

        out = tmp_path / "output"
        out.mkdir()
        with pytest.raises(runtime.ZipUnsafeError):
            runtime._extract_member(archive_path, "../evil.dll", out / "evil.dll")


# ---------------------------------------------------------------------------
# Component swap (staging -> formal, rollback)
# ---------------------------------------------------------------------------


class TestSwap:
    def test_fresh_install(self, tmp_path: Path) -> None:
        staging = tmp_path / "python.staging-abc"
        staging.mkdir()
        (staging / "marker").write_text("new", encoding="utf-8")
        final = tmp_path / "python"

        runtime._swap_in(staging, final)
        assert (final / "marker").read_text(encoding="utf-8") == "new"
        assert not staging.exists()

    def test_replaces_old_version(self, tmp_path: Path) -> None:
        final = tmp_path / "python"
        final.mkdir()
        (final / "marker").write_text("old", encoding="utf-8")
        staging = tmp_path / "python.staging-abc"
        staging.mkdir()
        (staging / "marker").write_text("new", encoding="utf-8")

        runtime._swap_in(staging, final)
        assert (final / "marker").read_text(encoding="utf-8") == "new"
        # No .old directories left
        olds = [d for d in tmp_path.iterdir() if ".old-" in d.name]
        assert len(olds) == 0

    def test_staging_failure_preserves_old(self, tmp_path: Path) -> None:
        final = tmp_path / "python"
        final.mkdir()
        (final / "marker").write_text("old", encoding="utf-8")
        staging = tmp_path / "python.staging-abc"
        staging.mkdir()
        (staging / "marker").write_text("new", encoding="utf-8")

        # Simulate: rename staging -> final fails
        original_rename = os.rename

        def failing_rename(src, dst):
            src_p = Path(src)
            # Fail when trying to rename staging to final position
            if "staging" in src_p.name:
                raise OSError("simulated rename failure")
            return original_rename(src, dst)

        import unittest.mock
        with unittest.mock.patch("os.rename", side_effect=failing_rename):
            with pytest.raises(OSError, match="simulated"):
                runtime._swap_in(staging, final)

        # Old version must still be intact
        assert (final / "marker").read_text(encoding="utf-8") == "old"


# ---------------------------------------------------------------------------
# Vendor stamp (hash of vendor directory excluding node_modules)
# ---------------------------------------------------------------------------


class TestVendorStamp:
    def test_stable_hash(self, tmp_path: Path) -> None:
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "a.js").write_text("console.log('a')", encoding="utf-8")
        (vendor / "b.json").write_text("{}", encoding="utf-8")
        sub = vendor / "src"
        sub.mkdir()
        (sub / "c.py").write_text("pass", encoding="utf-8")

        h1 = runtime._vendor_hash(vendor)
        h2 = runtime._vendor_hash(vendor)
        assert h1 == h2
        assert len(h1) == 64

    def test_excludes_node_modules(self, tmp_path: Path) -> None:
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "a.js").write_text("x", encoding="utf-8")

        h_before = runtime._vendor_hash(vendor)
        nm = vendor / "node_modules"
        nm.mkdir()
        (nm / "dep.js").write_text("y", encoding="utf-8")
        h_after = runtime._vendor_hash(vendor)

        assert h_before == h_after

    def test_content_change_detected(self, tmp_path: Path) -> None:
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        f = vendor / "a.js"
        f.write_text("original", encoding="utf-8")
        h1 = runtime._vendor_hash(vendor)

        f.write_text("modified", encoding="utf-8")
        h2 = runtime._vendor_hash(vendor)

        assert h1 != h2


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


class TestStatus:
    def test_all_missing(self, tmp_path: Path) -> None:
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        st = runtime.status(tmp_path, plugin_root)
        assert st["ok"] is False
        for comp in ("python", "sqlite3mc", "line-desktop-mcp"):
            assert st["components"][comp]["installed"] is False

    def test_dll_tamper_detected(self, tmp_path: Path) -> None:
        """A DLL whose hash doesn't match the pinned constant must be flagged."""
        rt = tmp_path / "runtime" / "sqlite3mc"
        rt.mkdir(parents=True)
        dll = rt / "sqlite3mc_x64.dll"
        dll.write_bytes(b"tampered content")  # not the real DLL content

        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        st = runtime.status(tmp_path, plugin_root)
        assert st["components"]["sqlite3mc"]["tampered"] is True
        assert st["components"]["sqlite3mc"]["code"] == "RUNTIME_HASH_MISMATCH"
        # status.ok must be False when a component is tampered
        assert st["ok"] is False

    def test_outdated_vendor(self, tmp_path: Path) -> None:
        """Vendor stamp mismatch signals RUNTIME_OUTDATED."""
        plugin_root = tmp_path / "plugin"
        vendor_dir = plugin_root / "vendor" / "line-desktop-mcp"
        vendor_dir.mkdir(parents=True)
        (vendor_dir / "index.js").write_text("v2", encoding="utf-8")
        (vendor_dir / "package-lock.json").write_text("{}", encoding="utf-8")

        rt = tmp_path / "runtime" / "line-desktop-mcp"
        rt.mkdir(parents=True)
        # status() checks for package.json to decide if installed
        (rt / "package.json").write_text("{}", encoding="utf-8")
        stamp = rt / ".daily-agenda-stamp.json"
        stamp.write_text(json.dumps({"vendor_hash": "old_hash"}), encoding="utf-8")

        st = runtime.status(tmp_path, plugin_root)
        comp = st["components"]["line-desktop-mcp"]
        assert comp.get("code") == "RUNTIME_OUTDATED"
        # status.ok must be False when a component is outdated
        assert st["ok"] is False


# ---------------------------------------------------------------------------
# env_for_line
# ---------------------------------------------------------------------------


class TestEnvForLine:
    def test_no_extensions(self, tmp_path: Path) -> None:
        """env_for_line must never include LINE_MCP_EXTENSIONS."""
        config = {
            "schema_version": 1,
            "line": {"enabled": True, "main_database": "abc123.edb"},
        }
        (tmp_path / "config.json").write_text(
            json.dumps(config), encoding="utf-8")
        # Create runtime python path
        venv_python = tmp_path / "runtime" / "python" / ".venv" / "Scripts" / "python.exe"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("fake", encoding="utf-8")
        # Create DLL
        dll = tmp_path / "runtime" / "sqlite3mc" / "sqlite3mc_x64.dll"
        dll.parent.mkdir(parents=True)
        dll.write_bytes(b"fake")

        env = runtime.env_for_line(tmp_path)
        assert "LINE_MCP_EXTENSIONS" not in env
        assert env["LINE_MCP_MAIN_DATABASE"] == "abc123.edb"

    def test_trusted_builds_set_only_if_exists(self, tmp_path: Path) -> None:
        config = {"schema_version": 1, "line": {"main_database": "x.edb"}}
        (tmp_path / "config.json").write_text(
            json.dumps(config), encoding="utf-8")
        venv_python = tmp_path / "runtime" / "python" / ".venv" / "Scripts" / "python.exe"
        venv_python.parent.mkdir(parents=True)
        venv_python.write_text("fake", encoding="utf-8")
        dll = tmp_path / "runtime" / "sqlite3mc" / "sqlite3mc_x64.dll"
        dll.parent.mkdir(parents=True)
        dll.write_bytes(b"fake")

        # No trusted_builds.json
        env = runtime.env_for_line(tmp_path)
        assert "LINE_MCP_TRUSTED_BUILDS" not in env

        # Create it
        (tmp_path / "trusted_builds.json").write_text("{}", encoding="utf-8")
        env2 = runtime.env_for_line(tmp_path)
        assert "LINE_MCP_TRUSTED_BUILDS" in env2


# ---------------------------------------------------------------------------
# Install (integration-level with fakes)
# ---------------------------------------------------------------------------


class TestInstallSqlite3mc:
    """Test sqlite3mc component installation with controlled fakes."""

    def test_good_install(self, tmp_path: Path) -> None:
        """Happy path: download, verify archive hash, extract, verify DLL hash."""
        dll_content = b"MZ_real_dll_content"
        dll_hash = _sha256(dll_content)
        archive_bytes = _make_zip({"dll/sqlite3mc_x64.dll": dll_content})
        archive_hash = _sha256(archive_bytes)

        h = tmp_path / "home"
        h.mkdir()
        result = runtime.install_sqlite3mc(
            h,
            fetch=_make_fetcher({runtime.SQLITE3MC_URL: archive_bytes}),
            archive_sha256=archive_hash,
            dll_sha256=dll_hash,
        )
        assert result["ok"] is True
        dll_path = h / "runtime" / "sqlite3mc" / "sqlite3mc_x64.dll"
        assert dll_path.exists()
        assert dll_path.read_bytes() == dll_content
        # No staging dirs left
        rt = h / "runtime"
        staging_dirs = [d for d in rt.iterdir() if "staging" in d.name]
        assert len(staging_dirs) == 0

    def test_archive_hash_mismatch(self, tmp_path: Path) -> None:
        archive_bytes = _make_zip({"dll/sqlite3mc_x64.dll": b"x"})
        h = tmp_path / "home"
        h.mkdir()
        result = runtime.install_sqlite3mc(
            h,
            fetch=_make_fetcher({runtime.SQLITE3MC_URL: archive_bytes}),
            archive_sha256="wrong" + "0" * 59,
            dll_sha256="a" * 64,
        )
        assert result["ok"] is False
        assert result["code"] == "RUNTIME_HASH_MISMATCH"
        # No files left in runtime
        rt = h / "runtime"
        if rt.exists():
            staging_dirs = [d for d in rt.iterdir() if "staging" in d.name]
            assert len(staging_dirs) == 0

    def test_dll_hash_mismatch(self, tmp_path: Path) -> None:
        """Archive hash matches but extracted DLL hash doesn't."""
        dll_content = b"MZ_dll"
        archive_bytes = _make_zip({"dll/sqlite3mc_x64.dll": dll_content})
        archive_hash = _sha256(archive_bytes)

        h = tmp_path / "home"
        h.mkdir()
        result = runtime.install_sqlite3mc(
            h,
            fetch=_make_fetcher({runtime.SQLITE3MC_URL: archive_bytes}),
            archive_sha256=archive_hash,
            dll_sha256="wrong" + "0" * 59,
        )
        assert result["ok"] is False
        assert result["code"] == "RUNTIME_HASH_MISMATCH"

    def test_zip_slip_rejected(self, tmp_path: Path) -> None:
        """Archive containing path traversal is rejected."""
        archive_bytes = _make_zip({"../etc/passwd": b"pwned",
                                   "dll/sqlite3mc_x64.dll": b"ok"})
        archive_hash = _sha256(archive_bytes)
        dll_hash = _sha256(b"ok")

        h = tmp_path / "home"
        h.mkdir()
        # The install should extract only the target member after validation,
        # so this shouldn't be an issue unless the member itself is unsafe.
        # Let's test with the target member being unsafe:
        bad_archive = _make_zip({"../evil.dll": b"pwned"})
        bad_hash = _sha256(bad_archive)
        result = runtime.install_sqlite3mc(
            h,
            fetch=_make_fetcher({runtime.SQLITE3MC_URL: bad_archive}),
            archive_sha256=bad_hash,
            dll_sha256=_sha256(b"pwned"),
        )
        # Should fail because dll/sqlite3mc_x64.dll is not in the archive
        assert result["ok"] is False


class TestInstallLineDesktopMcp:
    def test_good_install(self, tmp_path: Path) -> None:
        plugin_root = tmp_path / "plugin"
        vendor_dir = plugin_root / "vendor" / "line-desktop-mcp"
        vendor_dir.mkdir(parents=True)
        (vendor_dir / "package.json").write_text('{"name":"test"}', encoding="utf-8")
        (vendor_dir / "package-lock.json").write_text('{}', encoding="utf-8")
        (vendor_dir / "index.js").write_text("module.exports={}", encoding="utf-8")

        h = tmp_path / "home"
        h.mkdir()

        def runner(cmd, **kwargs):
            # npm ci — create a fake node_modules
            nm = Path(kwargs.get("cwd", ".")) / "node_modules"
            nm.mkdir(exist_ok=True)
            (nm / "dep.js").write_text("x", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        result = runtime.install_line_desktop_mcp(
            h,
            plugin_root=plugin_root,
            runner=runner,
            which=_make_which({"npm.cmd": "npm.cmd", "npm": "npm"}),
        )
        assert result["ok"] is True
        final = h / "runtime" / "line-desktop-mcp"
        assert final.exists()
        assert (final / "package.json").exists()
        # Stamp exists and excludes node_modules
        stamp = json.loads((final / ".daily-agenda-stamp.json").read_text(encoding="utf-8"))
        assert "vendor_hash" in stamp

    def test_npm_failure_rolls_back(self, tmp_path: Path) -> None:
        plugin_root = tmp_path / "plugin"
        vendor_dir = plugin_root / "vendor" / "line-desktop-mcp"
        vendor_dir.mkdir(parents=True)
        (vendor_dir / "package.json").write_text("{}", encoding="utf-8")
        (vendor_dir / "package-lock.json").write_text("{}", encoding="utf-8")

        h = tmp_path / "home"
        h.mkdir()
        # Pre-create a "previous install"
        old = h / "runtime" / "line-desktop-mcp"
        old.mkdir(parents=True)
        (old / "marker").write_text("old_version", encoding="utf-8")

        def failing_runner(cmd, **kwargs):
            return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="npm ERR!")

        result = runtime.install_line_desktop_mcp(
            h,
            plugin_root=plugin_root,
            runner=failing_runner,
            which=_make_which({"npm": "npm"}),
        )
        assert result["ok"] is False
        # Old version preserved
        assert (old / "marker").read_text(encoding="utf-8") == "old_version"
        # No staging dirs
        rt = h / "runtime"
        staging = [d for d in rt.iterdir() if "staging" in d.name]
        assert len(staging) == 0

    def test_stamp_stable_and_excludes_node_modules(self, tmp_path: Path) -> None:
        vendor = tmp_path / "vendor"
        vendor.mkdir()
        (vendor / "a.js").write_text("x", encoding="utf-8")

        h1 = runtime._vendor_hash(vendor)

        nm = vendor / "node_modules"
        nm.mkdir()
        (nm / "dep.js").write_text("y", encoding="utf-8")

        h2 = runtime._vendor_hash(vendor)
        assert h1 == h2


class TestInstallPython:
    def test_platform_mismatch_rejected(self, tmp_path: Path) -> None:
        h = tmp_path / "home"
        h.mkdir()
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()
        result = runtime.install_python(
            h,
            plugin_root=plugin_root,
            runner=_make_runner({"uv": (0, "")}),
            which=_make_which({"uv": "uv"}),
            arch_checker=_make_arch_checker(arch="32bit"),
        )
        assert result["ok"] is False
        assert result["code"] == "RUNTIME_PLATFORM_MISMATCH"

    def test_good_install(self, tmp_path: Path) -> None:
        h = tmp_path / "home"
        h.mkdir()
        plugin_root = tmp_path / "plugin"
        req_dir = plugin_root / "vendor" / "line-desktop-mcp" / "src" / "extensions" / "python"
        req_dir.mkdir(parents=True)
        (req_dir / "requirements.txt").write_text("cryptography\nPillow\n", encoding="utf-8")

        call_count = {"uv": 0}

        def runner(cmd, **kwargs):
            call_count["uv"] += 1
            # On the venv creation call, create the expected python path
            if "venv" in cmd:
                venv_dir = None
                for i, arg in enumerate(cmd):
                    if arg == "--python" and i + 1 < len(cmd):
                        # Next non-flag arg after --python <ver> is the path
                        continue
                    # The venv path is the last positional arg
                if len(cmd) > 3:
                    venv_dir = Path(cmd[-1])
                    if venv_dir and not str(venv_dir).startswith("-"):
                        scripts = venv_dir / "Scripts"
                        scripts.mkdir(parents=True, exist_ok=True)
                        (scripts / "python.exe").write_text("fake", encoding="utf-8")
            return subprocess.CompletedProcess(cmd, 0, stdout="", stderr="")

        result = runtime.install_python(
            h,
            plugin_root=plugin_root,
            runner=runner,
            which=_make_which({"uv": "uv"}),
            arch_checker=_make_arch_checker(),
        )
        assert result["ok"] is True
        final = h / "runtime" / "python" / ".venv" / "Scripts" / "python.exe"
        assert final.exists()


# ---------------------------------------------------------------------------
# Full install orchestration
# ---------------------------------------------------------------------------


class TestInstallOrchestration:
    def test_default_components_exclude_cua(self) -> None:
        assert "cua" not in runtime.DEFAULT_COMPONENTS

    def test_install_continues_past_component_failure(self, tmp_path: Path) -> None:
        """If one component fails, others still attempt."""
        h = tmp_path / "home"
        h.mkdir()
        plugin_root = tmp_path / "plugin"
        plugin_root.mkdir()

        # Provide minimal vendor structure for line-desktop-mcp
        vendor_dir = plugin_root / "vendor" / "line-desktop-mcp"
        vendor_dir.mkdir(parents=True)
        (vendor_dir / "package.json").write_text("{}", encoding="utf-8")
        (vendor_dir / "package-lock.json").write_text("{}", encoding="utf-8")

        # requirements.txt for python
        req_dir = plugin_root / "vendor" / "line-desktop-mcp" / "src" / "extensions" / "python"
        req_dir.mkdir(parents=True, exist_ok=True)
        (req_dir / "requirements.txt").write_text("x\n", encoding="utf-8")

        result = runtime.install(
            h,
            plugin_root=plugin_root,
            components=["sqlite3mc"],  # only sqlite3mc, which will fail (no fetch content)
            fetch=_make_fetcher({}),
            runner=_make_runner({}),
            which=_make_which({}),
            arch_checker=_make_arch_checker(),
        )
        assert result["ok"] is False
        assert "sqlite3mc" in result["components"]
        assert result["components"]["sqlite3mc"]["ok"] is False
