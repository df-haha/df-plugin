from __future__ import annotations
import os
import subprocess
from pathlib import Path
import pytest
from mt_core.config import load_config
from ci_governance_check import check_changed_paths, _is_gitlink

FIX = Path(__file__).parent / "fixtures"

def _cfg():
    return load_config(FIX / "config_valid.md")
    # allowed：draft_dir=drafts/、state_file dir=state/、run_log_dir=run-log/

def test_allowed_paths_pass(tmp_path):
    cfg = _cfg()
    changed = ["drafts/meeting_draft_week_2026-W22.md",
               "state/meeting_tracker_state.json",
               "run-log/run_report_2026-05-29.md"]
    assert check_changed_paths(cfg, tmp_path, changed) == []

def test_tracking_file_change_blocked(tmp_path):
    cfg = _cfg()
    v = check_changed_paths(cfg, tmp_path, ["tracking/weekly.md"])
    assert len(v) == 1 and "tracking/weekly.md" in v[0]

def test_parent_traversal_blocked(tmp_path):
    cfg = _cfg()
    v = check_changed_paths(cfg, tmp_path, ["drafts/../../etc/passwd"])
    assert v and "../" in v[0] or ".." in v[0]

def test_absolute_path_blocked(tmp_path):
    cfg = _cfg()
    v = check_changed_paths(cfg, tmp_path, ["/etc/passwd"])
    assert v

def test_gitmodules_blocked(tmp_path):
    cfg = _cfg()
    v = check_changed_paths(cfg, tmp_path, [".gitmodules"])
    assert v

def test_symlink_in_allowed_dir_blocked(tmp_path):
    cfg = _cfg()
    (tmp_path / "drafts").mkdir()
    target = tmp_path / "secret.txt"; target.write_text("x", encoding="utf-8")
    link = tmp_path / "drafts" / "evil.md"
    os.symlink(target, link)
    v = check_changed_paths(cfg, tmp_path, ["drafts/evil.md"])
    assert v and "symlink" in v[0].lower()

def test_symlinked_parent_dir_blocked(tmp_path):
    """FIX A: symlinked ANCESTOR dir must be caught even if final component is not a symlink."""
    cfg = _cfg()
    secret = tmp_path / "outside"
    secret.mkdir()
    (secret / "x.md").write_text("x", encoding="utf-8")
    # make drafts/ itself a symlink to the outside dir
    os.symlink(secret, tmp_path / "drafts")
    v = check_changed_paths(cfg, tmp_path, ["drafts/x.md"])
    assert v, "symlinked parent dir must be rejected"
    assert any("symlink" in vi.lower() for vi in v)

def test_backslash_path_blocked(tmp_path):
    """FIX B: backslash in path (literal filename char on Linux CI) must be rejected."""
    cfg = _cfg()
    v = check_changed_paths(cfg, tmp_path, ["drafts\\..\\evil.md"])
    assert v, "backslash path must be blocked"
    assert any("反斜線" in vi for vi in v)


def _setup_real_gitlink_repo(tmp_path: Path) -> bool:
    """Attempt to create a real git repo with a gitlink entry (mode 160000).

    Returns True if the setup succeeded and git ls-files -s shows mode 160000.
    Returns False if this git version rejects the cacheinfo approach (fallback to monkeypatch).
    """
    # Init the outer repo
    r = subprocess.run(["git", "init", str(tmp_path)], capture_output=True)
    if r.returncode != 0:
        return False
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.email", "test@example.com"], capture_output=True)
    subprocess.run(["git", "-C", str(tmp_path), "config", "user.name", "Test"], capture_output=True)

    # Create the drafts/ directory so the path makes sense
    (tmp_path / "drafts").mkdir(exist_ok=True)

    # Approach A: use --cacheinfo to inject a gitlink entry directly into the index
    dummy_sha = "0" * 40
    r2 = subprocess.run(
        ["git", "-C", str(tmp_path), "update-index", "--add",
         "--cacheinfo", f"160000,{dummy_sha},drafts/subproj"],
        capture_output=True,
    )
    if r2.returncode == 0:
        # Verify the index shows 160000
        ls = subprocess.run(
            ["git", "-C", str(tmp_path), "ls-files", "-s", "--", "drafts/subproj"],
            capture_output=True, text=True,
        )
        if ls.stdout.strip() and ls.stdout.split()[0] == "160000":
            return True
        # If ls-files doesn't confirm, fall through to real nested repo approach

    # Approach B: create a nested git repo with an actual commit
    nested = tmp_path / "drafts" / "subproj"
    nested.mkdir(parents=True, exist_ok=True)
    subprocess.run(["git", "init", str(nested)], capture_output=True)
    subprocess.run(["git", "-C", str(nested), "config", "user.email", "test@example.com"], capture_output=True)
    subprocess.run(["git", "-C", str(nested), "config", "user.name", "Test"], capture_output=True)
    (nested / "README.md").write_text("nested\n", encoding="utf-8")
    subprocess.run(["git", "-C", str(nested), "add", "."], capture_output=True)
    subprocess.run(["git", "-C", str(nested), "commit", "-m", "init"], capture_output=True)

    subprocess.run(["git", "-C", str(tmp_path), "add", "drafts/subproj"], capture_output=True)

    ls = subprocess.run(
        ["git", "-C", str(tmp_path), "ls-files", "-s", "--", "drafts/subproj"],
        capture_output=True, text=True,
    )
    if ls.stdout.strip() and ls.stdout.split()[0] == "160000":
        return True
    return False


def test_gitlink_mode_blocked(tmp_path, monkeypatch):
    """FIX 1: gitlink (mode 160000) path in an allowed dir must be blocked.

    Tries real-git setup first (preferred).  Falls back to monkeypatching
    _is_gitlink to prove the wiring in check_changed_paths is correct.
    """
    cfg = _cfg()
    real_git_worked = _setup_real_gitlink_repo(tmp_path)

    if real_git_worked:
        # Use the real tmp git repo — _is_gitlink will shell out and detect 160000
        v = check_changed_paths(cfg, tmp_path, ["drafts/subproj"])
        assert v, "gitlink path should be blocked"
        assert any("160000" in vi or "gitlink" in vi.lower() for vi in v), (
            f"Violation should mention gitlink/160000; got: {v}"
        )
    else:
        # Fallback: monkeypatch _is_gitlink to return True for the specific path,
        # proving the wiring is correct even when real-git setup is unavailable.
        import ci_governance_check
        monkeypatch.setattr(ci_governance_check, "_is_gitlink",
                            lambda repo_root, p: p == "drafts/subproj")
        v = check_changed_paths(cfg, tmp_path, ["drafts/subproj"])
        assert v, "gitlink path should be blocked (monkeypatched)"
        assert any("160000" in vi or "gitlink" in vi.lower() for vi in v), (
            f"Violation should mention gitlink/160000; got: {v}"
        )
