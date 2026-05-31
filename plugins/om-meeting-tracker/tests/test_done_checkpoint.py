from __future__ import annotations
import json, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"

def _git(repo, *args):
    subprocess.run(["git", "-C", str(repo), *args], check=True, capture_output=True)

def test_done_writes_checkpoint(tmp_path):
    repo = tmp_path
    (repo / ".meeting-tracker").mkdir()
    (repo / "tracking").mkdir()
    # 用合法 config（paths 對到 tmp repo 內）
    cfg_text = (FIX / "config_valid.md").read_text(encoding="utf-8")
    (repo / ".meeting-tracker" / "config.md").write_text(cfg_text, encoding="utf-8")
    (repo / "tracking" / "weekly.md").write_text("# 追蹤\n", encoding="utf-8")
    _git(repo, "init", "-q")
    _git(repo, "config", "user.email", "t@t.com")
    _git(repo, "config", "user.name", "t")
    _git(repo, "add", "-A")
    _git(repo, "commit", "-qm", "init")

    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/done_checkpoint.py"),
         "--config", str(repo / ".meeting-tracker/config.md"), "--repo-root", str(repo)],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    state = json.loads((repo / "state" / "meeting_tracker_state.json").read_text(encoding="utf-8"))
    rec = state["last_human_reviewed"]
    assert rec["tracking_file_blob_sha"] and rec["tracking_file_commit_sha"] and rec["at"]
