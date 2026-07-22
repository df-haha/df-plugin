#!/usr/bin/env python
# -*- coding: utf-8 -*-
"""
Diagnostic entry (doctor) for deep-research-ryan.

Deep-research does NOT install hooks, subagents, or MCP servers into user
settings — those are optional and user-managed. This script only:

  1. Verifies Python / Node.js / Claude Code CLI are present
  2. Reports which MCP servers are configured in ~/.claude.json (Exa / Tavily /
     Playwright)
  3. Optionally sets up UTF-8 hint on Windows (env var PYTHONIOENCODING=utf-8
     suggested; not enforced)
  4. Smoke-tests bundled scripts (nonce.py, extract_urls.py, find_skill_dir.py)

This script is purely diagnostic — it installs nothing. Re-run after any
environment change to confirm prerequisites are in order.

Usage:
    python doctor.py             # normal
    python doctor.py --json      # machine-readable summary

Exit code 0 always (script is diagnostic, not blocking).
"""
from __future__ import annotations
from __future__ import print_function
import json
import os
import platform
import shutil
import subprocess
import sys
from pathlib import Path

SKILL_DIR = Path(__file__).resolve().parent.parent
HOME = Path(os.path.expanduser('~'))


def _run(cmd: list[str], timeout: int = 10) -> tuple[int, str, str]:
    """Run a command, return (rc, stdout, stderr) with utf-8 decode."""
    try:
        p = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            encoding='utf-8',
            errors='replace',
            timeout=timeout,
            shell=False,
        )
        return p.returncode, (p.stdout or '').strip(), (p.stderr or '').strip()
    except FileNotFoundError:
        return 127, '', 'command not found'
    except subprocess.TimeoutExpired:
        return 124, '', 'timeout'
    except Exception as e:
        return 1, '', 'error: {0}'.format(e)


def check_python() -> dict[str, object]:
    v = sys.version_info
    ok = v.major == 3 and v.minor >= 9
    return {
        'ok': ok,
        'version': '{0}.{1}.{2}'.format(v.major, v.minor, v.micro),
        'executable': sys.executable,
        'note': 'need Python 3.9+' if not ok else '',
    }


def check_node() -> dict[str, object]:
    which = shutil.which('node')
    if not which:
        return {'ok': False, 'note': 'node not found in PATH; install Node.js LTS'}
    # Use resolved absolute path for consistency with check_claude (defensive:
    # node ships as node.exe on Windows so bare 'node' works, but resolving
    # keeps behaviour uniform and immune to future PATH quirks).
    rc, out, err = _run([which, '--version'])
    return {'ok': rc == 0, 'version': out, 'executable': which, 'note': err}


def check_claude() -> dict[str, object]:
    # Note: on Windows, npm-global Claude Code ships as `claude.cmd` (a batch
    # shim), not `claude.exe`. Python's subprocess with shell=False does NOT
    # search PATHEXT — passing bare 'claude' would FileNotFoundError even when
    # the CLI is installed. We use shutil.which(...) (which DOES search PATHEXT)
    # to resolve the actual `.cmd` / `.exe` / POSIX path first and invoke that.
    which = shutil.which('claude')
    if not which:
        return {'ok': False, 'note': 'claude CLI not found in PATH'}
    # Claude Code CLI cold-start on Windows (cmd.exe → node → main.js) can take
    # 5-15s especially under antivirus scanning; bump timeout to 30s.
    rc, out, err = _run([which, '--version'], timeout=30)
    return {'ok': rc == 0, 'version': out, 'executable': which, 'note': err}


def check_mcp_config() -> dict[str, object]:
    cfg = HOME / '.claude.json'
    result: dict[str, object] = {
        'config_path': str(cfg),
        'exists': cfg.exists(),
        'exa': False,
        'tavily': False,
        'playwright': False,
    }
    if not cfg.exists():
        result['note'] = 'no ~/.claude.json (Windows: %USERPROFILE%\\.claude.json)'
        return result
    try:
        data = json.loads(cfg.read_text(encoding='utf-8'))
    except Exception as e:
        result['note'] = 'cannot parse config: {0}'.format(e)
        return result
    servers = data.get('mcpServers', {}) or {}
    for k in servers.keys():
        kl = k.lower()
        if 'exa' in kl:
            result['exa'] = True
        if 'tavily' in kl:
            result['tavily'] = True
        if 'playwright' in kl:
            result['playwright'] = True
    return result


def smoke_test_scripts() -> dict[str, dict[str, object]]:
    """Run each bundled script in a harmless mode to catch syntax/runtime bugs."""
    scripts_dir = SKILL_DIR / 'scripts'
    results: dict[str, dict[str, object]] = {}
    # nonce.py: just run it; expect 8-char hex string from token_hex(4)
    rc, out, err = _run([sys.executable, str(scripts_dir / 'nonce.py')])
    results['nonce.py'] = {
        'ok': rc == 0 and len(out) == 8 and all(c in '0123456789abcdef' for c in out),
        'output': out,
        'stderr': err,
    }
    # find_skill_dir.py --list: won't fail even if no candidates (returns 1)
    rc, out, err = _run([sys.executable, str(scripts_dir / 'find_skill_dir.py'), '--list'])
    results['find_skill_dir.py'] = {
        'ok': rc in (0, 1),
        'output_lines': len(out.splitlines()),
        'stderr': err,
    }
    # extract_urls.py: run on SKILL.md itself
    skill_md = SKILL_DIR / 'SKILL.md'
    rc, out, err = _run([sys.executable, str(scripts_dir / 'extract_urls.py'), str(skill_md)])
    results['extract_urls.py'] = {
        'ok': rc == 0,
        'url_count': len(out.splitlines()) if out else 0,
        'stderr': err,
    }
    return results


def emit_summary(env: dict[str, object]) -> None:
    if hasattr(sys.stdout, 'reconfigure'):
        try:
            sys.stdout.reconfigure(encoding='utf-8')
        except Exception:
            pass
    print('=' * 60)
    print('deep-research-ryan — diagnostic (doctor.py)')
    print('=' * 60)
    print('Skill dir : {0}'.format(SKILL_DIR))
    print('Platform  : {0} {1}'.format(platform.system(), platform.release()))
    print()

    def row(name: str, r: dict[str, object]) -> None:
        status = 'OK ' if r.get('ok') else 'MISS'
        detail = r.get('version') or r.get('note') or ''
        print('  [{0}] {1:<12} {2}'.format(status, name, detail))

    print('Environment:')
    row('python', env['python'])
    row('node', env['node'])
    row('claude', env['claude'])
    print()
    print('MCP servers (from {0}):'.format(env['mcp']['config_path']))
    if not env['mcp']['exists']:
        print('  (config missing) {0}'.format(env['mcp'].get('note', '')))
    else:
        for k in ('exa', 'tavily', 'playwright'):
            print('  [{0}] {1}'.format('OK ' if env['mcp'][k] else 'MISS', k))
    print()
    print('Bundled script smoke test:')
    for name, r in env['scripts'].items():
        status = 'OK ' if r.get('ok') else 'FAIL'
        extra = r.get('output') or r.get('stderr') or ''
        print('  [{0}] {1:<20} {2}'.format(status, name, str(extra)[:60]))
    print()
    missing = []
    if not env['python']['ok']:
        missing.append('Python 3.9+')
    if not env['node']['ok']:
        missing.append('Node.js (for npx / MCP)')
    if not env['claude']['ok']:
        missing.append('Claude Code CLI')
    if missing:
        print('Missing prerequisites: {0}'.format(', '.join(missing)))
        print('See SKILL.md "Windows 相容性告示" for install steps.')
    else:
        print('All prerequisites present. Trigger the skill from a Claude Code session.')
    print()


def main(argv: list[str]) -> int:
    env: dict[str, object] = {
        'python': check_python(),
        'node': check_node(),
        'claude': check_claude(),
        'mcp': check_mcp_config(),
        'scripts': smoke_test_scripts(),
    }
    if '--json' in argv:
        if hasattr(sys.stdout, 'reconfigure'):
            try:
                sys.stdout.reconfigure(encoding='utf-8')
            except Exception:
                pass
        print(json.dumps(env, indent=2, ensure_ascii=False))
    else:
        emit_summary(env)
    return 0


if __name__ == '__main__':
    sys.exit(main(sys.argv))
