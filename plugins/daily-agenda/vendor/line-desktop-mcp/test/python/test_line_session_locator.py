import concurrent.futures
import importlib.util
import json
from pathlib import Path
import stat
import sys
import tempfile
import types
import unittest
from unittest import mock

PYTHON = Path(__file__).parents[2] / 'src' / 'extensions' / 'python'
sys.path.insert(0, str(PYTHON))

spec = importlib.util.spec_from_file_location('locator', PYTHON / 'line_session_locator.py')
locator = importlib.util.module_from_spec(spec)
spec.loader.exec_module(locator)
KEY = b'1234567890abcdef1234567890abcdef'
CREATED = 0x01ABCDEF01234567
ADDRESS = 0x12340000


class FakeProbe:
    class ProbeError(Exception):
        pass

    def __init__(self):
        self.window_calls = []
        self.full_calls = []
        self.window_data = b'x' * 128
        self.candidates = [KEY]
        self.validated = []
        self.window_error = False
        self.advance = lambda: None

    def read_locator_window(self, pid, exe, created, address, *, max_bytes):
        self.window_calls.append((pid, exe, created, address, max_bytes))
        self.advance()
        if self.window_error or created != f'{CREATED:016x}' or address != ADDRESS:
            raise self.ProbeError('fixed refusal')
        return self.window_data

    def iter_candidate_values(self, data, *, complete=True, start_is_boundary=True):
        yield from self.candidates

    def derive_key(self, candidate):
        return candidate

    def first_page_valid(self, page, key):
        self.validated.append((page, key))
        return page == b'fresh-page' and key == KEY

    def find_candidates(self, pid, exe, **kwargs):
        self.full_calls.append(kwargs)
        kwargs['process_created_filetime_recorder'](CREATED)
        kwargs['validated_window_recorder'](ADDRESS)
        valid = kwargs['candidate_validator'](KEY)
        return ([KEY] if valid else []), {'bytes_read': 500_000_000,
                                       'stopped_after_validation': valid}


class LocatorTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.database = self.root / 'fixture.edb'
        self.database.write_bytes(b'encrypted fixture')
        self.cache = self.root / 'locator.json'
        self.db_hash = locator.database_path_hash(self.database)
        self.payload = {'v': 1, 'pid': 42, 'processCreatedFiletimeHex': f'{CREATED:016x}',
                        'dbPathSha256': self.db_hash, 'windowAddressHex': f'{ADDRESS:016x}',
                        'savedAtUnixMs': locator._now_ms()}

    def seed(self, changes=None):
        self.cache.write_text(json.dumps({**self.payload, **(changes or {})}), encoding='utf-8')

    def acquire(self, probe, page=b'fresh-page'):
        return locator.acquire_passphrase(page, pid=42, expected_exe=self.root / 'LINE.exe',
                db_path=self.database, _probe=probe, _cache_path=self.cache)

    def test_closed_metadata_schema_and_time_bounds(self):
        self.assertTrue(locator.valid_locator(self.payload))
        for change in ({'key': KEY.decode()}, {'pid': True}, {'v': True}, {'v': 2},
                       {'windowAddressHex': '0' * 16}, {'processCreatedFiletimeHex': '0' * 16},
                       {'dbPathSha256': '../elsewhere'}, {'savedAtUnixMs': True},
                       {'savedAtUnixMs': self.payload['savedAtUnixMs'] - locator.TTL_MS - 1},
                       {'savedAtUnixMs': self.payload['savedAtUnixMs'] + 10000}):
            with self.subTest(change=tuple(change)):
                self.assertFalse(locator.valid_locator({**self.payload, **change}))
        self.assertFalse(locator.valid_locator(None))

    def test_cache_load_is_bounded_and_binds_database_and_pid(self):
        self.seed()
        self.assertIsNotNone(locator.load_locator(self.db_hash, 42, cache_path=self.cache))
        self.assertIsNone(locator.load_locator('0' * 64, 42, cache_path=self.cache))
        self.assertIsNone(locator.load_locator(self.db_hash, 99, cache_path=self.cache))
        for raw in (b'{broken', b'[]', b'x' * 1025, b'{"v":NaN}'):
            self.cache.write_bytes(raw)
            self.assertIsNone(locator.load_locator(self.db_hash, 42, cache_path=self.cache))

    def test_reparse_ancestor_refuses_hash_load_and_write(self):
        self.seed()
        original = Path.lstat
        def lstat(path):
            if path == self.root:
                return types.SimpleNamespace(st_mode=stat.S_IFDIR, st_file_attributes=0x400)
            return original(path)
        with mock.patch.object(Path, 'lstat', lstat):
            self.assertIsNone(locator.database_path_hash(self.database))
            self.assertIsNone(locator.load_locator(self.db_hash, 42, cache_path=self.cache))
            self.assertFalse(locator.commit_after_success((self.cache, self.payload)))

    def test_warm_hit_reads_current_window_and_never_runs_full_scan_or_implicitly_writes(self):
        self.seed()
        before = self.cache.read_bytes()
        probe = FakeProbe()
        key, metrics = self.acquire(probe)
        self.assertEqual(key, KEY)
        self.assertEqual(metrics['keyAcquisition'], 'locator')
        self.assertEqual(metrics['scanBytes'], 128)
        self.assertEqual(probe.window_calls[0][-1], locator.MAX_WINDOW_BYTES)
        self.assertEqual(probe.full_calls, [])
        self.assertTrue(all(page == b'fresh-page' for page, _ in probe.validated))
        self.assertEqual(self.cache.read_bytes(), before)
        self.assertNotIn('cache_commit', json.dumps(metrics))
        for forbidden in (*locator.FIELDS - {'v'}, KEY.decode()):
            self.assertNotIn(forbidden, json.dumps(metrics))

    def test_cold_result_returns_noncredential_commit_and_failed_write_is_nonfatal(self):
        probe = FakeProbe()
        key, metrics = self.acquire(probe)
        self.assertEqual((key, metrics['keyAcquisition']), (KEY, 'full-scan'))
        self.assertEqual(len(probe.full_calls), 1)
        self.assertFalse(self.cache.exists())
        self.assertTrue(locator.commit_after_success(metrics.cache_commit))
        before = self.cache.read_bytes()
        self.assertNotIn(KEY, before)
        self.assertEqual(set(json.loads(before)), locator.FIELDS)
        with mock.patch.object(locator.os, 'replace', side_effect=PermissionError('not public')):
            self.assertFalse(locator.commit_after_success(metrics.cache_commit))
        self.assertEqual(self.cache.read_bytes(), before)
        self.assertEqual(list(self.root.glob('.line-reader-locator-*.tmp')), [])

    def test_pid_reuse_expiry_bad_window_and_key_change_fall_back_once(self):
        cases = [({'processCreatedFiletimeHex': '0000000000000001'}, None),
                 ({'savedAtUnixMs': locator._now_ms() - locator.TTL_MS - 1}, None),
                 ({'windowAddressHex': '0000000011111111'}, None),
                 ({}, 'wrong-key'), ({}, 'read-failure'), ({}, 'oversized')]
        for change, kind in cases:
            with self.subTest(kind=kind, changed=tuple(change)):
                self.seed(change)
                probe = FakeProbe()
                if kind == 'wrong-key': probe.candidates = [b'a' * 32]
                if kind == 'read-failure': probe.window_error = True
                if kind == 'oversized': probe.window_data = b'x' * (locator.MAX_WINDOW_BYTES + 1)
                _, metrics = self.acquire(probe)
                self.assertEqual(metrics['keyAcquisition'], 'full-scan')
                self.assertEqual(len(probe.full_calls), 1)
                self.assertEqual(probe.full_calls[0]['max_candidates'], 10000)

    def test_warm_quota_does_not_consume_full_scan_quota(self):
        self.seed()
        probe = FakeProbe()
        probe.candidates = [f'{i:032x}'.encode() for i in range(300)] + [KEY]
        _, metrics = self.acquire(probe)
        self.assertEqual(metrics['keyAcquisition'], 'full-scan')
        self.assertEqual(len(probe.validated), locator.WARM_CANDIDATES + 2)
        self.assertEqual(len(probe.full_calls), 1)

    def test_warm_deadline_is_charged_to_overall_budget(self):
        self.seed()
        probe = FakeProbe()
        clock = [0.0]
        probe.advance = lambda: clock.__setitem__(0, 2.0)
        with mock.patch.object(locator.time, 'monotonic', side_effect=lambda: clock[0]):
            _, metrics = self.acquire(probe)
        self.assertEqual(metrics['keyAcquisition'], 'full-scan')
        self.assertEqual(probe.full_calls[0]['max_seconds'], 88.0)

    def test_invalid_fresh_page_never_returns_a_key_or_cache_commit(self):
        self.seed()
        probe = FakeProbe()
        before = self.cache.read_bytes()
        with self.assertRaises(locator.LocatorError):
            self.acquire(probe, page=b'changed-page')
        self.assertEqual(len(probe.full_calls), 1)
        self.assertEqual(self.cache.read_bytes(), before)

    def test_concurrent_writers_leave_a_complete_closed_document(self):
        with concurrent.futures.ThreadPoolExecutor(max_workers=4) as pool:
            results = list(pool.map(lambda i: locator.commit_after_success((self.cache,
                            {**self.payload, 'windowAddressHex': f'{ADDRESS + i:016x}'})), range(12)))
        self.assertTrue(any(results))
        final = json.loads(self.cache.read_bytes())
        self.assertTrue(locator.valid_locator(final))
        self.assertEqual(set(final), locator.FIELDS)
        self.assertEqual(list(self.root.glob('.line-reader-locator-*.tmp')), [])


if __name__ == '__main__':
    unittest.main()
