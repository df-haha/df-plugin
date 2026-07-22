#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Locate the latest deep-research-ryan-* skill directory using semantic
YYMMDD[-N] version ordering (NOT lexicographic — `260625-10` > `260625-2`,
which lexicographic sort would flip).

Cross-platform replacement for `ls -d ~/.claude/skills/deep-research-ryan-* |
sort -V | tail -1` (macOS/Linux only; `sort -V` also missing on macOS default).

Note: when deep-research-ryan is installed via the plugin marketplace
(CLAUDE_PLUGIN_ROOT form), use CLAUDE_PLUGIN_ROOT directly to locate the skill
directory — this script is the fallback for standalone (~/.claude/skills/)
installations only.

Usage:
    python find_skill_dir.py                  # prints latest matching skill abs path
    python find_skill_dir.py --list           # print all candidates sorted, newest last
    python find_skill_dir.py --prefix <name>  # override prefix (default: deep-research-ryan-)

Exit code:
    0 on success (path printed)
    1 no candidates found
"""
from __future__ import annotations
from __future__ import print_function
import os
import platform
import re
import sys
from pathlib import Path

DEFAULT_PREFIX = 'deep-research-ryan-'

# YYMMDD or YYMMDD-N (N up to 3 digits observed in the wild)
# lessons #22 flagged N can be >= 2; blueprint adversarial hole #3 warns
# lex sort would put `260625-10` < `260625-2`.
VERSION_RE = re.compile(r'^(\d{6})(?:-(\d+))?(?:-win)?$')


def parse_version(name: str, prefix: str) -> tuple[int, int, int] | None:
    """Return sortable tuple (yymmdd_int, n_int, is_win_int) or None.

    is_win_int lives in the tuple so ties between `260625` and `260625-win`
    (same YYMMDD, same N) are broken deterministically. Whether -win wins
    is decided by find_candidates() based on the runtime platform:
    on Windows, -win is preferred (is_win_int=1 sorts later); on macOS/Linux,
    the original mac version wins (is_win_int=0 sorts later — via key flip).
    """
    if not name.startswith(prefix):
        return None
    tail = name[len(prefix):]
    m = VERSION_RE.match(tail)
    if not m:
        return None
    yymmdd = int(m.group(1))
    n = int(m.group(2)) if m.group(2) else 0
    is_win = 1 if name.endswith('-win') else 0
    return (yymmdd, n, is_win)


def find_candidates(
    skills_dir: Path,
    prefix: str,
    prefer_win: bool | None = None,
) -> list[tuple[tuple[int, int, int], Path]]:
    """List candidates sorted so the "best" one is last.

    Ordering: (YYMMDD, N, platform_preference).
    - YYMMDD / N: newer wins.
    - platform_preference: on Windows we prefer the -win port; on POSIX we
      prefer the original mac version. This resolves the ambiguity when both
      `deep-research-ryan` and `deep-research-ryan` coexist
      (a common transition state — mac dogfood + Windows port side-by-side).
      Without this the tie was broken by filesystem iterdir order (non
      deterministic), which on Windows could silently return the mac-only
      path and feed subagents with POSIX-shell prompts that then fail.
    """
    if not skills_dir.is_dir():
        return []
    if prefer_win is None:
        prefer_win = platform.system() == 'Windows'
    out = []
    for child in skills_dir.iterdir():
        if not child.is_dir():
            continue
        v = parse_version(child.name, prefix)
        if v is None:
            continue
        # Sanity check: needs SKILL.md
        if not (child / 'SKILL.md').exists():
            continue
        out.append((v, child))

    def _key(item):
        yymmdd, n, is_win = item[0]
        # On Windows: is_win=1 > is_win=0, so use is_win directly.
        # On POSIX: prefer is_win=0, so flip via (1 - is_win).
        platform_rank = is_win if prefer_win else (1 - is_win)
        return (yymmdd, n, platform_rank)

    out.sort(key=_key)  # best last
    return out


def main(argv: list[str]) -> int:
    prefix = DEFAULT_PREFIX
    list_mode = False
    i = 1
    while i < len(argv):
        a = argv[i]
        if a == '--list':
            list_mode = True
        elif a == '--prefix':
            i += 1
            prefix = argv[i]
        else:
            print('unknown arg: {0}'.format(a), file=sys.stderr)
            return 2
        i += 1

    skills_dir = Path(os.path.expanduser('~')) / '.claude' / 'skills'
    cands = find_candidates(skills_dir, prefix)
    if not cands:
        print('no {0}* skill found under {1}'.format(prefix, skills_dir),
              file=sys.stderr)
        return 1

    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass

    if list_mode:
        for v, p in cands:
            yymmdd, n, is_win = v
            tag = '-win' if is_win else ''
            print('{0}-{1}{2}\t{3}'.format(yymmdd, n, tag, p))
    else:
        # print absolute path (str; on Windows uses backslashes, on POSIX forward)
        print(str(cands[-1][1].resolve()))
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
