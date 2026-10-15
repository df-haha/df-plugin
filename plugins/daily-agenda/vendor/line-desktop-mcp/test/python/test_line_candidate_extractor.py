"""Synthetic-only regressions for optimized extraction and memory chunk edges."""
import ctypes as ct
import importlib.util
from pathlib import Path
import random
import re
import sys
from types import SimpleNamespace
import unittest
from unittest import mock

PYTHON = Path(__file__).parents[2] / 'src' / 'extensions' / 'python'
sys.path.insert(0, str(PYTHON))
spec = importlib.util.spec_from_file_location('candidate_probe', PYTHON / 'line-schema-probe.py')
probe = importlib.util.module_from_spec(spec)
spec.loader.exec_module(probe)
OLD = (re.compile(rb'(?<![0-9A-Za-z])([0-9a-fA-F]{32})(?![0-9A-Za-z])'),
       re.compile(rb'(?<![0-9A-Za-z]\x00)((?:[0-9a-fA-F]\x00){32})(?![0-9A-Za-z]\x00)'))


def oracle(data):
    return ([m.group(1) for m in OLD[0].finditer(data)] +
            [m.group(1)[::2] for m in OLD[1].finditer(data)])


class CandidateExtractorTests(unittest.TestCase):
    def test_probe_has_no_standalone_diagnostic_cli(self):
        self.assertFalse(hasattr(probe, 'main'))
        self.assertFalse(hasattr(probe, 'role'))

    def test_full_buffers_match_previous_boundary_semantics(self):
        key = b'0123456789abcdef' * 2
        wide = key.decode().encode('utf-16le')
        cases = [b'', key, wide]
        for value in (key, wide):
            for left in (b'', b'|', b'G', b'G\0', b'0', b'\xff', b'\0'):
                for right in (b'', b'|', b'G', b'G\0', b'0', b'\xff', b'\0'):
                    cases.append(left + value + right)
        rng = random.Random(20260911)
        for _ in range(2500):
            value = rng.choice((key, wide, b'f' * 31, b'a' * 33))
            cases.append(rng.randbytes(rng.randrange(100)) + value +
                         rng.randbytes(rng.randrange(100)))
        for data in cases:
            self.assertEqual(list(probe.iter_candidate_values(data)), oracle(data))

    def test_unknown_memory_boundaries_are_deferred(self):
        key = b'a' * 32
        wide = b'a\0' * 32
        for data in (b'|' + key, b'|' + wide, b'|' + wide + b'G'):
            self.assertEqual(list(probe.iter_candidate_values(data, complete=False)), [])
        for data in (key + b'|', wide + b'|\0', b'|' + wide + b'|\0'):
            self.assertEqual(list(probe.iter_candidate_values(data, complete=False,
                              start_is_boundary=False)), [])
        self.assertEqual(list(probe.iter_candidate_values(b'||' + wide + b'||',
                         complete=False, start_is_boundary=False)), [key])

    def scan(self, data, *, partial=None, chunk=256, max_bytes=1024*1024):
        base = 4096
        calls = {'closed': 0, 'reads': [], 'validated': []}
        expected = PYTHON / 'fake-LINE.exe'
        def close(*_):
            calls['closed'] += 1
        def image(_, flags, buffer, capacity):
            buffer.value = str(expected)
            return True
        def query(_, address, pointer, size):
            if int(address.value or 0) not in (0, base):
                return 0
            info = pointer._obj
            info.base, info.region_size = base, len(data)
            info.state, info.kind, info.protect = 0x1000, 0x20000, 4
            return size
        def read(_, address, buffer, length, received):
            offset = address.value - base
            amount = partial.get(offset, length) if partial else length
            payload = data[offset:offset + amount]
            ct.memmove(buffer, payload, len(payload))
            received._obj.value = len(payload)
            calls['reads'].append((offset, length, len(payload)))
            return len(payload) == length
        kernel = SimpleNamespace(OpenProcess=lambda *_: 1, CloseHandle=close,
            QueryFullProcessImageNameW=image, VirtualQueryEx=query, ReadProcessMemory=read)
        with mock.patch.object(probe.ct, 'WinDLL', return_value=kernel), \
             mock.patch.object(probe, 'SCAN_CHUNK_BYTES', chunk):
            found, metrics = probe.find_candidates(42, expected,
                max_bytes=max_bytes,
                candidate_validator=lambda value: calls['validated'].append(value) or False)
        self.assertEqual(calls['closed'], 1)
        self.assertEqual(found, [])
        return calls, metrics

    def test_every_ascii_and_utf16_split_recovers_exact_candidate(self):
        key = b'0123456789abcdef' * 2
        for value in (key, key.decode().encode('utf-16le')):
            for split in range(1, len(value) + 1):
                with self.subTest(width=len(value), split=split):
                    calls, _ = self.scan(b'|' * (256 - split) + value + b'|' * 256)
                    self.assertEqual(calls['validated'], [key])

    def test_alphanumeric_right_edge_and_lost_overlap_left_edge_reject(self):
        key = b'a' * 32
        for value, bad_boundary in ((key, b'G'), (b'a\0' * 32, b'G\0')):
            for split in range(1, len(value) + 1):
                calls, _ = self.scan(b'|' * (256 - split) + value + bad_boundary + b'|' * 256)
                self.assertEqual(calls['validated'], [])
            # The second chunk's overlap begins on a key preceded by alnum.
            left = b'G' * 128 if len(value) == 32 else b'G\0' * 64
            calls, _ = self.scan(left + value + b'|' * 400)
            self.assertEqual(calls['validated'], [])

    def test_partial_read_never_stitches_a_candidate_across_unread_gap(self):
        data = b'|' * 184 + b'a' * 16 + b'G' * 56 + b'a' * 16 + b'|' * 256
        calls, metrics = self.scan(data, partial={0: 200})
        self.assertEqual(calls['validated'], [])
        self.assertEqual(calls['reads'][0], (0, 256, 200))
        self.assertEqual(metrics['failed_chunks'], 1)

    def test_region_end_is_complete_but_byte_limit_is_not(self):
        key = b'a' * 32
        for value in (key, b'a\0' * 32):
            data = b'|' * (256 - len(value)) + value
            calls, _ = self.scan(data)
            self.assertEqual(calls['validated'], [key])
            calls, _ = self.scan(data + b'G\0' + b'|' * 256, max_bytes=256)
            self.assertEqual(calls['validated'], [])


if __name__ == '__main__':
    unittest.main()
