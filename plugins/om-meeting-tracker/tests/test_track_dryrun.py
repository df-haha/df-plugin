# tests/test_track_dryrun.py
from __future__ import annotations
import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
EX = ROOT / "examples" / "dafeng-ops"

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
