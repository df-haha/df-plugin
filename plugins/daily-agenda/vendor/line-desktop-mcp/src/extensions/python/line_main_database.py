"""Main database selection with optional LINE_MCP_MAIN_DATABASE override.

Pure function: no Windows imports, no filesystem side effects. Testable on any
platform. Only accepts a bare filename; any path component is rejected.
"""
from __future__ import annotations

import os
import re
import unicodedata
from pathlib import Path


class MainDatabaseError(Exception):
    """Raised when the configured database name is invalid or not found."""

    def __init__(self, code: str) -> None:
        self.code = code
        super().__init__(code)


# Matches any character in Unicode category Cc (control characters)
_CONTROL_CHAR = re.compile(r'[\x00-\x1f\x7f-\x9f]')
# Prefixes that are always excluded from the main database list
_EXCLUDED_PREFIXES = ('album', 'chatStats', 'keep')


def _is_excluded(name: str) -> bool:
    """Check if a database filename starts with an excluded prefix followed by underscore."""
    return any(name.startswith(f'{prefix}_') for prefix in _EXCLUDED_PREFIXES)


def _validate_configured_name(configured: str) -> None:
    """Validate the configured name is a safe bare filename. Raises MAIN_DATABASE_INVALID."""
    if not configured:
        raise MainDatabaseError('MAIN_DATABASE_INVALID')
    # Reject path separators
    if '/' in configured or '\\' in configured:
        raise MainDatabaseError('MAIN_DATABASE_INVALID')
    # Reject parent directory traversal
    if '..' in configured:
        raise MainDatabaseError('MAIN_DATABASE_INVALID')
    # Reject absolute paths (Windows or Unix)
    if os.path.isabs(configured):
        raise MainDatabaseError('MAIN_DATABASE_INVALID')
    # Reject NUL and control characters
    if _CONTROL_CHAR.search(configured):
        raise MainDatabaseError('MAIN_DATABASE_INVALID')
    # Reject excluded prefixes
    if _is_excluded(configured):
        raise MainDatabaseError('MAIN_DATABASE_INVALID')


def select_main_database(paths: list[Path], *, configured: str | None) -> Path:
    """Select the main database from a list of .edb file paths.

    Args:
        paths: All .edb files found in the LINE Data/db directory.
        configured: The value of LINE_MCP_MAIN_DATABASE (None if unset).

    Returns:
        The selected Path.

    Raises:
        MainDatabaseError with code:
            MAIN_DATABASE_INVALID: configured name has invalid characters or format.
            MAIN_DATABASE_NOT_FOUND: configured name not in the filtered list.
            MAIN_DATABASE_AMBIGUOUS: not configured and multiple main DBs exist.
    """
    # Filter out album_, keep_, chatStats_ prefixed databases
    main = [p for p in paths if not _is_excluded(p.name)]

    if configured is None:
        # Upstream behavior: exactly one main DB required
        if len(main) != 1:
            raise MainDatabaseError('MAIN_DATABASE_AMBIGUOUS')
        return main[0]

    # Validate the configured name
    _validate_configured_name(configured)

    # Case-sensitive match against filtered list
    for p in main:
        if p.name == configured:
            return p

    raise MainDatabaseError('MAIN_DATABASE_NOT_FOUND')
