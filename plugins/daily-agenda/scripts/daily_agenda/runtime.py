"""Runtime environment installation and status (plan D-5, task 2.4).

Each component is installed to a staging directory, verified, then atomically
swapped into the final location.  A failed install preserves the previous version.

All network, subprocess and platform calls are injectable for testing on Linux.
Target platform is Windows x64 native Claude Code.

Decisions (fail-closed):
- Download only ``https`` URLs; reject after redirect if final URL is not ``https``.
- Hash verification: archive SHA-256 checked before extraction; extracted file SHA-256
  checked before swap.  Mismatch = delete staging, return error, no partial state.
- Zip extraction: only named members are extracted; path traversal (``..``, absolute
  paths, drive letters) is rejected before any bytes are written.
- Swap: staging -> final is a directory rename; if the old version exists it is first
  renamed to ``.old-<random>``, then staging is renamed in.  If the second rename fails
  the old version is restored before re-raising.
- ``cua`` is not in the default component list (Phase 5).
- ``npm ci --ignore-scripts`` is the only npm invocation.
"""

from __future__ import annotations

import hashlib
import json
import os
import secrets
import shutil
from pathlib import Path
from typing import Any, Callable
from urllib.parse import urlparse

from .fsutil import read_json
from .messages import humanize

# ---------------------------------------------------------------------------
# Constants
# ---------------------------------------------------------------------------

SQLITE3MC_URL = (
    "https://github.com/utelle/SQLite3MultipleCiphers/releases/download/"
    "v2.5.1/sqlite3mc-2.5.1-sqlite-3.53.4-win64.zip"
)
SQLITE3MC_ARCHIVE_SHA256 = "858c4f2e9e262caca06e6c4fafee62d13dd90c21b649b047f17992bf0402981e"
SQLITE3MC_DLL_SHA256 = "5030decc6d914539e3b9b7e28aa4f6de1e7161dac6fd4b21eb02e6754d9b175e"
SQLITE3MC_DLL_MEMBER = "dll/sqlite3mc_x64.dll"

CUA_URL = (
    "https://github.com/trycua/cua/releases/download/"
    "cua-driver-rs-v0.23.2/cua-driver-rs-0.23.2-windows-x86_64-binary.zip"
)
CUA_ARCHIVE_SHA256 = "27a41831d5dda71082b58154ff87966a9ad8131ce66e8060da2d860558655c13"

DEFAULT_COMPONENTS: tuple[str, ...] = ("python", "sqlite3mc", "line-desktop-mcp")
MAX_DOWNLOAD_BYTES = 200 * 1024 * 1024  # 200 MB

# Type aliases for injectable dependencies
FetchFn = Callable[..., bytes]
RunnerFn = Callable[..., Any]
WhichFn = Callable[[str], str | None]
ArchCheckerFn = Callable[[], dict[str, str]]


# ---------------------------------------------------------------------------
# Exceptions
# ---------------------------------------------------------------------------


class HashMismatchError(ValueError):
    """Downloaded or extracted file's SHA-256 does not match the expected value."""


class ZipUnsafeError(ValueError):
    """ZIP archive contains a member with an unsafe path (traversal / absolute)."""


# ---------------------------------------------------------------------------
# Download helpers
# ---------------------------------------------------------------------------


def _download(
    url: str,
    *,
    staging: Path,
    filename: str,
    expected_hash: str,
    fetch: FetchFn,
    max_bytes: int = MAX_DOWNLOAD_BYTES,
) -> Path:
    """Download ``url`` into ``staging/filename``, verify SHA-256.

    Raises HashMismatchError if the hash does not match.
    The file is deleted on mismatch — no unverified content remains.
    """
    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"only https URLs are accepted, got {parsed.scheme}://{parsed.netloc}")

    staging.mkdir(parents=True, exist_ok=True)
    dest = staging / filename

    data = fetch(url, timeout=120, max_bytes=max_bytes)

    # Hash before writing — no unverified bytes touch disk on mismatch
    actual_hash = hashlib.sha256(data).hexdigest()
    if actual_hash != expected_hash:
        raise HashMismatchError(
            f"SHA-256 mismatch for {filename}: expected {expected_hash}, got {actual_hash}"
        )

    dest.write_bytes(data)
    return dest


# ---------------------------------------------------------------------------
# Zip extraction helpers
# ---------------------------------------------------------------------------


def _safe_member(name: str) -> bool:
    """Return True if ``name`` is safe to extract (no traversal / absolute / drive)."""
    if not name:
        return False
    # Normalize separators
    normalized = name.replace("\\", "/")
    # Reject absolute paths
    if normalized.startswith("/"):
        return False
    # Reject drive letters (e.g. C:)
    if ":" in normalized:
        return False
    # Reject any component that is ".."
    parts = normalized.split("/")
    if ".." in parts:
        return False
    return True


def _extract_member(archive: Path, member_name: str, dest: Path, *, max_size: int = 64 * 1024 * 1024) -> None:
    """Extract exactly one member from a ZIP, writing to ``dest``.

    Validates the member name for safety before extraction.
    Raises ZipUnsafeError if the member path is unsafe.
    Raises KeyError if the member does not exist in the archive.
    """
    import zipfile

    if not _safe_member(member_name):
        raise ZipUnsafeError(f"unsafe member name: {member_name!r}")

    with zipfile.ZipFile(archive, "r") as zf:
        info = zf.getinfo(member_name)
        if info.file_size > max_size:
            raise ValueError(f"member {member_name} is {info.file_size} bytes, exceeds {max_size}")
        dest.parent.mkdir(parents=True, exist_ok=True)
        with zf.open(info) as src, dest.open("wb") as dst:
            shutil.copyfileobj(src, dst)


# ---------------------------------------------------------------------------
# Directory swap (staging <-> final)
# ---------------------------------------------------------------------------


def _swap_in(staging: Path, final: Path) -> None:
    """Atomically swap ``staging`` into ``final``, preserving old version on failure.

    1. If ``final`` exists, rename it to ``final.old-<random>``.
    2. Rename ``staging`` to ``final``.
    3. If step 2 fails, restore ``final`` from the old backup and re-raise.
    4. On success, delete the old backup.
    """
    old_backup: Path | None = None

    if final.exists():
        old_backup = final.with_name(f"{final.name}.old-{secrets.token_hex(4)}")
        os.rename(final, old_backup)

    try:
        os.rename(staging, final)
    except BaseException:
        # Restore old version
        if old_backup is not None and old_backup.exists():
            os.rename(old_backup, final)
        raise

    # Clean up old backup
    if old_backup is not None and old_backup.exists():
        shutil.rmtree(old_backup, ignore_errors=True)


def _cleanup_staging(runtime_dir: Path, component: str) -> None:
    """Remove any leftover staging directories for a component."""
    if not runtime_dir.exists():
        return
    prefix = f"{component}.staging-"
    for d in runtime_dir.iterdir():
        if d.name.startswith(prefix) and d.is_dir():
            shutil.rmtree(d, ignore_errors=True)


# ---------------------------------------------------------------------------
# Vendor hash (stamp)
# ---------------------------------------------------------------------------


def _vendor_hash(vendor_dir: Path) -> str:
    """Compute a stable hash of a vendor directory, excluding ``node_modules`` and ``__pycache__``."""
    entries: list[tuple[str, str]] = []
    excluded_components = {"node_modules", "__pycache__"}

    for f in sorted(vendor_dir.rglob("*")):
        if not f.is_file():
            continue
        rel = f.relative_to(vendor_dir)
        # Skip if any component of the path is in the excluded set
        if any(part in excluded_components for part in rel.parts):
            continue
        content_hash = hashlib.sha256(f.read_bytes()).hexdigest()
        entries.append((rel.as_posix(), content_hash))

    # Sort for stability
    entries.sort()

    digest = hashlib.sha256()
    for rel_posix, content_hash in entries:
        digest.update(f"{rel_posix}\0{content_hash}\n".encode("utf-8"))
    return digest.hexdigest()


# ---------------------------------------------------------------------------
# Per-component installers
# ---------------------------------------------------------------------------


def install_sqlite3mc(
    h: Path,
    *,
    fetch: FetchFn,
    archive_sha256: str = SQLITE3MC_ARCHIVE_SHA256,
    dll_sha256: str = SQLITE3MC_DLL_SHA256,
) -> dict[str, Any]:
    """Download and install SQLite3MultipleCiphers DLL."""
    h = Path(h)
    runtime_dir = h / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    component = "sqlite3mc"
    staging = runtime_dir / f"{component}.staging-{secrets.token_hex(4)}"

    try:
        # Download and verify archive
        archive_path = _download(
            SQLITE3MC_URL,
            staging=staging,
            filename="sqlite3mc.zip",
            expected_hash=archive_sha256,
            fetch=fetch,
        )

        # Extract only the target DLL
        dll_dest = staging / "sqlite3mc_x64.dll"
        try:
            _extract_member(archive_path, SQLITE3MC_DLL_MEMBER, dll_dest)
        except (KeyError, ZipUnsafeError) as exc:
            shutil.rmtree(staging, ignore_errors=True)
            return _component_result(False, "RUNTIME_INSTALL_FAILED", detail=str(exc))

        # Verify DLL hash
        actual_dll_hash = hashlib.sha256(dll_dest.read_bytes()).hexdigest()
        if actual_dll_hash != dll_sha256:
            shutil.rmtree(staging, ignore_errors=True)
            return _component_result(False, "RUNTIME_HASH_MISMATCH",
                                     detail=f"DLL SHA-256 mismatch: expected {dll_sha256}, got {actual_dll_hash}")

        # Remove the archive from staging (only keep the DLL)
        archive_path.unlink(missing_ok=True)

        # Write stamp
        stamp = {"dll_sha256": actual_dll_hash, "version": "2.5.1"}
        (staging / ".daily-agenda-stamp.json").write_text(
            json.dumps(stamp, ensure_ascii=False), encoding="utf-8")

        # Swap in
        final = runtime_dir / component
        _swap_in(staging, final)
        return _component_result(True)

    except HashMismatchError:
        shutil.rmtree(staging, ignore_errors=True)
        return _component_result(False, "RUNTIME_HASH_MISMATCH")
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return _component_result(False, "RUNTIME_INSTALL_FAILED", detail=str(exc))
    finally:
        _cleanup_staging(runtime_dir, component)


def install_line_desktop_mcp(
    h: Path,
    *,
    plugin_root: Path,
    runner: RunnerFn,
    which: WhichFn,
) -> dict[str, Any]:
    """Copy vendored line-desktop-mcp and run ``npm ci --ignore-scripts``."""
    h = Path(h)
    runtime_dir = h / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    component = "line-desktop-mcp"
    staging = runtime_dir / f"{component}.staging-{secrets.token_hex(4)}"

    vendor_src = plugin_root / "vendor" / "line-desktop-mcp"

    try:
        # Copy vendor to staging
        shutil.copytree(vendor_src, staging, dirs_exist_ok=False)

        # Find npm (npm.cmd on Windows)
        npm_exe = which("npm.cmd") or which("npm")
        if not npm_exe:
            shutil.rmtree(staging, ignore_errors=True)
            return _component_result(False, "RUNTIME_INSTALL_FAILED", detail="npm not found")

        # Run npm ci --ignore-scripts
        proc = runner(
            [npm_exe, "ci", "--ignore-scripts", "--no-audit", "--no-fund"],
            cwd=str(staging),
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        if proc.returncode != 0:
            shutil.rmtree(staging, ignore_errors=True)
            return _component_result(False, "RUNTIME_INSTALL_FAILED",
                                     detail=f"npm ci failed (rc={proc.returncode})")

        # Write stamp with vendor hash (excluding node_modules)
        vendor_hash = _vendor_hash(vendor_src)
        stamp = {"vendor_hash": vendor_hash}
        (staging / ".daily-agenda-stamp.json").write_text(
            json.dumps(stamp, ensure_ascii=False), encoding="utf-8")

        # Swap in
        final = runtime_dir / component
        _swap_in(staging, final)
        return _component_result(True)

    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return _component_result(False, "RUNTIME_INSTALL_FAILED", detail=str(exc))
    finally:
        _cleanup_staging(runtime_dir, component)


def install_python(
    h: Path,
    *,
    plugin_root: Path,
    runner: RunnerFn,
    which: WhichFn,
    arch_checker: ArchCheckerFn,
) -> dict[str, Any]:
    """Create a Python 3.12 venv and install requirements.

    ``plugin_root`` is required — we need vendor's ``requirements.txt``.
    A missing requirements file is fail-closed (venv without cryptography/Pillow is useless).
    """
    h = Path(h)
    runtime_dir = h / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    component = "python"
    staging = runtime_dir / f"{component}.staging-{secrets.token_hex(4)}"

    try:
        # Check platform (host check — target platform is always Windows AMD64)
        arch_info = arch_checker()
        if arch_info.get("arch") != "64bit":
            return _component_result(False, "RUNTIME_PLATFORM_MISMATCH",
                                     detail=f"arch={arch_info.get('arch')}")

        # Verify requirements.txt exists (fail-closed)
        req_file = (plugin_root / "vendor" / "line-desktop-mcp" / "src" /
                    "extensions" / "python" / "requirements.txt")
        if not req_file.exists():
            return _component_result(False, "RUNTIME_INSTALL_FAILED",
                                     detail=f"requirements.txt not found at {req_file}")

        # Find uv
        uv_exe = which("uv")
        if not uv_exe:
            return _component_result(False, "RUNTIME_INSTALL_FAILED", detail="uv not found")

        staging.mkdir(parents=True, exist_ok=True)
        venv_dir = staging / ".venv"

        # Create venv
        proc = runner(
            [uv_exe, "venv", "--python", "3.12", str(venv_dir)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=120,
        )
        if proc.returncode != 0:
            shutil.rmtree(staging, ignore_errors=True)
            return _component_result(False, "RUNTIME_INSTALL_FAILED",
                                     detail="uv venv failed")

        # Find venv python (Windows layout unconditionally — target platform)
        venv_python = venv_dir / "Scripts" / "python.exe"
        if not venv_python.exists():
            shutil.rmtree(staging, ignore_errors=True)
            return _component_result(False, "RUNTIME_INSTALL_FAILED",
                                     detail="venv python.exe not created")

        # Install requirements (fail-closed — already verified existence above)
        proc = runner(
            [uv_exe, "pip", "install", "--python", str(venv_python),
             "-r", str(req_file)],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            timeout=300,
        )
        if proc.returncode != 0:
            shutil.rmtree(staging, ignore_errors=True)
            return _component_result(False, "RUNTIME_INSTALL_FAILED",
                                     detail="uv pip install failed")

        # Swap in
        final = runtime_dir / component
        _swap_in(staging, final)
        return _component_result(True)

    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return _component_result(False, "RUNTIME_INSTALL_FAILED", detail=str(exc))
    finally:
        _cleanup_staging(runtime_dir, component)


def install_cua(
    h: Path,
    *,
    fetch: FetchFn,
    archive_sha256: str = CUA_ARCHIVE_SHA256,
) -> dict[str, Any]:
    """Download and install CUA Driver (Phase 5; version pending Phase 0 0.4 confirmation)."""
    h = Path(h)
    runtime_dir = h / "runtime"
    runtime_dir.mkdir(parents=True, exist_ok=True)
    component = "cua"
    staging = runtime_dir / f"{component}.staging-{secrets.token_hex(4)}"

    try:
        archive_path = _download(
            CUA_URL,
            staging=staging,
            filename="cua-driver.zip",
            expected_hash=archive_sha256,
            fetch=fetch,
        )

        # Extract all members after validation
        import zipfile
        with zipfile.ZipFile(archive_path, "r") as zf:
            for info in zf.infolist():
                if not _safe_member(info.filename):
                    shutil.rmtree(staging, ignore_errors=True)
                    return _component_result(False, "RUNTIME_ZIP_UNSAFE",
                                             detail=f"unsafe member: {info.filename!r}")

            # All safe; extract
            for info in zf.infolist():
                if info.is_dir():
                    continue
                dest = staging / info.filename.replace("\\", "/")
                dest.parent.mkdir(parents=True, exist_ok=True)
                with zf.open(info) as src, dest.open("wb") as dst:
                    shutil.copyfileobj(src, dst)

        # Find cua-driver.exe and record its hash
        exe_candidates = list(staging.rglob("cua-driver.exe"))
        if not exe_candidates:
            # Try without .exe
            exe_candidates = list(staging.rglob("cua-driver"))
        if not exe_candidates:
            shutil.rmtree(staging, ignore_errors=True)
            return _component_result(False, "RUNTIME_INSTALL_FAILED",
                                     detail="cua-driver executable not found in archive")

        exe_path = exe_candidates[0]
        exe_hash = hashlib.sha256(exe_path.read_bytes()).hexdigest()

        # Remove archive
        archive_path.unlink(missing_ok=True)

        # Write stamp
        stamp = {"exe_sha256": exe_hash, "version": "0.23.2"}
        (staging / ".daily-agenda-stamp.json").write_text(
            json.dumps(stamp, ensure_ascii=False), encoding="utf-8")

        # Swap in
        final = runtime_dir / component
        _swap_in(staging, final)
        return _component_result(True)

    except HashMismatchError:
        shutil.rmtree(staging, ignore_errors=True)
        return _component_result(False, "RUNTIME_HASH_MISMATCH")
    except Exception as exc:
        shutil.rmtree(staging, ignore_errors=True)
        return _component_result(False, "RUNTIME_INSTALL_FAILED", detail=str(exc))
    finally:
        _cleanup_staging(runtime_dir, component)


# ---------------------------------------------------------------------------
# Orchestrator
# ---------------------------------------------------------------------------


def install(
    h: Path,
    *,
    plugin_root: Path,
    components: list[str] | None = None,
    fetch: FetchFn | None = None,
    runner: RunnerFn | None = None,
    which: WhichFn | None = None,
    arch_checker: ArchCheckerFn | None = None,
) -> dict[str, Any]:
    """Install runtime components.  Each component installs independently.

    Returns ``{"ok": bool, "components": {name: result}}``.
    """
    import subprocess as sp

    comps = components if components is not None else list(DEFAULT_COMPONENTS)
    _fetch = fetch or _default_fetch
    _runner = runner or sp.run
    _which = which or shutil.which
    _arch = arch_checker or _default_arch_checker

    results: dict[str, dict[str, Any]] = {}

    for comp in comps:
        if comp == "python":
            results[comp] = install_python(
                h, plugin_root=plugin_root, runner=_runner, which=_which, arch_checker=_arch)
        elif comp == "sqlite3mc":
            results[comp] = install_sqlite3mc(h, fetch=_fetch)
        elif comp == "line-desktop-mcp":
            results[comp] = install_line_desktop_mcp(
                h, plugin_root=plugin_root, runner=_runner, which=_which)
        elif comp == "cua":
            results[comp] = install_cua(h, fetch=_fetch)
        else:
            results[comp] = _component_result(False, "RUNTIME_INSTALL_FAILED",
                                              detail=f"unknown component: {comp}")

    return {"ok": all(r["ok"] for r in results.values()), "components": results}


# ---------------------------------------------------------------------------
# Status
# ---------------------------------------------------------------------------


def status(h: Path, plugin_root: Path) -> dict[str, Any]:
    """Check the status of each runtime component."""
    h = Path(h)
    runtime_dir = h / "runtime"
    comps: dict[str, dict[str, Any]] = {}

    # Python
    python_dir = runtime_dir / "python"
    python_exe = python_dir / ".venv" / "Scripts" / "python.exe"
    comps["python"] = {
        "installed": python_exe.exists(),
        "path": str(python_exe) if python_exe.exists() else None,
    }

    # SQLite3MC — verify against the pinned constant, not just the stamp (if an
    # attacker can write the DLL they can write the stamp too)
    sqlite_dir = runtime_dir / "sqlite3mc"
    dll_path = sqlite_dir / "sqlite3mc_x64.dll"
    sqlite_status: dict[str, Any] = {
        "installed": dll_path.exists(),
        "path": str(dll_path) if dll_path.exists() else None,
        "tampered": False,
    }
    if dll_path.exists():
        actual_hash = hashlib.sha256(dll_path.read_bytes()).hexdigest()
        if actual_hash != SQLITE3MC_DLL_SHA256:
            sqlite_status["tampered"] = True
            sqlite_status["code"] = "RUNTIME_HASH_MISMATCH"
            sqlite_status["message"] = humanize("RUNTIME_HASH_MISMATCH")
    comps["sqlite3mc"] = sqlite_status

    # line-desktop-mcp
    mcp_dir = runtime_dir / "line-desktop-mcp"
    mcp_installed = mcp_dir.exists() and (mcp_dir / "package.json").exists()
    mcp_status: dict[str, Any] = {
        "installed": mcp_installed,
        "path": str(mcp_dir) if mcp_installed else None,
    }
    if mcp_installed:
        stamp_path = mcp_dir / ".daily-agenda-stamp.json"
        stamp = read_json(stamp_path, {})
        stored_hash = stamp.get("vendor_hash", "")
        vendor_src = plugin_root / "vendor" / "line-desktop-mcp"
        if vendor_src.exists():
            current_hash = _vendor_hash(vendor_src)
            if current_hash != stored_hash:
                mcp_status["code"] = "RUNTIME_OUTDATED"
                mcp_status["message"] = humanize("RUNTIME_OUTDATED")
    comps["line-desktop-mcp"] = mcp_status

    # CUA (optional)
    cua_dir = runtime_dir / "cua"
    comps["cua"] = {
        "installed": cua_dir.exists(),
        "path": str(cua_dir) if cua_dir.exists() else None,
    }

    def _comp_ok(c: dict[str, Any]) -> bool:
        return c.get("installed", False) and not c.get("tampered", False) and not c.get("code")

    return {
        "ok": all(_comp_ok(comps[c]) for c in DEFAULT_COMPONENTS),
        "components": comps,
    }


# ---------------------------------------------------------------------------
# env_for_line
# ---------------------------------------------------------------------------


def env_for_line(h: Path) -> dict[str, str]:
    """Build the environment variables dict for the LINE collector / action agent.

    Does NOT include ``LINE_MCP_EXTENSIONS`` — that is only set by the action agent
    for its own subprocess (D-3).
    """
    h = Path(h)
    runtime_dir = h / "runtime"
    env: dict[str, str] = {}

    # Python
    python_exe = runtime_dir / "python" / ".venv" / "Scripts" / "python.exe"
    if python_exe.exists():
        env["LINE_MCP_PYTHON"] = str(python_exe)

    # SQLite3MC DLL
    dll = runtime_dir / "sqlite3mc" / "sqlite3mc_x64.dll"
    if dll.exists():
        env["LINE_MCP_SQLITE3MC_DLL"] = str(dll)

    # Trusted builds (only if file exists)
    trusted = h / "trusted_builds.json"
    if trusted.exists():
        env["LINE_MCP_TRUSTED_BUILDS"] = str(trusted)

    # Main database from config
    config = read_json(h / "config.json", {})
    line_config = config.get("line", {})
    main_db = line_config.get("main_database")
    if main_db:
        env["LINE_MCP_MAIN_DATABASE"] = main_db

    return env


# ---------------------------------------------------------------------------
# Default implementations (production)
# ---------------------------------------------------------------------------


def _default_fetch(url: str, *, timeout: int = 120, max_bytes: int = MAX_DOWNLOAD_BYTES) -> bytes:
    """Production fetch via urllib (https only)."""
    import urllib.request

    parsed = urlparse(url)
    if parsed.scheme != "https":
        raise ValueError(f"only https URLs accepted, got {parsed.scheme}")

    req = urllib.request.Request(url)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        # Verify final URL is still https (after redirects)
        final_url = resp.geturl()
        if not final_url.startswith("https://"):
            raise ValueError(f"redirect landed on non-https URL: {final_url}")
        # Check Content-Length header if available
        cl = resp.headers.get("Content-Length")
        if cl and int(cl) > max_bytes:
            raise ValueError(f"Content-Length {cl} exceeds limit {max_bytes}")
        # Stream and accumulate
        chunks: list[bytes] = []
        total = 0
        while True:
            chunk = resp.read(1 << 20)  # 1 MB
            if not chunk:
                break
            total += len(chunk)
            if total > max_bytes:
                raise ValueError(f"download exceeds {max_bytes} bytes")
            chunks.append(chunk)
    return b"".join(chunks)


def _default_arch_checker() -> dict[str, str]:
    """Check the platform architecture."""
    import platform
    return {
        "arch": platform.architecture()[0],
        "system": platform.system(),
        "machine": platform.machine(),
    }


# ---------------------------------------------------------------------------
# Internal helpers
# ---------------------------------------------------------------------------


def _component_result(ok: bool, code: str | None = None, **extra: Any) -> dict[str, Any]:
    return {"ok": ok, "code": code, "message": humanize(code) if code else "", **extra}
