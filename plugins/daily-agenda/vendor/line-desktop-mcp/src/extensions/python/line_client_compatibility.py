"""Fail-closed local LINE executable compatibility verification.

This module only hashes a supplied executable through the snapshot module's
stable, no-reparse reader. It never opens LINE processes or reads their memory.
"""
from __future__ import annotations

import json
import os
from pathlib import Path
import re

from line_encrypted_snapshot import SnapshotError, _SourceChanged, _read_file


MAX_CLIENT_BYTES = 128 * 1024 * 1024
MAX_MANIFEST_BYTES = 8 * 1024
MANIFEST_PATH = Path(__file__).with_name('line_client_builds.json')
_VERSION = re.compile(r'(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\.(?:0|[1-9][0-9]*)\Z')
_SHA256 = re.compile(r'[0-9a-f]{64}\Z')


class ClientBuildError(Exception):
    """Public failures intentionally do not expose local paths or I/O details."""

    def __init__(self, code='LINE_BUILD_UNVERIFIED'):
        self.code = code
        super().__init__(code)


def _unverified():
    raise ClientBuildError('LINE_BUILD_UNVERIFIED')


def _trusted_builds_invalid():
    raise ClientBuildError('TRUSTED_BUILDS_INVALID')


def _read_manifest(manifest_path):
    try:
        observed = _read_file(Path(manifest_path), optional=False, keep_data=True,
                              max_bytes=MAX_MANIFEST_BYTES)
        if type(observed.data) is not bytes:
            _unverified()
        raw = json.loads(observed.data.decode('utf-8'))
    except (SnapshotError, _SourceChanged, OSError, TypeError, ValueError, UnicodeError, json.JSONDecodeError):
        _unverified()
    if type(raw) is not dict or set(raw) != {'v', 'builds'} or type(raw['v']) is not int or raw['v'] != 1:
        _unverified()
    builds = raw['builds']
    if type(builds) is not list or not 1 <= len(builds) <= 64:
        _unverified()
    accepted = []
    seen = set()
    for build in builds:
        if (type(build) is not dict or set(build) != {'version', 'bytes', 'sha256'}
                or type(build['version']) is not str or not _VERSION.fullmatch(build['version'])
                or type(build['bytes']) is not int or not 1 <= build['bytes'] <= MAX_CLIENT_BYTES
                or type(build['sha256']) is not str or not _SHA256.fullmatch(build['sha256'])):
            _unverified()
        identity = (build['bytes'], build['sha256'])
        if identity in seen:
            _unverified()
        seen.add(identity)
        accepted.append(dict(build))
    return raw['v'], accepted


def _read_trusted_builds(trusted_path):
    """Read a local trusted builds file. Raises TRUSTED_BUILDS_INVALID on any problem.

    Format: {"builds": [{"version","bytes","sha256", ...extra allowed...}]}
    Extra fields (signer, trusted_at, etc.) are allowed but matching only uses
    version/bytes/sha256. The 'v' key is not required (unlike the upstream manifest).
    """
    if not trusted_path or not os.path.isabs(trusted_path):
        _trusted_builds_invalid()
    try:
        observed = _read_file(Path(trusted_path), optional=False, keep_data=True,
                              max_bytes=MAX_MANIFEST_BYTES)
        if type(observed.data) is not bytes:
            _trusted_builds_invalid()
        raw = json.loads(observed.data.decode('utf-8'))
    except (SnapshotError, _SourceChanged, OSError, TypeError, ValueError,
            UnicodeError, json.JSONDecodeError):
        _trusted_builds_invalid()
    if type(raw) is not dict or 'builds' not in raw:
        _trusted_builds_invalid()
    builds = raw['builds']
    if type(builds) is not list or not 1 <= len(builds) <= 64:
        _trusted_builds_invalid()
    accepted = []
    seen = set()
    for build in builds:
        if type(build) is not dict:
            _trusted_builds_invalid()
        # Must have version, bytes, sha256; extra keys are allowed
        if not {'version', 'bytes', 'sha256'} <= set(build):
            _trusted_builds_invalid()
        version = build['version']
        size = build['bytes']
        sha = build['sha256']
        if (type(version) is not str or not _VERSION.fullmatch(version)
                or type(size) is not int or not 1 <= size <= MAX_CLIENT_BYTES
                or type(sha) is not str or not _SHA256.fullmatch(sha)):
            _trusted_builds_invalid()
        identity = (size, sha)
        if identity in seen:
            _trusted_builds_invalid()
        seen.add(identity)
        accepted.append({'version': version, 'bytes': size, 'sha256': sha})
    return accepted


def _merge_builds(upstream_builds, local_builds):
    """Merge upstream and local builds into a union. Fail-closed on conflicts.

    - Identical (version, bytes, sha256) triple: deduplicate silently.
    - Same version but different (bytes, sha256): TRUSTED_BUILDS_INVALID.
    - Same (bytes, sha256) but different version: TRUSTED_BUILDS_INVALID.
    """
    merged = list(upstream_builds)
    upstream_by_version = {b['version']: (b['bytes'], b['sha256']) for b in upstream_builds}
    upstream_by_identity = {(b['bytes'], b['sha256']): b['version'] for b in upstream_builds}
    for build in local_builds:
        version = build['version']
        identity = (build['bytes'], build['sha256'])
        if version in upstream_by_version:
            if upstream_by_version[version] == identity:
                continue  # exact duplicate, silently skip
            _trusted_builds_invalid()  # same version, different identity
        if identity in upstream_by_identity:
            if upstream_by_identity[identity] == version:
                continue  # exact duplicate (already handled above, defensive)
            _trusted_builds_invalid()  # same identity, different version
        merged.append(build)
    return merged


def verify_client_build(path, *, manifest_path=MANIFEST_PATH):
    """Return safe build metadata only when ``path`` exactly matches the manifest.

    Root readers should call this before any LINE process or memory operation.
    The result deliberately excludes the supplied path and all source I/O data.

    When LINE_MCP_TRUSTED_BUILDS is set, it must point to a valid absolute-path
    JSON file. Its builds are unioned with the upstream manifest. If the file is
    invalid in any way, TRUSTED_BUILDS_INVALID is raised — even if the exe
    matches the upstream manifest. This ensures the caller knows the local
    configuration is broken rather than silently falling back.
    """
    _manifest_version, upstream_builds = _read_manifest(manifest_path)

    # Read and validate local trusted builds before hashing the exe (fail fast)
    trusted_env = os.environ.get('LINE_MCP_TRUSTED_BUILDS')
    if trusted_env is not None:
        local_builds = _read_trusted_builds(trusted_env)
        builds = _merge_builds(upstream_builds, local_builds)
    else:
        builds = upstream_builds

    try:
        observed = _read_file(Path(path), optional=False, keep_data=False,
                              max_bytes=MAX_CLIENT_BYTES)
        if (type(observed.digest) is not bytes or len(observed.digest) != 32
                or type(observed.size) is not int
                or not 1 <= observed.size <= MAX_CLIENT_BYTES):
            _unverified()
    except (SnapshotError, _SourceChanged, OSError, TypeError, ValueError):
        _unverified()
    digest = observed.digest.hex()
    for build in builds:
        if build['bytes'] == observed.size and build['sha256'] == digest:
            return {
                'verified': True,
                'version': build['version'],
                'buildRef': f'sha256:{digest}',
            }
    _unverified()
