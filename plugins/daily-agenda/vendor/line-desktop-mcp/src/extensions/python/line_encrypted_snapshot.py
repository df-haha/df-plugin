"""Bounded, source-read-only snapshots for LINE's encrypted SQLite database.

The returned database and WAL bytes remain encrypted.  This module does not
decrypt pages or open SQLite; callers hand the verified pair to the cipher
engine in a private, writable copy directory.  Stability here is observational
(two equal reads bracketing the pair), not a mathematically atomic snapshot.
"""

from __future__ import annotations

from dataclasses import dataclass
import datetime as dt
import errno
import hashlib
import os
from pathlib import Path
import re
import stat
import struct
import time
from typing import Optional


MAX_FILE_BYTES = 256 * 1024 * 1024
MAX_CAPTURE_ATTEMPTS = 3
_READ_CHUNK_BYTES = 1024 * 1024

_SOURCE_LIMIT_SETTING = "LINE_MCP_MAX_SOURCE_BYTES"
_WAL_LIMIT_SETTING = "LINE_MCP_MAX_WAL_BYTES"
_SNAPSHOT_LIMIT_SETTING = "LINE_MCP_MAX_SNAPSHOT_BYTES"
_DEFAULT_MAX_SOURCE_BYTES = 2 * 1024 ** 3
_DEFAULT_MAX_WAL_BYTES = 256 * 1024 ** 2
_DEFAULT_MAX_SNAPSHOT_BYTES = 2304 * 1024 ** 2
_HARD_MAX_SOURCE_BYTES = 8 * 1024 ** 3
_HARD_MAX_WAL_BYTES = 1024 ** 3
_HARD_MAX_SNAPSHOT_BYTES = 9 * 1024 ** 3
_DATABASE_PREFIX_BYTES = 4096
_SNAPSHOT_FILENAMES = (
    "snapshot.edb-wal",
    "snapshot.edb-shm",
    "snapshot.edb-journal",
    "snapshot.edb",
)

_WAL_HEADER_BYTES = 32
_WAL_FRAME_HEADER_BYTES = 24
_WAL_VERSION = 3_007_000
_WAL_MAGIC_LITTLE_CHECKSUM = 0x377F0682
_WAL_MAGIC_BIG_CHECKSUM = 0x377F0683
_REPARSE_ATTRIBUTE = getattr(stat, "FILE_ATTRIBUTE_REPARSE_POINT", 0x400)


class SnapshotError(Exception):
    """A fixed-code failure that never includes a source path or file content."""

    def __init__(self, code: str, *, details: Optional[dict] = None):
        self.code = code
        if details is not None:
            expected = {"sourceKind", "sourceBytes", "maxBytes", "setting"}
            expected_settings = {
                "database": _SOURCE_LIMIT_SETTING,
                "wal": _WAL_LIMIT_SETTING,
                "snapshot": _SNAPSHOT_LIMIT_SETTING,
            }
            source_kind = details.get("sourceKind")
            if (
                code != "SOURCE_TOO_LARGE"
                or set(details) != expected
                or source_kind not in expected_settings
                or type(details.get("sourceBytes")) is not int
                or type(details.get("maxBytes")) is not int
                or details.get("maxBytes") < 1
                or details.get("sourceBytes") <= details.get("maxBytes")
                or details.get("setting") != expected_settings.get(source_kind)
            ):
                raise ValueError("invalid snapshot error details")
            details = dict(details)
        self.details = details
        super().__init__(code)


@dataclass(frozen=True)
class SnapshotLimits:
    """Byte budgets for one streamed database/WAL snapshot."""

    max_source_bytes: int = _DEFAULT_MAX_SOURCE_BYTES
    max_wal_bytes: int = _DEFAULT_MAX_WAL_BYTES
    max_snapshot_bytes: int = _DEFAULT_MAX_SNAPSHOT_BYTES


def _parse_limit(setting: str, default: int, hard_max: int) -> int:
    raw = os.environ.get(setting)
    if raw is None:
        return default
    if len(raw) > 20 or re.fullmatch(r"[0-9]+", raw) is None:
        raise SnapshotError("SOURCE_LIMIT_INVALID")
    try:
        value = int(raw, 10)
    except ValueError:
        raise SnapshotError("SOURCE_LIMIT_INVALID") from None
    if value <= 0 or value > hard_max:
        raise SnapshotError("SOURCE_LIMIT_INVALID")
    return value


def load_snapshot_limits() -> SnapshotLimits:
    """Load sanitized byte budgets from the three supported environment settings."""
    return SnapshotLimits(
        max_source_bytes=_parse_limit(
            _SOURCE_LIMIT_SETTING, _DEFAULT_MAX_SOURCE_BYTES, _HARD_MAX_SOURCE_BYTES
        ),
        max_wal_bytes=_parse_limit(
            _WAL_LIMIT_SETTING, _DEFAULT_MAX_WAL_BYTES, _HARD_MAX_WAL_BYTES
        ),
        max_snapshot_bytes=_parse_limit(
            _SNAPSHOT_LIMIT_SETTING,
            _DEFAULT_MAX_SNAPSHOT_BYTES,
            _HARD_MAX_SNAPSHOT_BYTES,
        ),
    )


def _validated_limits(limits: Optional[SnapshotLimits]) -> SnapshotLimits:
    if limits is None:
        return load_snapshot_limits()
    if not isinstance(limits, SnapshotLimits):
        raise SnapshotError("SOURCE_LIMIT_INVALID")
    values = (
        (limits.max_source_bytes, _HARD_MAX_SOURCE_BYTES),
        (limits.max_wal_bytes, _HARD_MAX_WAL_BYTES),
        (limits.max_snapshot_bytes, _HARD_MAX_SNAPSHOT_BYTES),
    )
    if any(type(value) is not int or value <= 0 or value > hard_max
           for value, hard_max in values):
        raise SnapshotError("SOURCE_LIMIT_INVALID")
    return limits


def _source_too_large(source_kind: str, source_bytes: int,
                      max_bytes: int, setting: str) -> SnapshotError:
    return SnapshotError(
        "SOURCE_TOO_LARGE",
        details={
            "sourceKind": source_kind,
            "sourceBytes": int(source_bytes),
            "maxBytes": int(max_bytes),
            "setting": setting,
        },
    )


class _SourceChanged(Exception):
    """Internal retry signal for a source that changed during observation."""


@dataclass(frozen=True)
class _Observation:
    present: bool
    data: Optional[bytes]
    digest: Optional[bytes]
    size: Optional[int]
    identity: Optional[tuple[int, int]]
    mtime_ns: Optional[int]
    ctime_ns: Optional[int]


def _is_reparse(info: os.stat_result) -> bool:
    attributes = getattr(info, "st_file_attributes", 0)
    return stat.S_ISLNK(info.st_mode) or bool(attributes & _REPARSE_ATTRIBUTE)


def _absolute_path(value: os.PathLike[str] | str) -> Path:
    try:
        raw = os.fspath(value)
        if not isinstance(raw, str) or not raw or "\0" in raw:
            raise ValueError
        return Path(os.path.abspath(raw))
    except (TypeError, ValueError, OSError):
        raise SnapshotError("INVALID_PATH") from None


def _path_components(path: Path):
    parts = path.parts
    if not parts:
        return
    current = Path(parts[0])
    yield current
    for part in parts[1:]:
        current = current / part
        yield current


def _assert_no_reparse_components(path: Path, *, final_may_be_missing: bool) -> None:
    components = tuple(_path_components(path))
    for index, component in enumerate(components):
        try:
            info = os.lstat(component)
        except FileNotFoundError:
            if final_may_be_missing and index == len(components) - 1:
                return
            raise SnapshotError("SOURCE_NOT_FOUND") from None
        except OSError:
            raise SnapshotError("SOURCE_IO_ERROR") from None
        if _is_reparse(info):
            raise SnapshotError("SOURCE_REPARSE")


def _identity(info: os.stat_result) -> tuple[int, int]:
    return int(info.st_dev), int(info.st_ino)


def _same_file(left: os.stat_result, right: os.stat_result) -> bool:
    return _identity(left) == _identity(right)


def _read_stats_stable(path_before: os.stat_result, opened_before: os.stat_result,
                       opened_after: os.stat_result, path_after: os.stat_result,
                       total: int) -> bool:
    # On Windows, stat-by-path and fstat-by-handle can expose different ctime
    # semantics (creation time versus metadata-change time).  Compare ctime only
    # within the same API, while identity, size, and mtime still agree across all
    # four observations.
    return (
        _same_file(path_before, opened_before)
        and _same_file(opened_before, opened_after)
        and _same_file(opened_after, path_after)
        and opened_before.st_size == opened_after.st_size == path_after.st_size == total
        and path_before.st_size == total
        and opened_before.st_mtime_ns == opened_after.st_mtime_ns == path_after.st_mtime_ns
        and path_before.st_mtime_ns == path_after.st_mtime_ns
        and path_before.st_ctime_ns == path_after.st_ctime_ns
        and opened_before.st_ctime_ns == opened_after.st_ctime_ns
    )


def _read_file(path: Path, *, optional: bool, keep_data: bool, max_bytes: int = MAX_FILE_BYTES) -> _Observation:
    _assert_no_reparse_components(path, final_may_be_missing=optional)
    try:
        path_before = os.lstat(path)
    except FileNotFoundError:
        if optional:
            return _Observation(False, None, None, None, None, None, None)
        raise _SourceChanged from None
    except OSError:
        raise SnapshotError("SOURCE_IO_ERROR") from None

    if _is_reparse(path_before):
        raise SnapshotError("SOURCE_REPARSE")
    if not stat.S_ISREG(path_before.st_mode):
        raise SnapshotError("SOURCE_NOT_FILE")
    if path_before.st_size > max_bytes:
        raise SnapshotError("SOURCE_TOO_LARGE")

    try:
        with path.open("rb", buffering=0) as stream:
            opened_before = os.fstat(stream.fileno())
            if not _same_file(path_before, opened_before):
                raise _SourceChanged
            if not stat.S_ISREG(opened_before.st_mode):
                raise SnapshotError("SOURCE_NOT_FILE")
            if opened_before.st_size > max_bytes:
                raise SnapshotError("SOURCE_TOO_LARGE")

            hasher = hashlib.sha256()
            if keep_data:
                data = stream.read(max_bytes + 1)
                hasher.update(data)
                total = len(data)
            else:
                data = None
                total = 0
                while True:
                    chunk = stream.read(min(_READ_CHUNK_BYTES, max_bytes + 1 - total))
                    if not chunk:
                        break
                    total += len(chunk)
                    if total > max_bytes:
                        raise SnapshotError("SOURCE_TOO_LARGE")
                    hasher.update(chunk)

            if total > max_bytes:
                raise SnapshotError("SOURCE_TOO_LARGE")
            opened_after = os.fstat(stream.fileno())
    except FileNotFoundError:
        raise _SourceChanged from None
    except PermissionError:
        raise SnapshotError("SOURCE_ACCESS_DENIED") from None
    except SnapshotError:
        raise
    except _SourceChanged:
        raise
    except OSError:
        raise SnapshotError("SOURCE_IO_ERROR") from None

    try:
        path_after = os.lstat(path)
    except FileNotFoundError:
        raise _SourceChanged from None
    except OSError:
        raise SnapshotError("SOURCE_IO_ERROR") from None

    if _is_reparse(path_after):
        raise SnapshotError("SOURCE_REPARSE")
    stable_stats = _read_stats_stable(
        path_before, opened_before, opened_after, path_after, total
    )
    if not stable_stats:
        raise _SourceChanged

    return _Observation(
        True,
        data,
        hasher.digest(),
        total,
        _identity(opened_after),
        int(opened_after.st_mtime_ns),
        int(opened_after.st_ctime_ns),
    )


def _observations_match(left: _Observation, right: _Observation) -> bool:
    return (
        left.present == right.present
        and left.size == right.size
        and left.identity == right.identity
        and left.mtime_ns == right.mtime_ns
        and left.ctime_ns == right.ctime_ns
        and left.digest == right.digest
    )


def _database_page_size_from_prefix(database_prefix: bytes, database_size: int) -> int:
    # wxSQLite3 AES128 leaves the standard page-size field at bytes 16..17 clear.
    if len(database_prefix) < 100:
        raise SnapshotError("DATABASE_HEADER_INVALID")
    encoded = int.from_bytes(database_prefix[16:18], "big")
    page_size = 65_536 if encoded == 1 else encoded
    if page_size < 512 or page_size > 65_536 or page_size & (page_size - 1):
        raise SnapshotError("DATABASE_HEADER_INVALID")
    if database_size % page_size:
        raise SnapshotError("DATABASE_SIZE_INVALID")
    return page_size


def _database_page_size(database: bytes) -> int:
    return _database_page_size_from_prefix(database, len(database))


def _wal_checksum(data: bytes | memoryview, *, big_endian: bool,
                  state: tuple[int, int] = (0, 0)) -> tuple[int, int]:
    if len(data) % 8:
        raise ValueError("checksum input must contain pairs of 32-bit words")
    byte_order = "big" if big_endian else "little"
    first, second = state
    for offset in range(0, len(data), 8):
        word1 = int.from_bytes(data[offset:offset + 4], byte_order)
        word2 = int.from_bytes(data[offset + 4:offset + 8], byte_order)
        first = (first + word1 + second) & 0xFFFFFFFF
        second = (second + word2 + first) & 0xFFFFFFFF
    return first, second


def _validate_wal(wal: Optional[bytes], database_page_size: int):
    if wal is None:
        return None, {
            "sourceState": "absent",
            "sourceBytes": 0,
            "returnedBytes": 0,
            "validFrames": 0,
            "committedFrames": 0,
            "tailBytesDiscarded": 0,
            "tailReason": None,
        }
    if not wal:
        return None, {
            "sourceState": "empty",
            "sourceBytes": 0,
            "returnedBytes": 0,
            "validFrames": 0,
            "committedFrames": 0,
            "tailBytesDiscarded": 0,
            "tailReason": None,
        }
    if len(wal) < _WAL_HEADER_BYTES:
        raise SnapshotError("WAL_HEADER_INVALID")

    magic, version, page_size = struct.unpack_from(">III", wal, 0)
    if magic not in (_WAL_MAGIC_LITTLE_CHECKSUM, _WAL_MAGIC_BIG_CHECKSUM):
        raise SnapshotError("WAL_HEADER_INVALID")
    if version != _WAL_VERSION:
        raise SnapshotError("WAL_HEADER_INVALID")
    if page_size < 512 or page_size > 65_536 or page_size & (page_size - 1):
        raise SnapshotError("WAL_HEADER_INVALID")
    if page_size != database_page_size:
        raise SnapshotError("WAL_PAGE_SIZE_MISMATCH")

    big_endian = magic == _WAL_MAGIC_BIG_CHECKSUM
    checksum = _wal_checksum(memoryview(wal)[:24], big_endian=big_endian)
    stored_header_checksum = struct.unpack_from(">II", wal, 24)
    if checksum != stored_header_checksum:
        raise SnapshotError("WAL_HEADER_INVALID")

    salt1, salt2 = struct.unpack_from(">II", wal, 16)
    frame_size = _WAL_FRAME_HEADER_BYTES + page_size
    offset = _WAL_HEADER_BYTES
    valid_frames = 0
    last_commit_frame = 0
    last_commit_db_pages = None
    tail_reason = None

    while len(wal) - offset >= frame_size:
        page_number, commit_db_pages, frame_salt1, frame_salt2, check1, check2 = (
            struct.unpack_from(">IIIIII", wal, offset)
        )
        if page_number == 0:
            tail_reason = "invalid_page_number"
            break
        if (frame_salt1, frame_salt2) != (salt1, salt2):
            tail_reason = "salt_mismatch"
            break
        frame = memoryview(wal)[offset:offset + frame_size]
        expected = _wal_checksum(
            frame[:8].tobytes() + frame[_WAL_FRAME_HEADER_BYTES:].tobytes(),
            big_endian=big_endian,
            state=checksum,
        )
        if expected != (check1, check2):
            tail_reason = "checksum_mismatch"
            break
        checksum = expected
        valid_frames += 1
        offset += frame_size
        if commit_db_pages:
            last_commit_frame = valid_frames
            last_commit_db_pages = commit_db_pages

    if tail_reason is None and offset != len(wal):
        tail_reason = "incomplete_frame"

    if last_commit_frame == 0:
        if len(wal) == _WAL_HEADER_BYTES:
            return None, {
                "sourceState": "header_only",
                "sourceBytes": len(wal),
                "returnedBytes": 0,
                "pageSize": page_size,
                "validFrames": 0,
                "committedFrames": 0,
                "tailBytesDiscarded": 0,
                "tailReason": None,
            }
        # A present WAL with frame bytes but no validated commit is ambiguous:
        # it may be torn/corrupt or use a legacy plaintext-checksum codec mode.
        raise SnapshotError("WAL_NO_VALID_COMMIT")

    commit_end = _WAL_HEADER_BYTES + last_commit_frame * frame_size
    if tail_reason is None and valid_frames > last_commit_frame:
        tail_reason = "uncommitted_tail"
    committed = wal[:commit_end]
    return committed, {
        "sourceState": "committed",
        "sourceBytes": len(wal),
        "returnedBytes": len(committed),
        "pageSize": page_size,
        "validFrames": valid_frames,
        "committedFrames": last_commit_frame,
        "commitDatabasePages": last_commit_db_pages,
        "tailBytesDiscarded": len(wal) - commit_end,
        "tailReason": tail_reason,
    }


def _snapshot_directory(value: os.PathLike[str] | str) -> Path:
    directory = _absolute_path(value)
    _assert_no_reparse_components(directory, final_may_be_missing=False)
    try:
        info = os.lstat(directory)
    except FileNotFoundError:
        raise SnapshotError("SOURCE_NOT_FOUND") from None
    except OSError:
        raise SnapshotError("SNAPSHOT_IO_ERROR") from None
    if _is_reparse(info):
        raise SnapshotError("SOURCE_REPARSE")
    if not stat.S_ISDIR(info.st_mode):
        raise SnapshotError("SOURCE_NOT_FILE")
    return directory


def _snapshot_paths(directory: Path) -> tuple[Path, ...]:
    paths = tuple(directory / name for name in _SNAPSHOT_FILENAMES)
    if any(path.parent != directory for path in paths):
        raise SnapshotError("INVALID_PATH")
    return paths


def _assert_snapshot_destination_available(directory: Path) -> None:
    for path in _snapshot_paths(directory):
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError:
            raise SnapshotError("SNAPSHOT_IO_ERROR") from None
        if _is_reparse(info):
            raise SnapshotError("SOURCE_REPARSE")
        raise SnapshotError("SNAPSHOT_DESTINATION_EXISTS")


def cleanup_snapshot(directory: os.PathLike[str] | str) -> None:
    """Delete only this module's four fixed snapshot filenames.

    The caller owns the directory itself.  No directory enumeration or recursive
    deletion is performed, and every existing component is rejected if it is a
    symlink or Windows reparse point.
    """
    directory_path = _snapshot_directory(directory)
    existing = []
    for path in _snapshot_paths(directory_path):
        _assert_no_reparse_components(path, final_may_be_missing=True)
        try:
            info = os.lstat(path)
        except FileNotFoundError:
            continue
        except OSError:
            raise SnapshotError("SNAPSHOT_CLEANUP_FAILED") from None
        if _is_reparse(info):
            raise SnapshotError("SOURCE_REPARSE")
        if not stat.S_ISREG(info.st_mode):
            raise SnapshotError("SNAPSHOT_CLEANUP_FAILED")
        existing.append(path)

    for path in existing:
        try:
            path.unlink(missing_ok=True)
        except OSError:
            raise SnapshotError("SNAPSHOT_CLEANUP_FAILED") from None


def _raise_destination_error(error: OSError) -> None:
    if error.errno == errno.ENOSPC or getattr(error, "winerror", None) == 112:
        raise SnapshotError("SNAPSHOT_DISK_FULL") from None
    if isinstance(error, FileExistsError):
        raise SnapshotError("SNAPSHOT_DESTINATION_EXISTS") from None
    raise SnapshotError("SNAPSHOT_IO_ERROR") from None


def _open_destination_exclusive(path: Path):
    _assert_no_reparse_components(path.parent, final_may_be_missing=False)
    try:
        return path.open("xb", buffering=0)
    except FileExistsError:
        try:
            info = os.lstat(path)
        except OSError:
            raise SnapshotError("SNAPSHOT_DESTINATION_EXISTS") from None
        if _is_reparse(info):
            raise SnapshotError("SOURCE_REPARSE")
        raise SnapshotError("SNAPSHOT_DESTINATION_EXISTS")
    except OSError as error:
        _raise_destination_error(error)


def _write_destination(stream, data: bytes) -> None:
    remaining = memoryview(data)
    try:
        while remaining:
            written = stream.write(remaining)
            if not written:
                raise OSError(errno.EIO, "snapshot write did not progress")
            remaining = remaining[written:]
    except OSError as error:
        _raise_destination_error(error)


def _enforce_stream_size(source_kind: str, source_bytes: int, max_bytes: int,
                         setting: str, aggregate_base: int,
                         aggregate_limit: int) -> None:
    if source_bytes > max_bytes:
        raise _source_too_large(source_kind, source_bytes, max_bytes, setting)
    aggregate_bytes = aggregate_base + source_bytes
    if aggregate_bytes > aggregate_limit:
        raise _source_too_large(
            "snapshot", aggregate_bytes, aggregate_limit, _SNAPSHOT_LIMIT_SETTING
        )


def _stream_observe(path: Path, *, optional: bool, destination: Optional[Path],
                    max_bytes: int, source_kind: str, setting: str,
                    aggregate_base: int, aggregate_limit: int,
                    prefix_bytes: int = 0) -> _Observation:
    """Copy or hash one complete source through bounded reads and stable stats."""
    _assert_no_reparse_components(path, final_may_be_missing=optional)
    try:
        path_before = os.lstat(path)
    except FileNotFoundError:
        if optional:
            return _Observation(False, None, None, None, None, None, None)
        raise _SourceChanged from None
    except OSError:
        raise SnapshotError("SOURCE_IO_ERROR") from None

    if _is_reparse(path_before):
        raise SnapshotError("SOURCE_REPARSE")
    if not stat.S_ISREG(path_before.st_mode):
        raise SnapshotError("SOURCE_NOT_FILE")
    _enforce_stream_size(
        source_kind,
        int(path_before.st_size),
        max_bytes,
        setting,
        aggregate_base,
        aggregate_limit,
    )

    try:
        source_stream = path.open("rb", buffering=0)
    except FileNotFoundError:
        raise _SourceChanged from None
    except PermissionError:
        raise SnapshotError("SOURCE_ACCESS_DENIED") from None
    except OSError:
        raise SnapshotError("SOURCE_IO_ERROR") from None

    destination_stream = None
    try:
        with source_stream:
            try:
                opened_before = os.fstat(source_stream.fileno())
            except OSError:
                raise SnapshotError("SOURCE_IO_ERROR") from None
            if not _same_file(path_before, opened_before):
                raise _SourceChanged
            if not stat.S_ISREG(opened_before.st_mode):
                raise SnapshotError("SOURCE_NOT_FILE")
            _enforce_stream_size(
                source_kind,
                int(opened_before.st_size),
                max_bytes,
                setting,
                aggregate_base,
                aggregate_limit,
            )

            if destination is not None:
                destination_stream = _open_destination_exclusive(destination)

            hasher = hashlib.sha256()
            prefix = bytearray()
            total = 0
            while True:
                request_bytes = min(_READ_CHUNK_BYTES, max_bytes + 1 - total)
                if request_bytes <= 0:
                    request_bytes = 1
                try:
                    chunk = source_stream.read(request_bytes)
                except PermissionError:
                    raise SnapshotError("SOURCE_ACCESS_DENIED") from None
                except OSError:
                    raise SnapshotError("SOURCE_IO_ERROR") from None
                if not chunk:
                    break
                total += len(chunk)
                _enforce_stream_size(
                    source_kind,
                    total,
                    max_bytes,
                    setting,
                    aggregate_base,
                    aggregate_limit,
                )
                hasher.update(chunk)
                if len(prefix) < prefix_bytes:
                    prefix.extend(chunk[:prefix_bytes - len(prefix)])
                if destination_stream is not None:
                    _write_destination(destination_stream, chunk)

            try:
                opened_after = os.fstat(source_stream.fileno())
            except OSError:
                raise SnapshotError("SOURCE_IO_ERROR") from None
    except PermissionError:
        raise SnapshotError("SOURCE_ACCESS_DENIED") from None
    except SnapshotError:
        raise
    except _SourceChanged:
        raise
    except OSError:
        raise SnapshotError("SOURCE_IO_ERROR") from None
    finally:
        if destination_stream is not None:
            try:
                destination_stream.close()
            except OSError as error:
                _raise_destination_error(error)

    try:
        path_after = os.lstat(path)
    except FileNotFoundError:
        raise _SourceChanged from None
    except OSError:
        raise SnapshotError("SOURCE_IO_ERROR") from None
    if _is_reparse(path_after):
        raise SnapshotError("SOURCE_REPARSE")
    if not _read_stats_stable(
        path_before, opened_before, opened_after, path_after, total
    ):
        raise _SourceChanged

    return _Observation(
        True,
        bytes(prefix) if prefix_bytes else None,
        hasher.digest(),
        total,
        _identity(opened_after),
        int(opened_after.st_mtime_ns),
        int(opened_after.st_ctime_ns),
    )


def _prefix_stats_stable(path_before: os.stat_result, opened_before: os.stat_result,
                         opened_after: os.stat_result,
                         path_after: os.stat_result) -> bool:
    return (
        _same_file(path_before, opened_before)
        and _same_file(opened_before, opened_after)
        and _same_file(opened_after, path_after)
        and path_before.st_size == opened_before.st_size
        == opened_after.st_size == path_after.st_size
        and path_before.st_mtime_ns == opened_before.st_mtime_ns
        == opened_after.st_mtime_ns == path_after.st_mtime_ns
        and path_before.st_ctime_ns == path_after.st_ctime_ns
        and opened_before.st_ctime_ns == opened_after.st_ctime_ns
    )


def _observe_database_prefix(path: Path, limits: SnapshotLimits) -> _Observation:
    _assert_no_reparse_components(path, final_may_be_missing=False)
    try:
        path_before = os.lstat(path)
    except FileNotFoundError:
        raise _SourceChanged from None
    except OSError:
        raise SnapshotError("SOURCE_IO_ERROR") from None
    if _is_reparse(path_before):
        raise SnapshotError("SOURCE_REPARSE")
    if not stat.S_ISREG(path_before.st_mode):
        raise SnapshotError("SOURCE_NOT_FILE")
    if path_before.st_size > limits.max_source_bytes:
        raise _source_too_large(
            "database",
            int(path_before.st_size),
            limits.max_source_bytes,
            _SOURCE_LIMIT_SETTING,
        )

    try:
        with path.open("rb", buffering=0) as stream:
            opened_before = os.fstat(stream.fileno())
            if not _same_file(path_before, opened_before):
                raise _SourceChanged
            if not stat.S_ISREG(opened_before.st_mode):
                raise SnapshotError("SOURCE_NOT_FILE")
            if opened_before.st_size > limits.max_source_bytes:
                raise _source_too_large(
                    "database",
                    int(opened_before.st_size),
                    limits.max_source_bytes,
                    _SOURCE_LIMIT_SETTING,
                )

            prefix = bytearray()
            while len(prefix) < _DATABASE_PREFIX_BYTES:
                chunk = stream.read(_DATABASE_PREFIX_BYTES - len(prefix))
                if not chunk:
                    break
                prefix.extend(chunk)
            opened_after = os.fstat(stream.fileno())
    except FileNotFoundError:
        raise _SourceChanged from None
    except PermissionError:
        raise SnapshotError("SOURCE_ACCESS_DENIED") from None
    except SnapshotError:
        raise
    except _SourceChanged:
        raise
    except OSError:
        raise SnapshotError("SOURCE_IO_ERROR") from None

    try:
        path_after = os.lstat(path)
    except FileNotFoundError:
        raise _SourceChanged from None
    except OSError:
        raise SnapshotError("SOURCE_IO_ERROR") from None
    if _is_reparse(path_after):
        raise SnapshotError("SOURCE_REPARSE")
    if not _prefix_stats_stable(path_before, opened_before, opened_after, path_after):
        raise _SourceChanged
    if len(prefix) != _DATABASE_PREFIX_BYTES:
        raise SnapshotError("DATABASE_HEADER_INVALID")

    data = bytes(prefix)
    return _Observation(
        True,
        data,
        hashlib.sha256(data).digest(),
        int(opened_after.st_size),
        _identity(opened_after),
        int(opened_after.st_mtime_ns),
        int(opened_after.st_ctime_ns),
    )


def read_database_prefix(db_path: os.PathLike[str] | str,
                         *, limits: Optional[SnapshotLimits] = None) -> bytes:
    """Return exactly 4096 stable source bytes without reading the whole database."""
    active_limits = _validated_limits(limits)
    database_path = _absolute_path(db_path)
    for _attempt in range(1, MAX_CAPTURE_ATTEMPTS + 1):
        try:
            first = _observe_database_prefix(database_path, active_limits)
            second = _observe_database_prefix(database_path, active_limits)
        except _SourceChanged:
            continue
        if not _observations_match(first, second) or first.data != second.data:
            continue
        if first.data is None or first.size is None:
            raise SnapshotError("SOURCE_IO_ERROR")
        _database_page_size_from_prefix(first.data, first.size)
        return first.data
    raise SnapshotError("SOURCE_BUSY")


def _read_exact(stream, size: int) -> bytes:
    data = bytearray()
    while len(data) < size:
        try:
            chunk = stream.read(min(_READ_CHUNK_BYTES, size - len(data)))
        except OSError:
            raise SnapshotError("SNAPSHOT_IO_ERROR") from None
        if not chunk:
            raise SnapshotError("SNAPSHOT_IO_ERROR")
        data.extend(chunk)
    return bytes(data)


def _assert_snapshot_copy(path: Path, expected_size: int) -> None:
    _assert_no_reparse_components(path, final_may_be_missing=False)
    try:
        info = os.lstat(path)
    except OSError:
        raise SnapshotError("SNAPSHOT_IO_ERROR") from None
    if _is_reparse(info):
        raise SnapshotError("SOURCE_REPARSE")
    if not stat.S_ISREG(info.st_mode) or info.st_size != expected_size:
        raise SnapshotError("SNAPSHOT_IO_ERROR")


def _remove_snapshot_copy(path: Path, expected_size: int) -> None:
    _assert_snapshot_copy(path, expected_size)
    try:
        path.unlink()
    except OSError:
        raise SnapshotError("SNAPSHOT_CLEANUP_FAILED") from None


def _validate_streamed_wal(path: Path, observation: _Observation,
                           database_page_size: int):
    if not observation.present:
        return {
            "sourceState": "absent",
            "sourceBytes": 0,
            "returnedBytes": 0,
            "validFrames": 0,
            "committedFrames": 0,
            "tailBytesDiscarded": 0,
            "tailReason": None,
        }
    source_bytes = observation.size
    if source_bytes is None:
        raise SnapshotError("SNAPSHOT_IO_ERROR")
    _assert_snapshot_copy(path, source_bytes)
    if source_bytes == 0:
        _remove_snapshot_copy(path, source_bytes)
        return {
            "sourceState": "empty",
            "sourceBytes": 0,
            "returnedBytes": 0,
            "validFrames": 0,
            "committedFrames": 0,
            "tailBytesDiscarded": 0,
            "tailReason": None,
        }
    if source_bytes < _WAL_HEADER_BYTES:
        raise SnapshotError("WAL_HEADER_INVALID")

    try:
        stream = path.open("r+b", buffering=0)
    except OSError:
        raise SnapshotError("SNAPSHOT_IO_ERROR") from None
    remove_after_close = False
    try:
        with stream:
            try:
                opened = os.fstat(stream.fileno())
            except OSError:
                raise SnapshotError("SNAPSHOT_IO_ERROR") from None
            if _is_reparse(opened) or not stat.S_ISREG(opened.st_mode) or opened.st_size != source_bytes:
                raise SnapshotError("SNAPSHOT_IO_ERROR")
            header = _read_exact(stream, _WAL_HEADER_BYTES)
            magic, version, page_size = struct.unpack_from(">III", header, 0)
            if magic not in (_WAL_MAGIC_LITTLE_CHECKSUM, _WAL_MAGIC_BIG_CHECKSUM):
                raise SnapshotError("WAL_HEADER_INVALID")
            if version != _WAL_VERSION:
                raise SnapshotError("WAL_HEADER_INVALID")
            if page_size < 512 or page_size > 65_536 or page_size & (page_size - 1):
                raise SnapshotError("WAL_HEADER_INVALID")
            if page_size != database_page_size:
                raise SnapshotError("WAL_PAGE_SIZE_MISMATCH")

            big_endian = magic == _WAL_MAGIC_BIG_CHECKSUM
            checksum = _wal_checksum(header[:24], big_endian=big_endian)
            if checksum != struct.unpack_from(">II", header, 24):
                raise SnapshotError("WAL_HEADER_INVALID")

            salt1, salt2 = struct.unpack_from(">II", header, 16)
            frame_size = _WAL_FRAME_HEADER_BYTES + page_size
            offset = _WAL_HEADER_BYTES
            valid_frames = 0
            last_commit_frame = 0
            last_commit_db_pages = None
            tail_reason = None

            while source_bytes - offset >= frame_size:
                frame = _read_exact(stream, frame_size)
                page_number, commit_db_pages, frame_salt1, frame_salt2, check1, check2 = (
                    struct.unpack_from(">IIIIII", frame, 0)
                )
                if page_number == 0:
                    tail_reason = "invalid_page_number"
                    break
                if (frame_salt1, frame_salt2) != (salt1, salt2):
                    tail_reason = "salt_mismatch"
                    break
                expected = _wal_checksum(frame[:8], big_endian=big_endian, state=checksum)
                expected = _wal_checksum(
                    memoryview(frame)[_WAL_FRAME_HEADER_BYTES:],
                    big_endian=big_endian,
                    state=expected,
                )
                if expected != (check1, check2):
                    tail_reason = "checksum_mismatch"
                    break
                checksum = expected
                valid_frames += 1
                offset += frame_size
                if commit_db_pages:
                    last_commit_frame = valid_frames
                    last_commit_db_pages = commit_db_pages

            if tail_reason is None and offset != source_bytes:
                tail_reason = "incomplete_frame"

            if last_commit_frame == 0:
                if source_bytes == _WAL_HEADER_BYTES:
                    remove_after_close = True
                    metadata = {
                        "sourceState": "header_only",
                        "sourceBytes": source_bytes,
                        "returnedBytes": 0,
                        "pageSize": page_size,
                        "validFrames": 0,
                        "committedFrames": 0,
                        "tailBytesDiscarded": 0,
                        "tailReason": None,
                    }
                else:
                    raise SnapshotError("WAL_NO_VALID_COMMIT")
            else:
                commit_end = _WAL_HEADER_BYTES + last_commit_frame * frame_size
                if tail_reason is None and valid_frames > last_commit_frame:
                    tail_reason = "uncommitted_tail"
                try:
                    stream.truncate(commit_end)
                except OSError:
                    raise SnapshotError("SNAPSHOT_IO_ERROR") from None
                metadata = {
                    "sourceState": "committed",
                    "sourceBytes": source_bytes,
                    "returnedBytes": commit_end,
                    "pageSize": page_size,
                    "validFrames": valid_frames,
                    "committedFrames": last_commit_frame,
                    "commitDatabasePages": last_commit_db_pages,
                    "tailBytesDiscarded": source_bytes - commit_end,
                    "tailReason": tail_reason,
                }
    except SnapshotError:
        raise
    except OSError:
        raise SnapshotError("SNAPSHOT_IO_ERROR") from None

    if remove_after_close:
        _remove_snapshot_copy(path, source_bytes)
    return metadata


def capture_snapshot_to(db_path: os.PathLike[str] | str,
                        directory: os.PathLike[str] | str,
                        *, limits: Optional[SnapshotLimits] = None):
    """Stream a stable encrypted DB/WAL pair into ``directory``.

    The source order is DB1, WAL1, WAL2, DB2.  DB1 and WAL1 are copied through
    exclusive destination handles; the second observations are hash-only.  A
    caller receives the path only after identities, timestamps, lengths, hashes,
    database shape, and the committed WAL prefix have all been validated.
    """
    active_limits = _validated_limits(limits)
    database_path = _absolute_path(db_path)
    wal_path = Path(str(database_path) + "-wal")
    directory_path = _snapshot_directory(directory)
    destination_path = directory_path / "snapshot.edb"
    destination_wal_path = directory_path / "snapshot.edb-wal"

    _assert_no_reparse_components(database_path, final_may_be_missing=False)
    _assert_no_reparse_components(wal_path, final_may_be_missing=True)
    _assert_snapshot_destination_available(directory_path)

    for attempt in range(1, MAX_CAPTURE_ATTEMPTS + 1):
        capture_started_at = dt.datetime.now(dt.timezone.utc).isoformat()
        capture_clock = time.monotonic()
        try:
            database_first = _stream_observe(
                database_path,
                optional=False,
                destination=destination_path,
                max_bytes=active_limits.max_source_bytes,
                source_kind="database",
                setting=_SOURCE_LIMIT_SETTING,
                aggregate_base=0,
                aggregate_limit=active_limits.max_snapshot_bytes,
                prefix_bytes=_DATABASE_PREFIX_BYTES,
            )
            if database_first.size is None:
                raise SnapshotError("SOURCE_IO_ERROR")
            wal_first = _stream_observe(
                wal_path,
                optional=True,
                destination=destination_wal_path,
                max_bytes=active_limits.max_wal_bytes,
                source_kind="wal",
                setting=_WAL_LIMIT_SETTING,
                aggregate_base=database_first.size,
                aggregate_limit=active_limits.max_snapshot_bytes,
            )
            wal_second = _stream_observe(
                wal_path,
                optional=True,
                destination=None,
                max_bytes=active_limits.max_wal_bytes,
                source_kind="wal",
                setting=_WAL_LIMIT_SETTING,
                aggregate_base=database_first.size,
                aggregate_limit=active_limits.max_snapshot_bytes,
            )
            wal_second_bytes = wal_second.size if wal_second.present and wal_second.size is not None else 0
            database_second = _stream_observe(
                database_path,
                optional=False,
                destination=None,
                max_bytes=active_limits.max_source_bytes,
                source_kind="database",
                setting=_SOURCE_LIMIT_SETTING,
                aggregate_base=wal_second_bytes,
                aggregate_limit=active_limits.max_snapshot_bytes,
            )
        except _SourceChanged:
            cleanup_snapshot(directory_path)
            continue
        except Exception:
            cleanup_snapshot(directory_path)
            raise

        if (
            not _observations_match(database_first, database_second)
            or not _observations_match(wal_first, wal_second)
        ):
            cleanup_snapshot(directory_path)
            continue

        try:
            if database_first.data is None or database_first.size is None:
                raise SnapshotError("SOURCE_IO_ERROR")
            _assert_snapshot_copy(destination_path, database_first.size)
            page_size = _database_page_size_from_prefix(
                database_first.data, database_first.size
            )
            wal_metadata = _validate_streamed_wal(
                destination_wal_path, wal_first, page_size
            )
        except Exception:
            cleanup_snapshot(directory_path)
            raise

        metadata = {
            "captureStartedAt": capture_started_at,
            "captureCompletedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "captureDurationMs": round((time.monotonic() - capture_clock) * 1000, 3),
            "snapshotKind": "observational_quiescent_copy",
            "atomicity": "not_mathematically_atomic",
            "sourceStable": True,
            "attempts": attempt,
            "databaseBytes": database_first.size,
            "databasePageSize": page_size,
            "wal": wal_metadata,
            "storage": "streamed_encrypted_files",
        }
        return destination_path, metadata

    raise SnapshotError("SOURCE_BUSY")


def capture_snapshot(db_path: os.PathLike[str] | str):
    """Return ``(database_bytes, committed_wal_bytes_or_none, metadata)``.

    Files are opened only for binary reads.  Each candidate pair is bracketed as
    DB1, WAL1, WAL2, DB2 and accepted only when identities, timestamps, lengths,
    and SHA-256 digests match.  Three immediately consecutive attempts are made;
    there is deliberately no sleep or background retry.
    """
    database_path = _absolute_path(db_path)
    wal_path = Path(str(database_path) + "-wal")
    _assert_no_reparse_components(database_path, final_may_be_missing=False)
    _assert_no_reparse_components(wal_path, final_may_be_missing=True)

    for attempt in range(1, MAX_CAPTURE_ATTEMPTS + 1):
        capture_started_at = dt.datetime.now(dt.timezone.utc).isoformat()
        capture_clock = time.monotonic()
        try:
            database_first = _read_file(database_path, optional=False, keep_data=True)
            wal_first = _read_file(wal_path, optional=True, keep_data=True)
            wal_second = _read_file(wal_path, optional=True, keep_data=False)
            database_second = _read_file(database_path, optional=False, keep_data=False)
        except _SourceChanged:
            continue

        if not _observations_match(database_first, database_second):
            continue
        if not _observations_match(wal_first, wal_second):
            continue
        if database_first.data is None:
            raise SnapshotError("SOURCE_IO_ERROR")

        page_size = _database_page_size(database_first.data)
        committed_wal, wal_metadata = _validate_wal(wal_first.data, page_size)
        metadata = {
            "captureStartedAt": capture_started_at,
            "captureCompletedAt": dt.datetime.now(dt.timezone.utc).isoformat(),
            "captureDurationMs": round((time.monotonic() - capture_clock) * 1000, 3),
            "snapshotKind": "observational_quiescent_copy",
            "atomicity": "not_mathematically_atomic",
            "sourceStable": True,
            "attempts": attempt,
            "databaseBytes": len(database_first.data),
            "databasePageSize": page_size,
            "wal": wal_metadata,
        }
        return database_first.data, committed_wal, metadata

    raise SnapshotError("SOURCE_BUSY")
