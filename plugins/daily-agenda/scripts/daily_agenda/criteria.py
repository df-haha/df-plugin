"""Criteria version management — list, read, save, restore.

Criteria versions live in ``<home>/criteria/v<N>.md`` (only increments).
"""

from __future__ import annotations

import re
from pathlib import Path

from .fsutil import atomic_write_text


def _criteria_dir(h: Path) -> Path:
    return Path(h) / "criteria"


def list_versions(h: Path) -> list[int]:
    """Return sorted list of available criteria version numbers."""
    d = _criteria_dir(h)
    if not d.is_dir():
        return []
    versions: list[int] = []
    for p in d.iterdir():
        m = re.match(r"^v(\d+)\.md$", p.name)
        if m:
            versions.append(int(m.group(1)))
    return sorted(versions)


def latest_version(h: Path) -> int:
    """Return the latest version number, or 0 if none."""
    vs = list_versions(h)
    return vs[-1] if vs else 0


def read_version(h: Path, version: int) -> str | None:
    """Read criteria text for a specific version. Returns None if not found."""
    path = _criteria_dir(h) / f"v{version}.md"
    try:
        return path.read_text(encoding="utf-8")
    except FileNotFoundError:
        return None


def read_latest(h: Path) -> str | None:
    """Read the latest criteria text."""
    v = latest_version(h)
    if v == 0:
        return None
    return read_version(h, v)


def save(h: Path, text: str) -> int:
    """Save new criteria version; return the new version number."""
    d = _criteria_dir(h)
    d.mkdir(parents=True, exist_ok=True)
    v = latest_version(h) + 1
    atomic_write_text(d / f"v{v}.md", text)
    return v


def restore(h: Path, version: int) -> int:
    """Copy an older version as the next version number. Returns new version."""
    text = read_version(h, version)
    if text is None:
        raise FileNotFoundError(f"criteria version {version} not found")
    return save(h, text)
