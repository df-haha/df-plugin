#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Extract all URLs from one or more markdown files, dedupe, print one per line.

Cross-platform replacement for `grep -oE 'https?://[^ )\\]]+' {report}`
(BSD/GNU grep only; Windows PowerShell has no grep).

Usage:
    python extract_urls.py <report_file> [<report_file2> ...]
    python extract_urls.py <directory>       # scans *.md recursively

Prints deduped URLs to stdout, one per line, sorted by first-seen order.
Handles markdown link `[text](url)`, angle-bracket `<url>`, and bare URL.
UTF-8 file read enforced (Windows default is cp950/cp1252 → mojibake risk).
"""
from __future__ import annotations
from __future__ import print_function
import os
import re
import sys
from pathlib import Path

# Match http/https URL, stopping at whitespace, close bracket, angle bracket, quote.
# Close paren ')' is intentionally INCLUDED in the match so that URLs like
# Function_(mathematics) are captured whole; unbalanced trailing ')' is stripped
# by the post-match balanced-paren logic below.
# Trailing punctuation `.,;:` is stripped after match (common in prose).
URL_RE = re.compile(r'https?://[^\s\]<>"\'`]+', re.IGNORECASE)
TRAILING_PUNCT = '.,;:!?'


def extract_from_text(text: str) -> list[str]:
    urls = []
    seen = set()
    for m in URL_RE.finditer(text):
        u = m.group(0)
        while u and u[-1] in TRAILING_PUNCT:
            u = u[:-1]
        while u.endswith(')') and u.count('(') < u.count(')'):
            u = u[:-1]
        if u and u not in seen:
            seen.add(u)
            urls.append(u)
    return urls


def gather_files(paths: list[str]) -> list[Path]:
    files: list[Path] = []
    for p in paths:
        pp = Path(p)
        if pp.is_dir():
            files.extend(sorted(pp.rglob('*.md')))
        elif pp.is_file():
            files.append(pp)
        else:
            print('warning: not found: {0}'.format(p), file=sys.stderr)
    return files


def main(argv: list[str]) -> int:
    if len(argv) < 2:
        print('Usage: python extract_urls.py <report_file_or_dir> [...]',
              file=sys.stderr)
        return 2
    files = gather_files(argv[1:])
    if not files:
        print('warning: no .md files found', file=sys.stderr)
        return 1
    all_seen = set()
    ordered = []
    for f in files:
        try:
            text = f.read_text(encoding='utf-8')
        except UnicodeDecodeError:
            # Fallback: try cp950 (Windows Chinese default) then latin-1
            for enc in ('cp950', 'cp1252', 'latin-1'):
                try:
                    text = f.read_text(encoding=enc)
                    print('warning: {0} decoded as {1} (not utf-8)'.format(f, enc),
                          file=sys.stderr)
                    break
                except UnicodeDecodeError:
                    continue
            else:
                print('error: cannot decode {0}'.format(f), file=sys.stderr)
                continue
        for u in extract_from_text(text):
            if u not in all_seen:
                all_seen.add(u)
                ordered.append(u)
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    for u in ordered:
        print(u)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
