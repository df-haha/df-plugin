"""Portable local-only directories for transient LINE reader state."""
from __future__ import annotations

import os
from pathlib import Path
import re
import stat
import tempfile
import uuid


_REPARSE_ATTRIBUTE = getattr(stat, 'FILE_ATTRIBUTE_REPARSE_POINT', 0x400)


class RuntimePathError(Exception):
    pass


def _is_reparse(info):
    return stat.S_ISLNK(info.st_mode) or bool(getattr(info, 'st_file_attributes', 0) & _REPARSE_ATTRIBUTE)


def _directory_info(path):
    """Return a safe directory stat result, or None only when it is missing."""
    try:
        info = path.lstat()
    except FileNotFoundError:
        return None
    except OSError:
        raise RuntimePathError() from None
    if _is_reparse(info) or not stat.S_ISDIR(info.st_mode):
        raise RuntimePathError()
    return info


def _plain_directory(path):
    """Reject missing, non-directory, and reparse-point path components."""
    if _directory_info(path) is None:
        raise RuntimePathError()


def _existing_components(path):
    """Yield lexical components from the root without resolving reparses."""
    if not path.is_absolute() or not path.anchor:
        raise RuntimePathError()
    current = Path(path.anchor)
    yield current
    for part in path.parts[1:]:
        if part in ('.', '..'):
            raise RuntimePathError()
        current = current / part
        yield current


def _verify_existing_prefix(path):
    """Verify every existing component; return the first missing component."""
    for component in _existing_components(path):
        if _directory_info(component) is None:
            return component
    return None


def _create_checked_children(base, children):
    """Create only fixed children, checking each parent and child around mkdir."""
    if _verify_existing_prefix(base) is not None:
        raise RuntimePathError()
    current = base
    _plain_directory(current)
    for child in children:
        current = current / child
        if _directory_info(current) is None:
            _plain_directory(current.parent)
            try:
                current.mkdir(mode=0o700)
            except FileExistsError:
                pass
            except OSError:
                raise RuntimePathError() from None
            _plain_directory(current)
    return current


def application_runtime_dir(*, create=False):
    """Return this application's generic local state directory.

    The reader has no path override for snapshots or locator hints.  On Windows
    this uses LOCALAPPDATA; the temporary-directory fallback only makes the
    synthetic modules importable on non-Windows test hosts.
    """
    if os.name == 'nt':
        value = os.environ.get('LOCALAPPDATA')
        if (type(value) is not str or not value or value != value.strip()
                or '\0' in value):
            raise RuntimePathError()
        base = Path(value)
        if not base.is_absolute() or '..' in base.parts:
            raise RuntimePathError()
    else:
        base = Path(tempfile.gettempdir())
        if not base.is_absolute():
            raise RuntimePathError()
    if _verify_existing_prefix(base) is not None:
        raise RuntimePathError()
    directory = base / 'line-desktop-mcp' / 'line-reader'
    if create:
        return _create_checked_children(base, ('line-desktop-mcp', 'line-reader'))
    _verify_existing_prefix(directory)
    return directory


def create_reader_request_directory(runtime_dir):
    """Claim an empty per-request directory without accepting an arbitrary path.

    The Node parent normally creates it and supplies only an opaque UUID. This
    lets the parent clean the exact directory after a forcibly terminated child.
    Standalone Python callers create their own UUID directory instead.
    """
    if _verify_existing_prefix(runtime_dir) is not None:
        raise RuntimePathError()
    request_id = os.environ.get('LINE_MCP_READER_REQUEST_ID')
    managed = request_id is not None
    if managed:
        if not re.fullmatch(r'[0-9a-f]{32}', request_id):
            raise RuntimePathError()
    else:
        request_id = uuid.uuid4().hex
    directory = runtime_dir / ('line-reader-' + request_id)
    created_here = False
    try:
        if not managed:
            directory.mkdir(mode=0o700)
            created_here = True
        if _verify_existing_prefix(directory) is not None:
            raise RuntimePathError()
        if any(directory.iterdir()):
            raise RuntimePathError()
    except (OSError, RuntimePathError):
        if created_here:
            # Creation can succeed before verification fails. Remove only our
            # exact directory, and only if it is still safe and empty.
            try:
                if _verify_existing_prefix(directory) is None and not any(directory.iterdir()):
                    directory.rmdir()
            except (OSError, RuntimePathError):
                pass
        raise RuntimePathError() from None
    return directory
