#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Generate an 8-hex-char NONCE for deep-research runDir suffix.

Cross-platform replacement for `openssl rand -hex 4` (macOS/Linux only).

Usage:
    python nonce.py

Prints one line to stdout, e.g. `a3f7b2c1`. Main dialogue captures it and
appends to `{topic}_{YYYYMMDD}_{NONCE}/` directory name for low-collision
multi-window isolation. The 8-char (32-bit) nonce provides sufficiently low
collision probability; runDir is created with exist_ok=False semantics so
any collision causes immediate regeneration rather than silent directory reuse.
"""
from __future__ import annotations
from __future__ import print_function
import sys


def gen_nonce() -> str:
    try:
        # Python 3.6+ preferred: cryptographically strong
        import secrets
        return secrets.token_hex(4)
    except Exception:
        # Fallback for exotic environments
        import os
        import binascii
        return binascii.hexlify(os.urandom(4)).decode('ascii')


if __name__ == '__main__':
    # Force UTF-8 output on Windows consoles (avoid cp950/cp1252 surprises)
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    print(gen_nonce())
