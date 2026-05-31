# tests/test_track_dryrun.py
from __future__ import annotations
import glob, subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples" / "dafeng-ops"

def test_non_dry_run_writes_draft_file(tmp_path):
    # Copy config.md and tracking.md into tmp_path preserving relative paths expected by config
    (tmp_path / "state").mkdir(parents=True, exist_ok=True)
    (tmp_path / "drafts").mkdir(parents=True, exist_ok=True)
    config_dst = tmp_path / "config.md"
    config_dst.write_bytes((EX / "config.md").read_bytes())
    tracking_dst = tmp_path / "tracking.md"
    tracking_dst.write_bytes((EX / "tracking.md").read_bytes())
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/track.py"),
         "--config", str(config_dst),
         "--repo-root", str(tmp_path),
         "--today", "2026-05-29"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    drafts = list((tmp_path / "drafts").glob("meeting_draft_week_*.md"))
    assert len(drafts) == 1, f"expected 1 draft file, got {drafts}"


def test_bad_config_nonzero_exit(tmp_path):
    bad_config = tmp_path / "bad_config.md"
    bad_config.write_text("no mt-config block here", encoding="utf-8")
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/track.py"),
         "--config", str(bad_config)],
        capture_output=True, text=True)
    assert r.returncode != 0


def test_dryrun_lists_owners_to_remind():
    r = subprocess.run(
        [sys.executable, str(ROOT / "scripts/track.py"),
         "--config", str(EX / "config.md"),
         "--repo-root", str(EX),
         "--dry-run", "--today", "2026-05-29"],
        capture_output=True, text=True)
    assert r.returncode == 0, r.stderr
    # g1-tender 是 red 且近 5/31 → haha 必催；輸出含 draft preview
    assert "haha" in r.stdout
    assert "準會議版 draft" in r.stdout
    assert "⏳ 待會議" in r.stdout
