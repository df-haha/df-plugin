"""Tests for the LINE_MCP_TRUSTED_BUILDS environment variable patch.

Naming: test_*.py (required by unittest discover -p 'test_*.py').
"""
import hashlib
import json
import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock

PYTHON = Path(__file__).parents[2] / 'src' / 'extensions' / 'python'
TMP_ROOT = Path(tempfile.gettempdir())
if str(PYTHON) not in sys.path:
    sys.path.insert(0, str(PYTHON))

import line_client_compatibility as compatibility


class TrustedBuildsTests(unittest.TestCase):
    def setUp(self):
        TMP_ROOT.mkdir(mode=0o777, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _make_exe(self, content=b'line-fixture'):
        exe = self.root / 'LINE.exe'
        exe.write_bytes(content)
        return exe

    def _write_manifest(self, exe, *, version='1.2.3.4'):
        digest = hashlib.sha256(exe.read_bytes()).hexdigest()
        manifest = {'v': 1, 'builds': [{'version': version,
                    'bytes': exe.stat().st_size, 'sha256': digest}]}
        path = self.root / 'builds.json'
        path.write_text(json.dumps(manifest), encoding='utf-8')
        return path, digest

    def _write_trusted(self, builds, path=None):
        path = path or self.root / 'trusted.json'
        path.write_text(json.dumps({'builds': builds}), encoding='utf-8')
        return str(path)

    def _assert_code(self, code, callback):
        with self.assertRaises(compatibility.ClientBuildError) as ctx:
            callback()
        self.assertEqual(ctx.exception.code, code)

    # --- ENV not set: upstream behavior unchanged ---

    def test_no_env_var_matches_upstream_manifest(self):
        exe = self._make_exe()
        manifest, digest = self._write_manifest(exe)
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LINE_MCP_TRUSTED_BUILDS', None)
            result = compatibility.verify_client_build(exe, manifest_path=manifest)
        self.assertTrue(result['verified'])

    def test_no_env_var_upstream_mismatch_fails(self):
        exe = self._make_exe()
        manifest, _ = self._write_manifest(exe, version='1.0.0.0')
        exe.write_bytes(b'changed-build')
        with mock.patch.dict(os.environ, {}, clear=False):
            os.environ.pop('LINE_MCP_TRUSTED_BUILDS', None)
            self._assert_code('LINE_BUILD_UNVERIFIED',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    # --- ENV set: union behavior ---

    def test_trusted_builds_union_matches_local_only(self):
        exe = self._make_exe()
        digest = hashlib.sha256(exe.read_bytes()).hexdigest()
        # Upstream manifest has a different build
        manifest = self.root / 'builds.json'
        manifest.write_text(json.dumps({'v': 1, 'builds': [
            {'version': '99.0.0.0', 'bytes': 999, 'sha256': 'a' * 64}
        ]}), encoding='utf-8')
        trusted_path = self._write_trusted([
            {'version': '1.2.3.4', 'bytes': exe.stat().st_size, 'sha256': digest}
        ])
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': trusted_path}):
            result = compatibility.verify_client_build(exe, manifest_path=manifest)
        self.assertTrue(result['verified'])
        self.assertEqual(result['version'], '1.2.3.4')

    def test_trusted_builds_union_matches_upstream_still_works(self):
        exe = self._make_exe()
        manifest, digest = self._write_manifest(exe)
        # Trusted file has a different build
        trusted_path = self._write_trusted([
            {'version': '99.0.0.0', 'bytes': 999, 'sha256': 'b' * 64}
        ])
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': trusted_path}):
            result = compatibility.verify_client_build(exe, manifest_path=manifest)
        self.assertTrue(result['verified'])
        self.assertEqual(result['version'], '1.2.3.4')

    def test_trusted_builds_allows_extra_fields(self):
        """Local builds can have signer, trusted_at etc. but matching only uses version/bytes/sha256."""
        exe = self._make_exe()
        digest = hashlib.sha256(exe.read_bytes()).hexdigest()
        manifest = self.root / 'builds.json'
        manifest.write_text(json.dumps({'v': 1, 'builds': [
            {'version': '99.0.0.0', 'bytes': 999, 'sha256': 'a' * 64}
        ]}), encoding='utf-8')
        trusted_path = self._write_trusted([
            {'version': '1.2.3.4', 'bytes': exe.stat().st_size, 'sha256': digest,
             'signer': 'LY Corporation', 'trusted_at': '2026-10-08T00:00:00+08:00'}
        ])
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': trusted_path}):
            result = compatibility.verify_client_build(exe, manifest_path=manifest)
        self.assertTrue(result['verified'])

    # --- Fail-closed cases ---

    def test_file_not_exists_fails_closed(self):
        exe = self._make_exe()
        manifest, _ = self._write_manifest(exe)
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': '/nonexistent/path.json'}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    def test_empty_string_env_fails_closed(self):
        exe = self._make_exe()
        manifest, _ = self._write_manifest(exe)
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': ''}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    def test_relative_path_fails_closed(self):
        exe = self._make_exe()
        manifest, _ = self._write_manifest(exe)
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': 'relative/path.json'}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    def test_invalid_json_fails_closed(self):
        exe = self._make_exe()
        manifest, _ = self._write_manifest(exe)
        bad_file = self.root / 'bad.json'
        bad_file.write_text('not json', encoding='utf-8')
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': str(bad_file)}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    def test_wrong_schema_fails_closed(self):
        exe = self._make_exe()
        manifest, _ = self._write_manifest(exe)
        bad_file = self.root / 'bad.json'
        bad_file.write_text(json.dumps({'wrong': True}), encoding='utf-8')
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': str(bad_file)}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    def test_empty_builds_array_fails_closed(self):
        exe = self._make_exe()
        manifest, _ = self._write_manifest(exe)
        trusted_path = self.root / 'trusted.json'
        trusted_path.write_text(json.dumps({'builds': []}), encoding='utf-8')
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': str(trusted_path)}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    def test_bad_version_format_fails_closed(self):
        exe = self._make_exe()
        manifest, _ = self._write_manifest(exe)
        trusted_path = self._write_trusted([
            {'version': 'bad', 'bytes': 100, 'sha256': 'a' * 64}
        ])
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': trusted_path}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    def test_uppercase_sha256_fails_closed(self):
        exe = self._make_exe()
        manifest, _ = self._write_manifest(exe)
        trusted_path = self._write_trusted([
            {'version': '1.0.0.0', 'bytes': 100, 'sha256': 'A' * 64}
        ])
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': trusted_path}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    def test_sha256_wrong_length_fails_closed(self):
        exe = self._make_exe()
        manifest, _ = self._write_manifest(exe)
        trusted_path = self._write_trusted([
            {'version': '1.0.0.0', 'bytes': 100, 'sha256': 'a' * 63}
        ])
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': trusted_path}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    def test_non_int_bytes_fails_closed(self):
        exe = self._make_exe()
        manifest, _ = self._write_manifest(exe)
        trusted_path = self._write_trusted([
            {'version': '1.0.0.0', 'bytes': '100', 'sha256': 'a' * 64}
        ])
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': trusted_path}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    def test_duplicate_identity_within_local_fails_closed(self):
        """Same (bytes, sha256) twice in local file."""
        exe = self._make_exe()
        manifest, _ = self._write_manifest(exe)
        trusted_path = self._write_trusted([
            {'version': '1.0.0.0', 'bytes': 100, 'sha256': 'a' * 64},
            {'version': '2.0.0.0', 'bytes': 100, 'sha256': 'a' * 64},
        ])
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': trusted_path}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    def test_same_version_different_identity_cross_files_fails_closed(self):
        """Same version in upstream and local but different bytes/sha256."""
        exe = self._make_exe()
        manifest = self.root / 'builds.json'
        manifest.write_text(json.dumps({'v': 1, 'builds': [
            {'version': '1.0.0.0', 'bytes': 100, 'sha256': 'a' * 64}
        ]}), encoding='utf-8')
        trusted_path = self._write_trusted([
            {'version': '1.0.0.0', 'bytes': 200, 'sha256': 'b' * 64}
        ])
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': trusted_path}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    def test_same_identity_different_version_cross_files_fails_closed(self):
        """Same (bytes, sha256) in upstream and local but different version."""
        exe = self._make_exe()
        manifest = self.root / 'builds.json'
        manifest.write_text(json.dumps({'v': 1, 'builds': [
            {'version': '1.0.0.0', 'bytes': 100, 'sha256': 'a' * 64}
        ]}), encoding='utf-8')
        trusted_path = self._write_trusted([
            {'version': '2.0.0.0', 'bytes': 100, 'sha256': 'a' * 64}
        ])
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': trusted_path}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    def test_identical_entry_in_both_files_dedupes_silently(self):
        """Exact same triple in upstream and local is fine (union dedup)."""
        exe = self._make_exe()
        digest = hashlib.sha256(exe.read_bytes()).hexdigest()
        manifest = self.root / 'builds.json'
        build = {'version': '1.0.0.0', 'bytes': exe.stat().st_size, 'sha256': digest}
        manifest.write_text(json.dumps({'v': 1, 'builds': [build]}), encoding='utf-8')
        trusted_path = self._write_trusted([build])
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': trusted_path}):
            result = compatibility.verify_client_build(exe, manifest_path=manifest)
        self.assertTrue(result['verified'])

    def test_trusted_builds_invalid_does_not_fallback_to_upstream(self):
        """Even if exe matches upstream, a broken trusted file must fail."""
        exe = self._make_exe()
        manifest, _ = self._write_manifest(exe)
        bad_file = self.root / 'bad.json'
        bad_file.write_text('not json', encoding='utf-8')
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': str(bad_file)}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))

    def test_more_than_64_local_builds_fails_closed(self):
        exe = self._make_exe()
        manifest, _ = self._write_manifest(exe)
        builds = [{'version': f'{i}.0.0.0', 'bytes': i + 1, 'sha256': f'{i:064x}'}
                  for i in range(65)]
        trusted_path = self._write_trusted(builds)
        with mock.patch.dict(os.environ, {'LINE_MCP_TRUSTED_BUILDS': trusted_path}):
            self._assert_code('TRUSTED_BUILDS_INVALID',
                              lambda: compatibility.verify_client_build(exe, manifest_path=manifest))


if __name__ == '__main__':
    unittest.main()
