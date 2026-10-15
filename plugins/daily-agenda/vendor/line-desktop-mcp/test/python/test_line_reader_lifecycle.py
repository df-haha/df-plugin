import ctypes as ct
import datetime as dt
import importlib.util
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

PYTHON = Path(__file__).parents[2] / 'src' / 'extensions' / 'python'
sys.path.insert(0, str(PYTHON))


def load(name, filename):
    spec = importlib.util.spec_from_file_location(name, PYTHON / filename)
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


reader = load('reader_lifecycle', 'line-reader.py')
probe = load('reader_probe', 'line-schema-probe.py')


class ReaderLifecycleTests(unittest.TestCase):
    def test_process_discovery_distinguishes_absence_ambiguity_and_enumeration_failure(self):
        def discovery(names, *, last_error=18, snapshot=7):
            entries = iter(enumerate(names, 100))
            calls = {'closed': 0}
            def next_entry(handle, pointer):
                try:
                    number, name = next(entries)
                    pointer._obj.pid, pointer._obj.exe = number, name
                    return True
                except StopIteration:
                    return False
            def close(handle):
                calls['closed'] += 1
            kernel = SimpleNamespace(CreateToolhelp32Snapshot=lambda *_: snapshot,
                Process32FirstW=next_entry, Process32NextW=next_entry, CloseHandle=close)
            with mock.patch.object(reader.ct, 'WinDLL', return_value=kernel), \
                 mock.patch.object(reader.ct, 'get_last_error', return_value=last_error):
                try:
                    return reader.find_line_process(), calls
                except reader.ReaderError as error:
                    return error.code, calls
        for names, last_error, expected in (
            ([], 18, 'LINE_PROCESS_UNAVAILABLE'),
            (['other.exe'], 18, 'LINE_PROCESS_UNAVAILABLE'),
            (['LINE.exe'], 18, 100),
            (['LINE.exe', 'line.exe'], 18, 'LINE_PROCESS_AMBIGUOUS'),
            ([], 5, 'LINE_PROCESS_UNAVAILABLE'),
            (['LINE.exe'], 5, 'LINE_PROCESS_UNAVAILABLE'),
        ):
            result, calls = discovery(names, last_error=last_error)
            self.assertEqual(result, expected)
            self.assertEqual(calls['closed'], 1)
        result, calls = discovery([], snapshot=reader.ct.c_void_p(-1).value)
        self.assertEqual(result, 'LINE_PROCESS_UNAVAILABLE')
        self.assertEqual(calls['closed'], 0)

    def _window_kernel(self, *, image_path, created_filetime, state=0x1000,
                       kind=0x20000, protect=4, data=b'|' + b'a' * 32 + b'|'):
        calls = {'closed': 0, 'query': 0, 'read_lengths': []}
        base = 4096

        def open_process(*args):
            return 1

        def close(*args):
            calls['closed'] += 1

        def image_name(handle, flags, buffer, capacity):
            buffer.value = str(image_path)
            return True

        def process_times(handle, created, exited, kernel_time, user_time):
            created._obj.dwLowDateTime = created_filetime & 0xFFFFFFFF
            created._obj.dwHighDateTime = created_filetime >> 32
            return True

        def query(handle, address, pointer, size):
            requested = int(address.value or 0)
            if requested not in (0, base):
                return 0
            calls['query'] += 1
            info = pointer._obj
            info.base, info.region_size = base, len(data)
            info.state, info.kind, info.protect = state, kind, protect
            return size

        def read(handle, address, buffer, length, received):
            calls['read_lengths'].append(length)
            payload = data[:length]
            ct.memmove(buffer, payload, len(payload))
            received._obj.value = len(payload)
            return True

        return SimpleNamespace(
            OpenProcess=open_process, CloseHandle=close,
            QueryFullProcessImageNameW=image_name, GetProcessTimes=process_times,
            VirtualQueryEx=query, ReadProcessMemory=read), calls, base

    def test_locator_window_rejects_executable_and_same_handle_filetime_mismatch(self):
        expected = PYTHON / 'fake-LINE.exe'
        expected_filetime = 0x0000000100000002
        for label, image, created in (
            ('executable', PYTHON / 'other.exe', expected_filetime),
            ('filetime', expected, expected_filetime + 1),
        ):
            with self.subTest(label=label):
                kernel, calls, base = self._window_kernel(
                    image_path=image, created_filetime=created)
                with mock.patch.object(probe.ct, 'WinDLL', return_value=kernel):
                    with self.assertRaises(probe.ProbeError):
                        probe.read_locator_window(42, expected, expected_filetime, base)
                self.assertEqual(calls['read_lengths'], [])
                self.assertEqual(calls['closed'], 1)

    def test_locator_window_rejects_nonprivate_and_guard_regions(self):
        expected = PYTHON / 'fake-LINE.exe'
        created = 0x0000000100000002
        for label, kind, protect in (
            ('nonprivate', 0x1000000, 4),
            ('guard', 0x20000, 4 | 0x100),
        ):
            with self.subTest(label=label):
                kernel, calls, base = self._window_kernel(
                    image_path=expected, created_filetime=created,
                    kind=kind, protect=protect)
                with mock.patch.object(probe.ct, 'WinDLL', return_value=kernel):
                    with self.assertRaises(probe.ProbeError):
                        probe.read_locator_window(42, expected, created, base)
                self.assertEqual(calls['read_lengths'], [])
                self.assertEqual(calls['closed'], 1)

    def test_locator_window_is_bounded_and_full_scan_reports_internal_callbacks(self):
        expected = PYTHON / 'fake-LINE.exe'
        created = 0x0000000100000002
        good = b'a' * 32
        data = b'|' + good + b'|' + b'x' * 128
        kernel, calls, base = self._window_kernel(
            image_path=expected, created_filetime=created, data=data)
        with mock.patch.object(probe.ct, 'WinDLL', return_value=kernel):
            window = probe.read_locator_window(42, expected, created, base, max_bytes=64)
        self.assertEqual(len(window), 64)
        self.assertEqual(calls['read_lengths'], [64])
        callbacks = {'window': [], 'created': []}
        with mock.patch.object(probe.ct, 'WinDLL', return_value=kernel):
            found, metrics = probe.find_candidates(
                42, expected, candidate_validator=lambda value: value == good,
                validated_window_recorder=callbacks['window'].append,
                process_created_filetime_recorder=callbacks['created'].append)
        self.assertEqual(found, [good])
        self.assertTrue(metrics['stopped_after_validation'])
        self.assertEqual(callbacks, {'window': [base], 'created': [created]})
        self.assertEqual(calls['read_lengths'], [64, len(data)])
        self.assertEqual(calls['closed'], 2)

    def test_memory_scan_stops_only_after_candidate_validator_accepts(self):
        bad, good = b'1' * 32, b'a' * 32
        data = b'|' + bad + b'|' + bad + b'|' + good + b'|'
        calls = {'read': 0, 'closed': 0}
        def open_process(*args):
            return 1
        def close(*args):
            calls['closed'] += 1
        def image_name(handle, flags, buffer, capacity):
            buffer.value = str(PYTHON / 'fake-LINE.exe')
            return True
        def query(handle, address, pointer, size):
            if address.value:
                return 0
            info = pointer._obj
            info.base, info.region_size = 4096, len(data)
            info.state, info.kind, info.protect = 0x1000, 0x20000, 4
            return size
        def read(handle, address, buffer, length, received):
            calls['read'] += 1
            ct.memmove(buffer, data, len(data))
            received._obj.value = len(data)
            return True
        kernel = SimpleNamespace(OpenProcess=open_process, CloseHandle=close,
            QueryFullProcessImageNameW=image_name, VirtualQueryEx=query, ReadProcessMemory=read)
        checked = []
        def validate(value):
            checked.append(value)
            return value == good
        with mock.patch.object(probe.ct, 'WinDLL', return_value=kernel):
            found, metrics = probe.find_candidates(42, PYTHON / 'fake-LINE.exe', candidate_validator=validate)
        self.assertEqual(found, [good])
        self.assertEqual(checked, [bad, good])
        self.assertEqual(calls, {'read': 1, 'closed': 1})
        self.assertTrue(metrics['stopped_after_validation'])
        with mock.patch.object(probe.ct, 'WinDLL', return_value=kernel):
            found, metrics = probe.find_candidates(42, PYTHON / 'fake-LINE.exe', candidate_validator=lambda _: False)
        self.assertEqual(found, [])
        self.assertFalse(metrics['stopped_after_validation'])

    def test_query_streams_once_after_bootstrap_and_reports_actual_snapshot_age(self):
        work = Path(tempfile.mkdtemp(prefix='reader-lifecycle-'))
        database_dir = work / 'LINE' / 'Data' / 'db'
        database_dir.mkdir(mode=0o777, parents=True)
        path = database_dir / 'test.edb'
        path.write_bytes(b'fixture')
        events = []
        def prefix(source, *, limits):
            if source == path:
                events.append('bootstrap')
            return b'x' * 4096
        def capture(_, directory, *, limits):
            events.append('capture')
            destination = directory / 'snapshot.edb'
            destination.write_bytes(b'x' * 4096)
            return destination, {'captureCompletedAt': dt.datetime.now(dt.timezone.utc).isoformat(), 'sequence': len(events)}
        def acquire(*_):
            events.append('initialize')
            return b'not-a-real-key', {'scanSeconds': 30}
        def scoped(db, args, snapshot, resolver):
            events.append('query')
            self.assertEqual(snapshot['sequence'], 3)
            return {'messages': [], 'count': 0, 'scope': {'snapshot': snapshot}}
        connection = mock.MagicMock()
        connection.__enter__.return_value.version = {}
        try:
            with mock.patch.dict(reader.os.environ, {'LOCALAPPDATA': str(work)}), \
                 mock.patch.object(reader, 'application_runtime_dir', return_value=work), \
                 mock.patch.object(reader, 'verify_client_build', return_value={'verified': True}), \
                 mock.patch.object(reader, 'read_database_prefix', side_effect=prefix), \
                 mock.patch.object(reader, 'capture_snapshot_to', side_effect=capture), \
                 mock.patch.object(reader, 'acquire_passphrase', side_effect=acquire), \
                 mock.patch.object(reader, 'passphrase_matches', return_value=True), \
                 mock.patch.object(reader, 'Connection', return_value=connection), \
                 mock.patch.object(reader, 'read_scoped', side_effect=scoped):
                result = reader.run({'chatName': 'Synthetic', 'dateFrom': '2026-09-11', 'dateTo': '2026-09-11'})
            self.assertEqual(events, ['bootstrap', 'initialize', 'capture', 'query'])
            self.assertTrue(result['freshness']['capturedAfterInitialization'])
            self.assertTrue(result['freshness']['recapturedAfterInitialization'])
            self.assertEqual(result['freshness']['bootstrapKind'], 'stable_database_prefix')
            self.assertFalse(result['freshness']['sourceCurrentAtCompletionVerified'])
            self.assertGreaterEqual(result['freshness']['snapshotAgeMs'], 0)
            self.assertEqual(result['retrievedAt'], result['freshness']['queryCompletedAt'])
            with mock.patch.dict(reader.os.environ, {'LOCALAPPDATA': str(work)}), \
                 mock.patch.object(reader, 'application_runtime_dir', return_value=work), \
                 mock.patch.object(reader, 'verify_client_build', return_value={'verified': True}), \
                 mock.patch.object(reader, 'read_database_prefix', side_effect=prefix), \
                 mock.patch.object(reader, 'capture_snapshot_to', side_effect=capture), \
                 mock.patch.object(reader, 'acquire_passphrase', side_effect=acquire), \
                 mock.patch.object(reader, 'passphrase_matches', return_value=False), \
                 mock.patch.object(reader, 'Connection') as connect:
                with self.assertRaises(reader.ReaderError) as error:
                    reader.run({'chatName': 'Synthetic', 'dateFrom': '2026-09-11', 'dateTo': '2026-09-11'})
                self.assertEqual(error.exception.code, 'SESSION_KEY_CHANGED')
                connect.assert_not_called()
        finally:
            path.unlink()
            for directory in [database_dir, database_dir.parent, database_dir.parent.parent, work]:
                directory.rmdir()


if __name__ == '__main__':
    unittest.main()
