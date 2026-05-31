from __future__ import annotations
import os
from pathlib import Path
from mt_core.config import load_config
from ci_governance_check import check_changed_paths

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
