# tests/conftest.py — 讓 tests 能 import mt_core（把 plugin 的 scripts/ 加進 sys.path）
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent.parent / "scripts"))
