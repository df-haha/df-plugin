"""Tests for parse_root_args: unknown root-level flag rejection (H1b)."""

import os
import subprocess
import sys
from pathlib import Path

import pytest

SCRIPTS = Path(__file__).resolve().parent.parent  # <P>/scripts


def _run_cli(
    argv: list[str],
    *,
    cwd: str | None = None,
    home: str | None = None,
) -> subprocess.CompletedProcess:
    """Run daily_agenda CLI as a subprocess and return result."""
    cmd = [sys.executable, "-m", "daily_agenda"] + argv
    env = {**os.environ, "DAILY_AGENDA_HOME": home or "/tmp/da-test-nonexistent"}
    return subprocess.run(
        cmd, capture_output=True, text=True,
        cwd=cwd or str(SCRIPTS),
        env=env,
        timeout=10,
    )


class TestUnknownRootFlags:
    """H1b: unknown root-level flags before the command must be rejected."""

    def test_bogus_flag_before_subcommand_rejected(self):
        """--bogus outlook-preview must fail (ARG_ERROR exit 2)."""
        r = _run_cli(["--bogus", "outlook-preview"])
        assert r.returncode == 2, f"Expected exit 2, got {r.returncode}: {r.stdout}"
        assert "ARG_ERROR" in r.stdout

    def test_unknown_short_flag_before_subcommand_rejected(self):
        """-x outlook-preview must fail."""
        r = _run_cli(["-x", "outlook-preview"])
        assert r.returncode == 2, f"Expected exit 2, got {r.returncode}: {r.stdout}"
        assert "ARG_ERROR" in r.stdout

    def test_unknown_flag_with_value_before_subcommand_rejected(self):
        """--evil=val outlook-preview must fail."""
        r = _run_cli(["--evil=val", "outlook-preview"])
        assert r.returncode == 2, f"Expected exit 2, got {r.returncode}: {r.stdout}"
        assert "ARG_ERROR" in r.stdout

    def test_progress_file_before_subcommand_ok(self, tmp_path: Path):
        """--progress-file <path> <cmd> must still parse (not rejected as unknown flag)."""
        # Set up a valid home with runs/progress/ so the path validation passes
        home = tmp_path / ".daily-agenda"
        progress_dir = home / "runs" / "progress"
        progress_dir.mkdir(parents=True)
        pf = str(progress_dir / "x.jsonl")
        r = _run_cli(["--progress-file", pf, "envcheck"], home=str(home))
        # Must NOT be ARG_ERROR about unknown flag
        assert r.returncode != 2, \
            f"Should not be exit 2 ARG_ERROR: {r.stdout}"
        assert "ARG_ERROR" not in r.stdout

    def test_progress_file_after_subcommand_ok(self, tmp_path: Path):
        """<cmd> --progress-file <path> must still parse."""
        home = tmp_path / ".daily-agenda"
        progress_dir = home / "runs" / "progress"
        progress_dir.mkdir(parents=True)
        pf = str(progress_dir / "x.jsonl")
        r = _run_cli(["envcheck", "--progress-file", pf], home=str(home))
        assert r.returncode != 2, \
            f"Should not be exit 2 ARG_ERROR: {r.stdout}"
        assert "ARG_ERROR" not in r.stdout

    def test_subcommand_flags_not_rejected(self):
        """Flags after the command go to subcommand parser, not rejected."""
        r = _run_cli(["envcheck"])
        # envcheck may fail for non-Windows, but it should not be ARG_ERROR
        assert "ARG_ERROR" not in r.stdout


class TestIsolation:
    """python -I -X utf8 prevents cwd and PYTHONPATH module shadowing."""

    def _make_fake_pkg(self, tmp_path: Path) -> Path:
        """Create a fake daily_agenda package that prints a sentinel."""
        fake_pkg = tmp_path / "daily_agenda"
        fake_pkg.mkdir(exist_ok=True)
        (fake_pkg / "__init__.py").write_text("")
        (fake_pkg / "__main__.py").write_text(
            'import sys; sys.stdout.write("SHADOW_SENTINEL"); sys.exit(99)\n'
        )
        return fake_pkg

    def test_isolated_mode_prevents_cwd_shadow(self, tmp_path: Path):
        """With -I, a fake daily_agenda package in cwd does not execute."""
        self._make_fake_pkg(tmp_path)
        r_safe = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", "-m", "daily_agenda", "envcheck"],
            capture_output=True, text=True, cwd=str(tmp_path), timeout=10,
        )
        assert "SHADOW_SENTINEL" not in r_safe.stdout, \
            "-I should prevent cwd shadow"

    def test_isolated_mode_prevents_pythonpath_shadow(self, tmp_path: Path):
        """With -I, PYTHONPATH-planted fake daily_agenda does not execute."""
        shadow = tmp_path / "shadow"
        shadow.mkdir()
        self._make_fake_pkg(shadow)
        env = {**os.environ, "PYTHONPATH": str(shadow)}
        r_safe = subprocess.run(
            [sys.executable, "-I", "-X", "utf8", "-m", "daily_agenda", "envcheck"],
            capture_output=True, text=True, cwd=str(SCRIPTS), env=env, timeout=10,
        )
        assert "SHADOW_SENTINEL" not in r_safe.stdout, \
            "-I should prevent PYTHONPATH shadow"

    def test_p_flag_alone_does_not_block_pythonpath(self, tmp_path: Path):
        """With -P only, PYTHONPATH shadow DOES execute (proves -I is needed)."""
        shadow = tmp_path / "shadow"
        shadow.mkdir()
        self._make_fake_pkg(shadow)
        env = {**os.environ, "PYTHONPATH": str(shadow)}
        r_unsafe = subprocess.run(
            [sys.executable, "-P", "-m", "daily_agenda", "envcheck"],
            capture_output=True, text=True, cwd=str(SCRIPTS), env=env, timeout=10,
        )
        assert "SHADOW_SENTINEL" in r_unsafe.stdout, \
            "-P alone should NOT block PYTHONPATH shadow (proves -I is needed)"

    def test_without_safe_path_cwd_shadows(self, tmp_path: Path):
        """Without -P/-I, a fake daily_agenda package in cwd DOES execute (proves test is live)."""
        self._make_fake_pkg(tmp_path)

        # Without -P: fake package shadows
        r_unsafe = subprocess.run(
            [sys.executable, "-m", "daily_agenda", "envcheck"],
            capture_output=True, text=True, cwd=str(tmp_path), timeout=10,
        )
        assert "SHADOW_SENTINEL" in r_unsafe.stdout, \
            "Without -P, cwd should shadow (proves test is live)"
