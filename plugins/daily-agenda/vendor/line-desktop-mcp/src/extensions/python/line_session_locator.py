"""Optional noncredential scan-window hints; keys live only in the reader child."""
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import re
import stat
import tempfile
import time
from line_runtime_paths import RuntimePathError, application_runtime_dir


CACHE_NAME = 'line-reader-locator-v1.json'
MAX_CACHE_BYTES = 1024
MAX_WINDOW_BYTES = 4 * 1024 * 1024 + 128
TTL_MS = 30 * 60 * 1000
WARM_SECONDS = 1.0
WARM_CANDIDATES = 256
TOTAL_SCAN_SECONDS = 90.0
FIELDS = frozenset(('v', 'pid', 'processCreatedFiletimeHex', 'dbPathSha256',
                    'windowAddressHex', 'savedAtUnixMs'))


def default_cache_path():
    return application_runtime_dir(create=False) / CACHE_NAME


class LocatorError(Exception):
    def __init__(self, code='SESSION_KEY_UNAVAILABLE'):
        self.code = code
        super().__init__('Local LINE initialization did not complete.')


class KeyMetrics(dict):
    """Only the dict is public; the noncredential commit capsule is private."""
    def __init__(self, public, cache_commit=None):
        super().__init__(public)
        self.cache_commit = cache_commit


def _now_ms():
    return int(time.time() * 1000)


def _plain_file_path(path, *, missing_leaf=False):
    path = Path(os.path.abspath(os.fspath(path)))
    chain = list(reversed(path.parents)) + [path]
    for current in chain:
        try:
            info = current.lstat()
        except FileNotFoundError:
            return path if missing_leaf and current == path else None
        if stat.S_ISLNK(info.st_mode) or getattr(info, 'st_file_attributes', 0) & 0x400:
            return None
        if current != path and not stat.S_ISDIR(info.st_mode):
            return None
        if current == path and not stat.S_ISREG(info.st_mode):
            return None
    return path


def database_path_hash(path):
    try:
        checked = _plain_file_path(path)
        if checked is None:
            return None
        return hashlib.sha256(os.path.normcase(str(checked)).encode('utf-8')).hexdigest()
    except (OSError, ValueError, TypeError):
        return None


def valid_locator(value, *, now_ms=None):
    now = _now_ms() if now_ms is None else now_ms
    return (type(value) is dict and set(value) == FIELDS
            and type(value['v']) is int and value['v'] == 1
            and type(value['pid']) is int and 0 < value['pid'] < 2**32
            and all(type(value[field]) is str and re.fullmatch(pattern, value[field])
                    for field, pattern in (
                        ('processCreatedFiletimeHex', r'[0-9a-f]{16}'),
                        ('windowAddressHex', r'[0-9a-f]{16}'),
                        ('dbPathSha256', r'[0-9a-f]{64}')))
            and int(value['processCreatedFiletimeHex'], 16) > 0
            and 0 < int(value['windowAddressHex'], 16) < 2**63
            and type(value['savedAtUnixMs']) is int
            and 0 < value['savedAtUnixMs'] < 2**53
            and type(now) is int and 0 <= now - value['savedAtUnixMs'] <= TTL_MS)


def load_locator(db_hash, pid, *, cache_path=None, now_ms=None):
    try:
        path = default_cache_path() if cache_path is None else Path(cache_path)
    except RuntimePathError:
        return None
    try:
        checked = _plain_file_path(path)
        if checked is None or checked.stat().st_size > MAX_CACHE_BYTES:
            return None
        flags = os.O_RDONLY | getattr(os, 'O_BINARY', 0) | getattr(os, 'O_NOFOLLOW', 0)
        with os.fdopen(os.open(checked, flags), 'rb') as stream:
            opened = os.fstat(stream.fileno())
            if (_plain_file_path(path) is None or not stat.S_ISREG(opened.st_mode)
                    or getattr(opened, 'st_file_attributes', 0) & 0x400
                    or opened.st_size > MAX_CACHE_BYTES):
                return None
            raw = stream.read(MAX_CACHE_BYTES + 1)
        if len(raw) > MAX_CACHE_BYTES:
            return None
        value = json.loads(raw)
        if not valid_locator(value, now_ms=now_ms):
            return None
        return value if value['pid'] == pid and value['dbPathSha256'] == db_hash else None
    except (OSError, ValueError, TypeError, UnicodeError):
        return None


def commit_after_success(cache_commit):
    """Best-effort atomic write; this is never required for a correct read."""
    if cache_commit is None:
        return False
    staged = None
    try:
        path, template = cache_commit
        path = Path(path)
        if path == default_cache_path():
            application_runtime_dir(create=True)
        payload = {**template, 'savedAtUnixMs': _now_ms()}
        if not valid_locator(payload) or _plain_file_path(path, missing_leaf=True) is None:
            return False
        raw = json.dumps(payload, separators=(',', ':'), ensure_ascii=True).encode('ascii')
        if len(raw) > MAX_CACHE_BYTES:
            return False
        fd, name = tempfile.mkstemp(prefix='.line-reader-locator-', suffix='.tmp', dir=path.parent)
        staged = Path(name)
        with os.fdopen(fd, 'wb') as stream:
            stream.write(raw)
            stream.flush()
            os.fsync(stream.fileno())
        if (_plain_file_path(staged) is None
                or _plain_file_path(path, missing_leaf=True) is None):
            return False
        os.replace(staged, path)
        staged = None
        return True
    except (OSError, ValueError, TypeError, RuntimePathError):
        return False
    finally:
        if staged is not None:
            try:
                staged.unlink(missing_ok=True)
            except OSError:
                pass


def _load_probe():
    spec = importlib.util.spec_from_file_location('line_locator_probe',
                Path(__file__).with_name('line-schema-probe.py'))
    probe = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(probe)
    return probe


def acquire_passphrase(first_page, *, pid, expected_exe, db_path,
                       _probe=None, _cache_path=None):
    """Try one small current-memory window, then at most one bounded full scan."""
    started = time.monotonic()
    probe = _load_probe() if _probe is None else _probe
    try:
        cache_path = default_cache_path() if _cache_path is None else Path(_cache_path)
    except RuntimePathError:
        cache_path = None
    db_hash = database_path_hash(db_path)
    hint = load_locator(db_hash, pid, cache_path=cache_path) if db_hash and cache_path else None
    warm_bytes = 0
    validate = lambda candidate: probe.first_page_valid(first_page, probe.derive_key(candidate))

    if hint:
        data = None
        seen = set()
        warm_started = time.monotonic()
        try:
            data = probe.read_locator_window(pid, expected_exe,
                hint['processCreatedFiletimeHex'], int(hint['windowAddressHex'], 16),
                max_bytes=MAX_WINDOW_BYTES)
            if type(data) is not bytes or not 32 <= len(data) <= MAX_WINDOW_BYTES:
                raise LocatorError()
            warm_bytes = len(data)
            for candidate in probe.iter_candidate_values(data, complete=False,
                    start_is_boundary=False):
                if time.monotonic() - warm_started > WARM_SECONDS:
                    break
                if candidate in seen:
                    continue
                if len(seen) >= WARM_CANDIDATES:
                    break
                seen.add(candidate)
                if validate(candidate):
                    return candidate, KeyMetrics({
                        'keyValidated': True, 'keyAcquisition': 'locator',
                        'scanSeconds': round(time.monotonic() - started, 3),
                        'scanBytes': warm_bytes, 'scanStoppedAfterValidation': True,
                    }, (cache_path, hint))
        except (probe.ProbeError, LocatorError, OSError, ValueError, TypeError):
            pass
        finally:
            data = None
            seen.clear()

    remaining = TOTAL_SCAN_SECONDS - (time.monotonic() - started)
    if remaining <= 0:
        raise LocatorError()
    observed = {}
    candidates = []
    try:
        candidates, metrics = probe.find_candidates(pid, expected_exe,
            max_seconds=remaining, max_candidates=10000, candidate_validator=validate,
            validated_window_recorder=lambda address: observed.update(window=address),
            process_created_filetime_recorder=lambda created: observed.update(created=created))
        if not candidates:
            raise LocatorError()
        candidate = candidates[0]
        if not validate(candidate):
            raise LocatorError()
        pending = None
        if (cache_path is not None and db_hash
                and type(observed.get('window')) is int and type(observed.get('created')) is int):
            template = {'v': 1, 'pid': pid, 'dbPathSha256': db_hash,
                        'processCreatedFiletimeHex': f"{observed['created']:016x}",
                        'windowAddressHex': f"{observed['window']:016x}",
                        'savedAtUnixMs': _now_ms()}
            if valid_locator(template):
                pending = (cache_path, template)
        return candidate, KeyMetrics({
            'keyValidated': True, 'keyAcquisition': 'full-scan',
            'scanSeconds': round(time.monotonic() - started, 3),
            'scanBytes': warm_bytes + metrics['bytes_read'],
            'scanStoppedAfterValidation': metrics['stopped_after_validation'],
        }, pending)
    finally:
        candidates.clear()
