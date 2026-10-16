"""Tests for envcheck (task 2.3)."""

from __future__ import annotations

import hashlib
import json
import subprocess
from pathlib import Path

import pytest

from daily_agenda import envcheck

HELP_OK = "\n".join(f"  {f}  desc" for f in envcheck.REQUIRED_CLAUDE_FLAGS)


def make_runner(responses: dict[str, tuple[int, str]]):
    calls = []

    def runner(cmd, **kwargs):
        assert kwargs.get("encoding") == "utf-8" and kwargs.get("errors") == "replace"
        calls.append(cmd)
        key = Path(cmd[0]).name.lower().removesuffix(".exe")
        if key not in responses:
            raise FileNotFoundError(cmd[0])
        rc, out = responses[key]
        return subprocess.CompletedProcess(cmd, rc, stdout=out, stderr="")

    runner.calls = calls
    return runner


def test_node_version_parse():
    assert envcheck.parse_node_major("v24.11.1\n") == 24
    assert envcheck.parse_node_major("v20.1.0") == 20
    assert envcheck.parse_node_major("garbage") is None


def test_all_ok(monkeypatch):
    runner = make_runner({"claude": (0, HELP_OK), "node": (0, "v24.11.1"), "uv": (0, "uv 0.9.18"),
                          "tasklist": (0, '"LINE.exe","24224","Console","1","548,000 K"\n')})
    monkeypatch.setattr(envcheck, "_which", lambda name: name)
    monkeypatch.setattr(envcheck, "check_lock_hook", lambda **kw: envcheck._check("lock_hook", True))
    report = envcheck.check_environment(runner=runner, is_windows=True)
    assert report["ok"] is True
    assert {c["name"]: c["ok"] for c in report["checks"]} == {
        "platform": True, "claude": True, "node": True, "uv": True,
        "lock_hook": True, "line_running": True}


def test_missing_flags_and_old_node(monkeypatch):
    runner = make_runner({"claude": (0, "  --tools  x\n"), "node": (0, "v20.0.0"), "uv": (0, "uv 1"),
                          "tasklist": (0, "INFO: No tasks are running which match the specified criteria.\n")})
    monkeypatch.setattr(envcheck, "_which", lambda name: name)
    monkeypatch.setattr(envcheck, "check_lock_hook", lambda **kw: envcheck._check("lock_hook", True))
    report = envcheck.check_environment(runner=runner, is_windows=True)
    by = {c["name"]: c for c in report["checks"]}
    assert by["claude"]["code"] == "CLAUDE_FLAGS_MISSING"
    assert "--no-chrome" in by["claude"]["missing"]
    assert by["node"]["code"] == "NODE_TOO_OLD"
    assert by["line_running"]["code"] == "LINE_NOT_RUNNING"
    assert report["ok"] is False
    # every failing check carries a plain-language message
    assert all(c["message"] for c in report["checks"] if not c["ok"])


def test_missing_executables(monkeypatch):
    monkeypatch.setattr(envcheck, "_which", lambda name: None)
    monkeypatch.setattr(envcheck, "check_lock_hook", lambda **kw: envcheck._check("lock_hook", False, "LOCK_HOOK_UNAVAILABLE"))
    report = envcheck.check_environment(runner=make_runner({}), is_windows=True)
    by = {c["name"]: c["code"] for c in report["checks"]}
    assert by["node"] == "NODE_MISSING" and by["uv"] == "UV_MISSING"


def test_not_windows():
    report = envcheck.check_environment(runner=make_runner({}), is_windows=False)
    assert report["ok"] is False
    assert report["checks"][0]["code"] == "NOT_WINDOWS"


def test_line_exe_info(tmp_path: Path):
    exe = tmp_path / "LINE.exe"
    exe.write_bytes(b"MZfake")
    sig = {"Status": "Valid", "Subject": "CN=LY Corporation, O=LY Corporation, C=JP",
           "Thumbprint": "ABC", "FileVersion": "26.5.0.3975"}

    def runner(cmd, **kwargs):
        assert "powershell" in Path(cmd[0]).name.lower()
        # path must travel through the environment, never through the script text
        assert str(exe) not in " ".join(cmd)
        assert kwargs["env"]["DA_TARGET_EXE"] == str(exe)
        return subprocess.CompletedProcess(cmd, 0, stdout=json.dumps(sig), stderr="")

    info = envcheck.read_line_exe_info(exe, runner=runner)
    assert info == {
        "path": str(exe), "version": "26.5.0.3975", "bytes": 6,
        "sha256": hashlib.sha256(b"MZfake").hexdigest(),
        "signature_status": "Valid", "signer_subject": sig["Subject"],
        "signer_org": "LY Corporation", "thumbprint": "ABC",
    }


@pytest.mark.parametrize("subject,org", [
    ("CN=LY Corporation, O=LY Corporation, L=Chiyoda-ku", "LY Corporation"),
    ('CN=X, O="Foo, Inc.", C=US', "Foo, Inc."),
    ("CN=NoOrg", None),
])
def test_org_parse(subject, org):
    assert envcheck.parse_org(subject) == org


def test_signature_unreadable(tmp_path: Path):
    exe = tmp_path / "LINE.exe"
    exe.write_bytes(b"x")

    def runner(cmd, **kwargs):
        return subprocess.CompletedProcess(cmd, 1, stdout="", stderr="boom")

    info = envcheck.read_line_exe_info(exe, runner=runner)
    assert info["signature_status"] == "Unreadable"
    assert info["signer_org"] is None


# ---------------------------------------------------------------------------
# F8: lock hook self-test
# ---------------------------------------------------------------------------

import shutil as _shutil

_node_available = _shutil.which("node") is not None


class TestCheckLockHook:
    """F8: envcheck runs the lock hook with a tainted fixture and expects deny."""

    @pytest.mark.skipif(not _node_available, reason="node not on PATH")
    def test_passes_when_hook_works(self, tmp_path: Path) -> None:
        """With a taint marker present, the hook should deny WebFetch."""
        plugin_root = Path(__file__).resolve().parents[2]
        result = envcheck.check_lock_hook(
            runner=subprocess.run,
            plugin_root=plugin_root,
            tmp_dir=tmp_path,
        )
        assert result["ok"] is True
        assert result["name"] == "lock_hook"

    @pytest.mark.skipif(not _node_available, reason="node not on PATH")
    def test_fails_when_hook_missing(self, tmp_path: Path) -> None:
        """When the hook file doesn't exist, the check should fail."""
        fake_plugin = tmp_path / "fake-plugin"
        fake_plugin.mkdir()
        result = envcheck.check_lock_hook(
            runner=subprocess.run,
            plugin_root=fake_plugin,
            tmp_dir=tmp_path / "tmp",
        )
        assert result["ok"] is False
        assert result["code"] == "LOCK_HOOK_UNAVAILABLE"
        assert result["message"]  # has a 繁中 message

    def test_fails_when_node_missing(self, tmp_path: Path, monkeypatch) -> None:
        """When node is not on PATH, the check should fail."""
        monkeypatch.setattr(envcheck, "_which", lambda name: None)
        plugin_root = Path(__file__).resolve().parents[2]
        result = envcheck.check_lock_hook(
            runner=subprocess.run,
            plugin_root=plugin_root,
            tmp_dir=tmp_path,
        )
        assert result["ok"] is False
        assert result["code"] == "LOCK_HOOK_UNAVAILABLE"

    @pytest.mark.skipif(not _node_available, reason="node not on PATH")
    def test_temp_dir_cleaned_up(self, tmp_path: Path) -> None:
        """The temporary DA home for the self-test should be removed."""
        plugin_root = Path(__file__).resolve().parents[2]
        envcheck.check_lock_hook(
            runner=subprocess.run,
            plugin_root=plugin_root,
            tmp_dir=tmp_path,
        )
        # The temp DA home (inside tmp_dir) should be cleaned up
        subdirs = list(tmp_path.iterdir())
        # Only the marker for cleanup verification should remain
        assert all(not d.name.startswith("da-selftest-") for d in subdirs if d.is_dir())


class TestCheckEnvironmentIncludesLockHook:
    """check_environment should include lock_hook when is_windows=True."""

    def test_lock_hook_in_checks(self, monkeypatch) -> None:
        runner = make_runner({
            "claude": (0, HELP_OK), "node": (0, "v24.11.1"),
            "uv": (0, "uv 0.9.18"),
            "tasklist": (0, '"LINE.exe","24224","Console","1","548,000 K"\n'),
        })
        monkeypatch.setattr(envcheck, "_which", lambda name: name)
        # Monkey-patch check_lock_hook to return OK
        monkeypatch.setattr(envcheck, "check_lock_hook", lambda **kw: envcheck._check("lock_hook", True))
        report = envcheck.check_environment(runner=runner, is_windows=True)
        names = [c["name"] for c in report["checks"]]
        assert "lock_hook" in names
