"""Environment checks for setup step 1 (task 2.3) and LINE.exe identity for trust bootstrap (D-11).

Checks report plain-language messages; codes are for logs. Claude Code is checked by
capability (``claude --help`` lists the isolation flags), not by version number —
Phase 0 found 2.1.272 already supports every flag the analysis process needs.
"""

from __future__ import annotations

import hashlib
import json
import os
import re
import shutil
import subprocess
from pathlib import Path
from typing import Any, Callable

from .messages import humanize

REQUIRED_CLAUDE_FLAGS: tuple[str, ...] = (
    "--tools", "--strict-mcp-config", "--safe-mode", "--no-session-persistence", "--no-chrome",
)
MIN_NODE_MAJOR = 24
_TIMEOUT_S = 30
_SIGNATURE_SCRIPT = (
    "$ErrorActionPreference='Stop';"
    "[Console]::OutputEncoding=[Text.Encoding]::UTF8;"
    "$p=$env:DA_TARGET_EXE;"
    "$s=Get-AuthenticodeSignature -LiteralPath $p;"
    "$v=(Get-Item -LiteralPath $p).VersionInfo;"
    "[pscustomobject]@{Status=[string]$s.Status;"
    "Subject=[string]$s.SignerCertificate.Subject;"
    "Thumbprint=[string]$s.SignerCertificate.Thumbprint;"
    "FileVersion=[string]$v.FileVersion} | ConvertTo-Json -Compress"
)

Runner = Callable[..., subprocess.CompletedProcess]


def _which(name: str) -> str | None:
    return shutil.which(name)


def _run(runner: Runner, cmd: list[str], **kwargs: Any) -> subprocess.CompletedProcess | None:
    try:
        # Windows consoles default to the OEM code page (cp950 on zh-TW); child output is UTF-8.
        return runner(cmd, capture_output=True, text=True, encoding="utf-8", errors="replace",
                      timeout=_TIMEOUT_S, **kwargs)
    except (OSError, subprocess.TimeoutExpired):
        return None


def _check(name: str, ok: bool, code: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"name": name, "ok": ok, "code": None if ok else code,
            "message": "" if ok else humanize(code), **extra}


def parse_node_major(text: str) -> int | None:
    match = re.match(r"\s*v?(\d+)\.", text or "")
    return int(match.group(1)) if match else None


def check_claude(runner: Runner) -> dict[str, Any]:
    exe = _which("claude")
    proc = _run(runner, [exe, "--help"]) if exe else None
    if proc is None:
        return _check("claude", False, "CLAUDE_FLAGS_MISSING", missing=list(REQUIRED_CLAUDE_FLAGS))
    missing = [f for f in REQUIRED_CLAUDE_FLAGS if not re.search(rf"(?<![\w-]){re.escape(f)}\b", proc.stdout)]
    return _check("claude", not missing, "CLAUDE_FLAGS_MISSING", missing=missing)


def check_node(runner: Runner) -> dict[str, Any]:
    exe = _which("node")
    proc = _run(runner, [exe, "--version"]) if exe else None
    if proc is None or proc.returncode != 0:
        return _check("node", False, "NODE_MISSING")
    major = parse_node_major(proc.stdout)
    return _check("node", major is not None and major >= MIN_NODE_MAJOR, "NODE_TOO_OLD", major=major)


def check_uv(runner: Runner) -> dict[str, Any]:
    exe = _which("uv")
    proc = _run(runner, [exe, "--version"]) if exe else None
    return _check("uv", proc is not None and proc.returncode == 0, "UV_MISSING")


def check_line_running(runner: Runner) -> dict[str, Any]:
    proc = _run(runner, ["tasklist", "/FI", "IMAGENAME eq LINE.exe", "/FO", "CSV", "/NH"])
    running = proc is not None and proc.returncode == 0 and '"LINE.exe"' in proc.stdout
    return _check("line_running", running, "LINE_NOT_RUNNING")


def check_environment(*, runner: Runner = subprocess.run, is_windows: bool | None = None) -> dict[str, Any]:
    """Run every setup-step-1 check. LINE not running is reported but Outlook can continue."""
    windows = os.name == "nt" if is_windows is None else is_windows
    if not windows:
        checks = [_check("platform", False, "NOT_WINDOWS")]
        return {"ok": False, "checks": checks}
    checks = [_check("platform", True), check_claude(runner), check_node(runner),
              check_uv(runner), check_line_running(runner)]
    return {"ok": all(c["ok"] for c in checks), "checks": checks}


def parse_org(subject: str) -> str | None:
    """Return the ``O=`` component of an X.500 subject string (handles quoted values)."""
    match = re.search(r'(?:^|,\s*)O=("(?:[^"]|"")*"|[^,]*)', subject or "")
    if not match:
        return None
    value = match.group(1).strip()
    if value.startswith('"') and value.endswith('"'):
        value = value[1:-1].replace('""', '"')
    return value or None


def sha256_file(path: Path) -> str:
    digest = hashlib.sha256()
    with Path(path).open("rb") as fh:
        for chunk in iter(lambda: fh.read(1 << 20), b""):
            digest.update(chunk)
    return digest.hexdigest()


def default_line_exe() -> Path:
    base = os.environ.get("LOCALAPPDATA") or str(Path.home() / "AppData" / "Local")
    return Path(base) / "LINE" / "bin" / "current" / "LINE.exe"


def read_line_exe_info(exe: Path, *, runner: Runner = subprocess.run) -> dict[str, Any]:
    """Version, size, SHA-256 and Authenticode signer of ``exe`` (plan D-11).

    The path is passed to PowerShell through an environment variable so it is never
    interpolated into script text.
    """
    exe = Path(exe)
    env = {**os.environ, "DA_TARGET_EXE": str(exe)}
    proc = _run(runner, ["powershell.exe", "-NoProfile", "-NonInteractive", "-Command", _SIGNATURE_SCRIPT],
                env=env)
    sig: dict[str, Any] = {}
    if proc is not None and proc.returncode == 0:
        try:
            parsed = json.loads(proc.stdout or "{}")
            sig = parsed if isinstance(parsed, dict) else {}
        except json.JSONDecodeError:
            sig = {}
    subject = str(sig.get("Subject") or "")
    return {
        "path": str(exe),
        "version": sig.get("FileVersion") or None,
        "bytes": exe.stat().st_size,
        "sha256": sha256_file(exe),
        "signature_status": sig.get("Status") or "Unreadable",
        "signer_subject": subject or None,
        "signer_org": parse_org(subject),
        "thumbprint": sig.get("Thumbprint") or None,
    }
