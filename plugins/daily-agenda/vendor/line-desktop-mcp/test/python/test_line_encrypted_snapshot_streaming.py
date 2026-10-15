import dataclasses
import importlib.util
import os
from pathlib import Path
import sqlite3
import struct
import sys
import tempfile
import unittest
from unittest import mock
import uuid


PYTHON = Path(__file__).parents[2] / "src" / "extensions" / "python"
if str(PYTHON) not in sys.path:
    sys.path.insert(0, str(PYTHON))

SPEC = importlib.util.spec_from_file_location(
    "line_encrypted_snapshot_streaming_tests",
    PYTHON / "line_encrypted_snapshot.py",
)
snapshot = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = snapshot
SPEC.loader.exec_module(snapshot)


class _ReadRecorder:
    def __init__(self, stream, sizes):
        self._stream = stream
        self._sizes = sizes

    def __enter__(self):
        return self

    def __exit__(self, *args):
        return self._stream.__exit__(*args)

    def __getattr__(self, name):
        return getattr(self._stream, name)

    def read(self, size=-1):
        self._sizes.append(size)
        return self._stream.read(size)


class _ExtraByteReader(_ReadRecorder):
    def __init__(self, stream):
        super().__init__(stream, [])
        self._injected = False

    def read(self, size=-1):
        data = self._stream.read(size)
        if not data and size != 0 and not self._injected:
            self._injected = True
            return b"x"
        return data


class StreamingEncryptedSnapshotTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.gettempdir()) / ("streaming-snapshot-test-" + uuid.uuid4().hex)
        self.work.mkdir()
        self.database = self.work / "source.edb"

        connection = sqlite3.connect(self.database)
        try:
            connection.execute("PRAGMA page_size=4096")
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            connection.execute("PRAGMA wal_autocheckpoint=0")
            connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
            connection.commit()
            connection.execute("INSERT INTO sample(value) VALUES ('first')")
            connection.commit()
            connection.execute("UPDATE sample SET value='latest' WHERE id=1")
            connection.commit()
            self.database_bytes = self.database.read_bytes()
            self.wal_path = Path(str(self.database) + "-wal")
            self.wal_bytes = self.wal_path.read_bytes()
        finally:
            connection.close()

        self.database.write_bytes(self.database_bytes)
        self.wal_path.write_bytes(self.wal_bytes)
        Path(str(self.database) + "-shm").unlink(missing_ok=True)

    def tearDown(self):
        for child in tuple(self.work.iterdir()):
            if child.is_dir() and not child.is_symlink():
                for grandchild in tuple(child.iterdir()):
                    if grandchild.is_file() or grandchild.is_symlink():
                        grandchild.unlink()
                child.rmdir()
            elif child.is_file() or child.is_symlink():
                child.unlink()
        self.work.rmdir()

    def _directory(self, name):
        directory = self.work / name
        directory.mkdir()
        return directory

    def _remove_directory(self, directory):
        snapshot.cleanup_snapshot(directory)
        for child in tuple(directory.iterdir()):
            child.unlink()
        directory.rmdir()

    def _limits(self, *, source=None, wal=None, total=None):
        return snapshot.SnapshotLimits(
            max_source_bytes=len(self.database_bytes) if source is None else source,
            max_wal_bytes=len(self.wal_bytes) if wal is None else wal,
            max_snapshot_bytes=(len(self.database_bytes) + len(self.wal_bytes)) if total is None else total,
        )

    @staticmethod
    def _valid_uncommitted_frame(committed_wal):
        page_size = struct.unpack_from(">I", committed_wal, 8)[0]
        frame_size = 24 + page_size
        frame_count = (len(committed_wal) - 32) // frame_size
        last_offset = 32 + (frame_count - 1) * frame_size
        page_number = struct.unpack_from(">I", committed_wal, last_offset)[0]
        salt1, salt2 = struct.unpack_from(">II", committed_wal, 16)
        state = struct.unpack_from(">II", committed_wal, last_offset + 16)
        page = committed_wal[last_offset + 24:last_offset + frame_size]
        first_eight = struct.pack(">II", page_number, 0)
        magic = struct.unpack_from(">I", committed_wal, 0)[0]
        checksum = snapshot._wal_checksum(
            first_eight + page,
            big_endian=magic == snapshot._WAL_MAGIC_BIG_CHECKSUM,
            state=state,
        )
        return struct.pack(">IIIIII", page_number, 0, salt1, salt2, *checksum) + page

    def test_limit_defaults_and_strict_decimal_validation(self):
        settings = {
            "LINE_MCP_MAX_SOURCE_BYTES",
            "LINE_MCP_MAX_WAL_BYTES",
            "LINE_MCP_MAX_SNAPSHOT_BYTES",
        }
        clean_environment = {key: value for key, value in os.environ.items() if key not in settings}
        with mock.patch.dict(os.environ, clean_environment, clear=True):
            limits = snapshot.load_snapshot_limits()
        self.assertEqual(limits.max_source_bytes, 2 * 1024 ** 3)
        self.assertEqual(limits.max_wal_bytes, 256 * 1024 ** 2)
        self.assertEqual(limits.max_snapshot_bytes, 2304 * 1024 ** 2)

        for invalid in ("", "0", "-1", "+1", " 1", "1 ", "1.0", "１２", "9" * 5000):
            with self.subTest(invalid=invalid), mock.patch.dict(
                os.environ, {"LINE_MCP_MAX_SOURCE_BYTES": invalid}, clear=False
            ):
                with self.assertRaises(snapshot.SnapshotError) as caught:
                    snapshot.load_snapshot_limits()
                self.assertEqual(caught.exception.code, "SOURCE_LIMIT_INVALID")
                if invalid:
                    self.assertNotIn(invalid, str(caught.exception))
                self.assertIsNone(caught.exception.details)

    def test_hard_limit_boundaries_are_accepted_and_one_byte_over_is_rejected(self):
        maxima = {
            "LINE_MCP_MAX_SOURCE_BYTES": 8 * 1024 ** 3,
            "LINE_MCP_MAX_WAL_BYTES": 1024 ** 3,
            "LINE_MCP_MAX_SNAPSHOT_BYTES": 9 * 1024 ** 3,
        }
        with mock.patch.dict(
            os.environ, {key: str(value) for key, value in maxima.items()}, clear=False
        ):
            limits = snapshot.load_snapshot_limits()
        self.assertEqual(limits, snapshot.SnapshotLimits(
            max_source_bytes=maxima["LINE_MCP_MAX_SOURCE_BYTES"],
            max_wal_bytes=maxima["LINE_MCP_MAX_WAL_BYTES"],
            max_snapshot_bytes=maxima["LINE_MCP_MAX_SNAPSHOT_BYTES"],
        ))

        for setting, hard_max in maxima.items():
            with self.subTest(setting=setting), mock.patch.dict(
                os.environ, {setting: str(hard_max + 1)}, clear=False
            ):
                with self.assertRaises(snapshot.SnapshotError) as caught:
                    snapshot.load_snapshot_limits()
                self.assertEqual(caught.exception.code, "SOURCE_LIMIT_INVALID")
                self.assertIsNone(caught.exception.details)

        invalid_limits = snapshot.SnapshotLimits(
            max_source_bytes=True,
            max_wal_bytes=1,
            max_snapshot_bytes=1,
        )
        with self.assertRaises(snapshot.SnapshotError) as caught:
            snapshot.read_database_prefix(self.database, limits=invalid_limits)
        self.assertEqual(caught.exception.code, "SOURCE_LIMIT_INVALID")

    def test_exact_source_wal_and_aggregate_limits_are_allowed_then_one_less_fails(self):
        exact_directory = self._directory("exact")
        path, metadata = snapshot.capture_snapshot_to(
            self.database, exact_directory, limits=self._limits()
        )
        self.assertEqual(path, exact_directory / "snapshot.edb")
        self.assertEqual(metadata["storage"], "streamed_encrypted_files")
        self.assertEqual(path.read_bytes(), self.database_bytes)
        self._remove_directory(exact_directory)

        cases = (
            (
                "database",
                self._limits(source=len(self.database_bytes) - 1),
                len(self.database_bytes),
                len(self.database_bytes) - 1,
                "LINE_MCP_MAX_SOURCE_BYTES",
            ),
            (
                "wal",
                self._limits(wal=len(self.wal_bytes) - 1),
                len(self.wal_bytes),
                len(self.wal_bytes) - 1,
                "LINE_MCP_MAX_WAL_BYTES",
            ),
            (
                "snapshot",
                self._limits(total=len(self.database_bytes) + len(self.wal_bytes) - 1),
                len(self.database_bytes) + len(self.wal_bytes),
                len(self.database_bytes) + len(self.wal_bytes) - 1,
                "LINE_MCP_MAX_SNAPSHOT_BYTES",
            ),
        )
        for source_kind, limits, source_bytes, max_bytes, setting in cases:
            directory = self._directory("too-large-" + source_kind)
            with self.subTest(source_kind=source_kind):
                with self.assertRaises(snapshot.SnapshotError) as caught:
                    snapshot.capture_snapshot_to(self.database, directory, limits=limits)
                self.assertEqual(caught.exception.code, "SOURCE_TOO_LARGE")
                self.assertEqual(caught.exception.details, {
                    "sourceKind": source_kind,
                    "sourceBytes": source_bytes,
                    "maxBytes": max_bytes,
                    "setting": setting,
                })
            self._remove_directory(directory)

    def test_streamed_wal_round_trip_and_tail_truncation(self):
        directory = self._directory("round-trip")
        path, metadata = snapshot.capture_snapshot_to(
            self.database, directory, limits=self._limits()
        )
        self.assertGreaterEqual(metadata["wal"]["committedFrames"], 1)
        connection = sqlite3.connect(path)
        try:
            self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "latest")
        finally:
            connection.close()
        self._remove_directory(directory)

        committed, _ = snapshot._validate_wal(self.wal_bytes, 4096)
        uncommitted = self._valid_uncommitted_frame(committed)
        self.wal_path.write_bytes(committed + uncommitted)
        directory = self._directory("truncated-tail")
        path, metadata = snapshot.capture_snapshot_to(
            self.database,
            directory,
            limits=self._limits(wal=len(committed + uncommitted), total=len(self.database_bytes) + len(committed + uncommitted)),
        )
        copied_wal = Path(str(path) + "-wal")
        self.assertEqual(copied_wal.read_bytes(), committed)
        self.assertEqual(metadata["wal"]["tailReason"], "uncommitted_tail")
        self.assertEqual(metadata["wal"]["tailBytesDiscarded"], len(uncommitted))
        self._remove_directory(directory)

    def test_partial_and_corrupt_tails_are_truncated_after_the_last_commit(self):
        committed, _ = snapshot._validate_wal(self.wal_bytes, 4096)
        page_size = struct.unpack_from(">I", committed, 8)[0]
        frame_size = 24 + page_size

        checksum_tail = bytearray(committed[-frame_size:])
        checksum_tail[-1] ^= 1
        salt_tail = bytearray(committed[-frame_size:])
        original_salt = struct.unpack_from(">I", salt_tail, 8)[0]
        struct.pack_into(">I", salt_tail, 8, original_salt ^ 1)
        page_number_tail = bytearray(committed[-frame_size:])
        struct.pack_into(">I", page_number_tail, 0, 0)
        cases = (
            ("partial", b"partial-tail", "incomplete_frame"),
            ("checksum", bytes(checksum_tail), "checksum_mismatch"),
            ("salt", bytes(salt_tail), "salt_mismatch"),
            ("page-number", bytes(page_number_tail), "invalid_page_number"),
        )
        for name, tail, reason in cases:
            with self.subTest(name=name):
                source_wal = committed + tail
                self.wal_path.write_bytes(source_wal)
                directory = self._directory("tail-" + name)
                limits = snapshot.SnapshotLimits(
                    max_source_bytes=len(self.database_bytes),
                    max_wal_bytes=len(source_wal),
                    max_snapshot_bytes=len(self.database_bytes) + len(source_wal),
                )
                path, metadata = snapshot.capture_snapshot_to(
                    self.database, directory, limits=limits
                )
                self.assertEqual(Path(str(path) + "-wal").read_bytes(), committed)
                self.assertEqual(metadata["wal"]["tailReason"], reason)
                self.assertEqual(metadata["wal"]["tailBytesDiscarded"], len(tail))
                self._remove_directory(directory)
        self.wal_path.write_bytes(self.wal_bytes)

    def test_absent_empty_and_header_only_wal_leave_no_scratch_wal(self):
        cases = (
            ("absent", None),
            ("empty", b""),
            ("header_only", self.wal_bytes[:32]),
        )
        for state, wal in cases:
            with self.subTest(state=state):
                if wal is None:
                    self.wal_path.unlink(missing_ok=True)
                else:
                    self.wal_path.write_bytes(wal)
                directory = self._directory("wal-" + state)
                limits = snapshot.SnapshotLimits(
                    max_source_bytes=len(self.database_bytes),
                    max_wal_bytes=max(1, len(self.wal_bytes)),
                    max_snapshot_bytes=len(self.database_bytes) + max(1, len(self.wal_bytes)),
                )
                path, metadata = snapshot.capture_snapshot_to(
                    self.database, directory, limits=limits
                )
                self.assertEqual(metadata["wal"]["sourceState"], state)
                self.assertFalse(Path(str(path) + "-wal").exists())
                self._remove_directory(directory)
        self.wal_path.write_bytes(self.wal_bytes)

    def test_incremental_wal_validation_preserves_closed_failure_codes(self):
        corrupt_header = bytearray(self.wal_bytes)
        corrupt_header[24] ^= 1
        committed, _ = snapshot._validate_wal(self.wal_bytes, 4096)
        uncommitted = self._valid_uncommitted_frame(committed)
        cases = (
            ("bad-header", bytes(corrupt_header), "WAL_HEADER_INVALID"),
            ("no-commit", committed[:32] + uncommitted, "WAL_NO_VALID_COMMIT"),
        )
        for name, wal, code in cases:
            with self.subTest(name=name):
                self.wal_path.write_bytes(wal)
                directory = self._directory(name)
                limits = snapshot.SnapshotLimits(
                    max_source_bytes=len(self.database_bytes),
                    max_wal_bytes=len(wal),
                    max_snapshot_bytes=len(self.database_bytes) + len(wal),
                )
                with self.assertRaises(snapshot.SnapshotError) as caught:
                    snapshot.capture_snapshot_to(self.database, directory, limits=limits)
                self.assertEqual(caught.exception.code, code)
                self.assertEqual(tuple(directory.iterdir()), ())
                directory.rmdir()
        self.wal_path.write_bytes(self.wal_bytes)

    def test_copy_and_hash_order_retries_a_drifted_four_step_observation(self):
        calls = []
        original = snapshot._stream_observe

        def observe(*args, **kwargs):
            result = original(*args, **kwargs)
            calls.append((Path(args[0]).name, kwargs.get("destination") is not None))
            if len(calls) == 4:
                result = dataclasses.replace(result, digest=b"observed-drift")
            return result

        directory = self._directory("drift")
        with mock.patch.object(snapshot, "_stream_observe", side_effect=observe):
            path, metadata = snapshot.capture_snapshot_to(
                self.database, directory, limits=self._limits()
            )
        self.assertEqual(metadata["attempts"], 2)
        expected_attempt = [
            ("source.edb", True),
            ("source.edb-wal", True),
            ("source.edb-wal", False),
            ("source.edb", False),
        ]
        self.assertEqual(calls, expected_attempt + expected_attempt)
        self.assertTrue(path.exists())
        self._remove_directory(directory)

    def test_database_prefix_retries_three_double_observations_before_busy(self):
        comparisons = 0

        def drift(_left, _right):
            nonlocal comparisons
            comparisons += 1
            return False

        with mock.patch.object(snapshot, "_observations_match", side_effect=drift):
            with self.assertRaises(snapshot.SnapshotError) as caught:
                snapshot.read_database_prefix(self.database, limits=self._limits())
        self.assertEqual(caught.exception.code, "SOURCE_BUSY")
        self.assertEqual(comparisons, snapshot.MAX_CAPTURE_ATTEMPTS)

    def test_change_during_each_source_observation_cleans_and_exhausts_three_attempts(self):
        original = snapshot._stream_observe
        for changed_position in (1, 2, 3, 4):
            with self.subTest(changed_position=changed_position):
                directory = self._directory('changed-at-' + str(changed_position))
                calls = 0

                def changing(*args, **kwargs):
                    nonlocal calls
                    result = original(*args, **kwargs)
                    calls += 1
                    if calls % changed_position == 0:
                        raise snapshot._SourceChanged()
                    return result

                with mock.patch.object(snapshot, '_stream_observe', side_effect=changing):
                    with self.assertRaises(snapshot.SnapshotError) as caught:
                        snapshot.capture_snapshot_to(self.database, directory, limits=self._limits())
                self.assertEqual(caught.exception.code, 'SOURCE_BUSY')
                self.assertEqual(calls, changed_position * snapshot.MAX_CAPTURE_ATTEMPTS)
                self.assertEqual(list(directory.iterdir()), [])
                self.assertEqual(self.database.read_bytes(), self.database_bytes)
                self.assertEqual(self.wal_path.read_bytes(), self.wal_bytes)
                directory.rmdir()

    def test_wal_appearing_between_observations_is_recaptured_before_replay(self):
        self.wal_path.unlink()
        original = snapshot._stream_observe
        calls = 0

        def appearing(*args, **kwargs):
            nonlocal calls
            result = original(*args, **kwargs)
            calls += 1
            if calls == 2:
                self.assertFalse(result.present)
                self.wal_path.write_bytes(self.wal_bytes)
            return result

        directory = self._directory('wal-appeared')
        with mock.patch.object(snapshot, '_stream_observe', side_effect=appearing):
            path, metadata = snapshot.capture_snapshot_to(self.database, directory, limits=self._limits())
        self.assertEqual(metadata['attempts'], 2)
        with sqlite3.connect(path) as replay:
            self.assertEqual(replay.execute('SELECT value FROM sample').fetchone(), ('latest',))
        replay.close()
        self._remove_directory(directory)

    def test_prefix_and_snapshot_source_reads_are_explicitly_bounded(self):
        large = self.work / "large.edb"
        content = bytearray(3 * 1024 * 1024)
        content[16:18] = (4096).to_bytes(2, "big")
        large.write_bytes(content)
        sizes = []
        original_open = Path.open

        def tracked_open(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            mode = args[0] if args else kwargs.get("mode", "r")
            if Path(path) == large and mode == "rb":
                return _ReadRecorder(stream, sizes)
            return stream

        directory = self._directory("bounded-reads")
        limits = snapshot.SnapshotLimits(
            max_source_bytes=len(content),
            max_wal_bytes=1,
            max_snapshot_bytes=len(content),
        )
        with mock.patch.object(Path, "open", new=tracked_open):
            prefix = snapshot.read_database_prefix(large, limits=limits)
            path, _ = snapshot.capture_snapshot_to(large, directory, limits=limits)
        self.assertEqual(prefix, bytes(content[:4096]))
        self.assertEqual(path.stat().st_size, len(content))
        self.assertTrue(sizes)
        self.assertNotIn(-1, sizes)
        self.assertLessEqual(max(sizes), 1024 * 1024)
        self._remove_directory(directory)

    def test_bytes_actually_read_enforce_source_and_aggregate_caps(self):
        original_open = Path.open

        def growing_open(path, *args, **kwargs):
            stream = original_open(path, *args, **kwargs)
            mode = args[0] if args else kwargs.get("mode", "r")
            if Path(path) == self.database and mode == "rb":
                return _ExtraByteReader(stream)
            return stream

        size = len(self.database_bytes)
        cases = (
            ("database", size, size + 1, "LINE_MCP_MAX_SOURCE_BYTES"),
            ("snapshot", size + 1, size, "LINE_MCP_MAX_SNAPSHOT_BYTES"),
        )
        for source_kind, source_limit, aggregate_limit, setting in cases:
            with self.subTest(source_kind=source_kind), mock.patch.object(
                Path, "open", new=growing_open
            ):
                with self.assertRaises(snapshot.SnapshotError) as caught:
                    snapshot._stream_observe(
                        self.database,
                        optional=False,
                        destination=None,
                        max_bytes=source_limit,
                        source_kind="database",
                        setting="LINE_MCP_MAX_SOURCE_BYTES",
                        aggregate_base=0,
                        aggregate_limit=aggregate_limit,
                    )
                self.assertEqual(caught.exception.code, "SOURCE_TOO_LARGE")
                self.assertEqual(caught.exception.details, {
                    "sourceKind": source_kind,
                    "sourceBytes": size + 1,
                    "maxBytes": size,
                    "setting": setting,
                })

    def test_destination_is_exclusive_and_cleanup_removes_only_fixed_files(self):
        directory = self._directory("exclusive")
        occupied = directory / "snapshot.edb"
        occupied.write_bytes(b"do-not-overwrite")
        unrelated = directory / "keep.txt"
        unrelated.write_bytes(b"keep")
        with self.assertRaises(snapshot.SnapshotError) as caught:
            snapshot.capture_snapshot_to(self.database, directory, limits=self._limits())
        self.assertEqual(caught.exception.code, "SNAPSHOT_DESTINATION_EXISTS")
        self.assertEqual(occupied.read_bytes(), b"do-not-overwrite")

        for name in ("snapshot.edb-wal", "snapshot.edb-shm", "snapshot.edb-journal"):
            (directory / name).write_bytes(b"owned")
        snapshot.cleanup_snapshot(directory)
        self.assertEqual(unrelated.read_bytes(), b"keep")
        self.assertEqual(tuple(directory.iterdir()), (unrelated,))
        unrelated.unlink()
        directory.rmdir()

    def test_disk_full_write_error_is_mapped_without_os_details(self):
        stream = mock.Mock()
        stream.write.side_effect = OSError(28, "sensitive operating system detail")
        with self.assertRaises(snapshot.SnapshotError) as caught:
            snapshot._write_destination(stream, b"bytes")
        self.assertEqual(caught.exception.code, "SNAPSHOT_DISK_FULL")
        self.assertEqual(str(caught.exception), "SNAPSHOT_DISK_FULL")
        self.assertIsNone(caught.exception.details)


if __name__ == "__main__":
    unittest.main()
