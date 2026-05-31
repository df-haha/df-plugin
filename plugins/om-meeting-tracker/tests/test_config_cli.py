from __future__ import annotations
import subprocess, sys
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
FIX = Path(__file__).parent / "fixtures"

def _run(args):
    return subprocess.run([sys.executable, str(ROOT / "scripts/mt_core/config.py"), *args],
                          capture_output=True, text=True)

def test_validate_ok():
    r = _run(["--validate", str(FIX / "config_valid.md")])
    assert r.returncode == 0, r.stderr
    assert "OK" in r.stdout

def test_validate_fail(tmp_path):
    bad = tmp_path / "bad.md"
    bad.write_text("no block", encoding="utf-8")
    r = _run(["--validate", str(bad)])
    assert r.returncode != 0
    assert "找不到" in (r.stdout + r.stderr)
