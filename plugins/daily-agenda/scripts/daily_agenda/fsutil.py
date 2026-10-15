"""Atomic file helpers (write temp in same directory → fsync → os.replace)."""

from __future__ import annotations

import json
import os
import tempfile
from pathlib import Path
from typing import Any


def atomic_write_bytes(path: Path, data: bytes) -> None:
    """Atomically replace ``path`` with ``data``."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(prefix=f".{path.name}.", suffix=".tmp", dir=path.parent)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise
    fsync_dir(path.parent)


def atomic_write_text(path: Path, text: str) -> None:
    atomic_write_bytes(path, text.encode("utf-8"))


def atomic_write_json(path: Path, value: Any) -> None:
    atomic_write_text(path, json.dumps(value, ensure_ascii=False, indent=2) + "\n")


def jsonl_line(obj: Any) -> str:
    """Serialize *obj* as a single JSONL line, escaping U+2028/U+2029.

    ``json.dumps(ensure_ascii=False)`` leaves U+2028 (LINE SEPARATOR) and
    U+2029 (PARAGRAPH SEPARATOR) raw.  Since ``str.splitlines()`` and some
    models treat them as line breaks, a raw occurrence in untrusted text would
    create a fake JSONL record.  We escape them after serialization.
    """
    line = json.dumps(obj, ensure_ascii=False)
    return line.replace("\u2028", "\\u2028").replace("\u2029", "\\u2029")


def read_json(path: Path, default: Any = None) -> Any:
    try:
        return json.loads(Path(path).read_text(encoding="utf-8"))
    except FileNotFoundError:
        return default


def fsync_dir(directory: Path) -> None:
    """Best-effort directory fsync (no-op on Windows, where it is unsupported)."""
    if os.name == "nt":
        return
    fd = os.open(directory, os.O_RDONLY)
    try:
        os.fsync(fd)
    finally:
        os.close(fd)
