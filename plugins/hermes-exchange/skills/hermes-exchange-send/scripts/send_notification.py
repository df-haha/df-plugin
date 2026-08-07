#!/usr/bin/env python3
"""Invoke the packaged Hermes Exchange CLI without duplicating relay policy."""

from __future__ import annotations

from pathlib import Path
import sys


PLUGIN_ASSETS = Path(__file__).resolve().parents[3] / "assets"
sys.path.insert(0, str(PLUGIN_ASSETS))

from hermes_exchange.cli import main  # noqa: E402


if __name__ == "__main__":
    raise SystemExit(main())
