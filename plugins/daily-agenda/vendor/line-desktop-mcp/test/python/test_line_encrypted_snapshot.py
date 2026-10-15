import importlib.util
from pathlib import Path
import os
import sqlite3
import struct
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock
import uuid

PYTHON = Path(__file__).parents[2] / 'src' / 'extensions' / 'python'
if str(PYTHON) not in sys.path:
    sys.path.insert(0, str(PYTHON))

SPEC = importlib.util.spec_from_file_location(
    "line_encrypted_snapshot",
    PYTHON / "line_encrypted_snapshot.py",
)
snapshot = importlib.util.module_from_spec(SPEC)
sys.modules[SPEC.name] = snapshot
SPEC.loader.exec_module(snapshot)

import line_sqlite_engine as cipher_engine


class EncryptedSnapshotTests(unittest.TestCase):
    def setUp(self):
        base = Path(tempfile.gettempdir())
        self.work = base / ("snapshot-test-" + uuid.uuid4().hex)
        self.work.mkdir()
        self.database = self.work / "source.edb"

        connection = sqlite3.connect(self.database)
        try:
            self.assertEqual(connection.execute("PRAGMA page_size=4096").fetchone(), None)
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchone()[0], "wal")
            connection.execute("PRAGMA wal_autocheckpoint=0")
            connection.execute("CREATE TABLE sample(id INTEGER PRIMARY KEY, value TEXT)")
            connection.commit()
            connection.execute("INSERT INTO sample(value) VALUES ('first')")
            connection.commit()
            connection.execute("UPDATE sample SET value='latest' WHERE id=1")
            connection.commit()
            self.database_bytes = self.database.read_bytes()
            self.wal_bytes = Path(str(self.database) + "-wal").read_bytes()
        finally:
            connection.close()

        # Closing the last SQLite connection may checkpoint/delete the WAL.
        # Restore the exact pre-close pair as an inert synthetic source fixture.
        self.database.write_bytes(self.database_bytes)
        self.wal_path = Path(str(self.database) + "-wal")
        self.wal_path.write_bytes(self.wal_bytes)
        shm_path = Path(str(self.database) + "-shm")
        if shm_path.exists():
            shm_path.unlink()

    def tearDown(self):
        # This test owns the UUID directory; remove only its direct children.
        for child in tuple(self.work.iterdir()):
            if child.is_file() or child.is_symlink():
                child.unlink()
        self.work.rmdir()

    @staticmethod
    def _frame_details(wal):
        page_size = struct.unpack_from(">I", wal, 8)[0]
        frame_size = 24 + page_size
        count = (len(wal) - 32) // frame_size
        commits = []
        for index in range(count):
            offset = 32 + index * frame_size
            if struct.unpack_from(">I", wal, offset + 4)[0]:
                commits.append(index + 1)
        return page_size, frame_size, count, commits

    @staticmethod
    def _valid_uncommitted_frame(committed_wal):
        page_size, frame_size, count, _ = EncryptedSnapshotTests._frame_details(committed_wal)
        last_offset = 32 + (count - 1) * frame_size
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
        header = struct.pack(">IIIIII", page_number, 0, salt1, salt2, *checksum)
        return header + page

    def _write_wal(self, value):
        self.wal_path.write_bytes(value)

    def _assert_code(self, code):
        with self.assertRaises(snapshot.SnapshotError) as caught:
            snapshot.capture_snapshot(self.database)
        self.assertEqual(caught.exception.code, code)
        self.assertEqual(str(caught.exception), code)

    def test_real_sqlite_wal_round_trip_uses_latest_committed_page(self):
        database, wal, metadata = snapshot.capture_snapshot(self.database)
        self.assertEqual(database, self.database_bytes)
        self.assertIsNotNone(wal)
        self.assertEqual(metadata["snapshotKind"], "observational_quiescent_copy")
        self.assertEqual(metadata["atomicity"], "not_mathematically_atomic")
        self.assertTrue(metadata["sourceStable"])
        self.assertLessEqual(metadata['captureStartedAt'], metadata['captureCompletedAt'])
        self.assertGreaterEqual(metadata['captureDurationMs'], 0)
        self.assertGreaterEqual(metadata["wal"]["committedFrames"], 1)

        replay = self.work / "replay.edb"
        replay.write_bytes(database)
        Path(str(replay) + "-wal").write_bytes(wal)
        connection = sqlite3.connect(replay)
        try:
            self.assertEqual(connection.execute("SELECT value FROM sample").fetchone()[0], "latest")
        finally:
            connection.close()

    def test_sqlite3mc_encrypted_wal_validates_and_replays_in_cipher_engine(self):
        try:
            cipher_engine.verified_dll_path()
        except cipher_engine.ReaderError:
            self.skipTest("configured SQLite3MultipleCiphers DLL is unavailable")
        encrypted = self.work / "encrypted.edb"
        passphrase = b"synthetic-wal-test-key"
        with cipher_engine.Connection(encrypted, passphrase, readonly=False) as connection:
            connection.execute("PRAGMA page_size=4096")
            self.assertEqual(connection.execute("PRAGMA journal_mode=WAL").fetchall(), [("wal",)])
            connection.execute("PRAGMA wal_autocheckpoint=0")
            connection.execute("CREATE TABLE encrypted_sample(id INTEGER PRIMARY KEY, value TEXT)")
            connection.execute("INSERT INTO encrypted_sample(value) VALUES ('first')")
            connection.execute("UPDATE encrypted_sample SET value='latest' WHERE id=1")
            encrypted_database = encrypted.read_bytes()
            encrypted_wal_path = Path(str(encrypted) + "-wal")
            encrypted_wal = encrypted_wal_path.read_bytes()

        # Restore the exact pre-close pair after SQLite's close-time checkpoint.
        encrypted.write_bytes(encrypted_database)
        encrypted_wal_path.write_bytes(encrypted_wal)
        encrypted_shm_path = Path(str(encrypted) + "-shm")
        if encrypted_shm_path.exists():
            encrypted_shm_path.unlink()
        self.assertNotEqual(encrypted_database[:16], b"SQLite format 3\0")

        database, wal, metadata = snapshot.capture_snapshot(encrypted)
        self.assertIsNotNone(wal)
        self.assertGreaterEqual(metadata["wal"]["committedFrames"], 1)
        replay = self.work / "encrypted-replay.edb"
        replay.write_bytes(database)
        Path(str(replay) + "-wal").write_bytes(wal)
        with cipher_engine.Connection(replay, passphrase, readonly=True) as connection:
            rows = connection.execute("SELECT value FROM encrypted_sample").fetchall()
        self.assertEqual(rows, [("latest",)])

    def test_empty_and_valid_header_only_wal_have_no_committed_pair(self):
        self._write_wal(b"")
        _, wal, metadata = snapshot.capture_snapshot(self.database)
        self.assertIsNone(wal)
        self.assertEqual(metadata["wal"]["sourceState"], "empty")

        self._write_wal(self.wal_bytes[:32])
        _, wal, metadata = snapshot.capture_snapshot(self.database)
        self.assertIsNone(wal)
        self.assertEqual(metadata["wal"]["sourceState"], "header_only")

    def test_missing_wal_is_distinct_from_empty_wal(self):
        self.wal_path.unlink()
        _, wal, metadata = snapshot.capture_snapshot(self.database)
        self.assertIsNone(wal)
        self.assertEqual(metadata["wal"]["sourceState"], "absent")

    def test_malformed_or_corrupt_wal_header_fails_closed(self):
        self._write_wal(self.wal_bytes[:17])
        self._assert_code("WAL_HEADER_INVALID")

        corrupt = bytearray(self.wal_bytes)
        corrupt[24] ^= 1
        self._write_wal(corrupt)
        self._assert_code("WAL_HEADER_INVALID")

    def test_partial_tail_after_valid_commit_is_salvaged_and_reported(self):
        committed, _ = snapshot._validate_wal(self.wal_bytes, 4096)
        self.assertIsNotNone(committed)
        self._write_wal(committed + b"partial-tail")
        _, returned, metadata = snapshot.capture_snapshot(self.database)
        self.assertEqual(returned, committed)
        self.assertEqual(metadata["wal"]["tailReason"], "incomplete_frame")
        self.assertEqual(metadata["wal"]["tailBytesDiscarded"], len(b"partial-tail"))

    def test_valid_uncommitted_end_is_excluded_at_last_commit(self):
        committed, _ = snapshot._validate_wal(self.wal_bytes, 4096)
        tail = self._valid_uncommitted_frame(committed)
        self._write_wal(committed + tail)
        _, returned, metadata = snapshot.capture_snapshot(self.database)
        self.assertEqual(returned, committed)
        self.assertEqual(metadata["wal"]["tailReason"], "uncommitted_tail")
        self.assertEqual(metadata["wal"]["validFrames"], metadata["wal"]["committedFrames"] + 1)

    def test_salt_mismatch_after_commit_is_salvaged_but_before_commit_fails(self):
        committed, _ = snapshot._validate_wal(self.wal_bytes, 4096)
        page_size, frame_size, _, _ = self._frame_details(committed)
        tail = bytearray(committed[-frame_size:])
        original_salt = struct.unpack_from(">I", tail, 8)[0]
        struct.pack_into(">I", tail, 8, original_salt ^ 1)
        self._write_wal(committed + tail)
        _, returned, metadata = snapshot.capture_snapshot(self.database)
        self.assertEqual(returned, committed)
        self.assertEqual(metadata["wal"]["tailReason"], "salt_mismatch")

        first_frame = bytearray(self.wal_bytes[32:32 + 24 + page_size])
        struct.pack_into(">I", first_frame, 8, original_salt ^ 1)
        self._write_wal(self.wal_bytes[:32] + first_frame)
        self._assert_code("WAL_NO_VALID_COMMIT")

    def test_valid_frames_without_any_commit_fail_closed(self):
        committed, _ = snapshot._validate_wal(self.wal_bytes, 4096)
        uncommitted = self._valid_uncommitted_frame(committed)
        self._write_wal(committed[:32] + uncommitted)
        self._assert_code("WAL_NO_VALID_COMMIT")

    def test_observed_drift_retries_exactly_three_times_without_accepting(self):
        comparisons = 0

        def report_drift(left, right):
            nonlocal comparisons
            comparisons += 1
            return False

        with mock.patch.object(snapshot, "_observations_match", side_effect=report_drift):
            self._assert_code("SOURCE_BUSY")
        self.assertEqual(comparisons, snapshot.MAX_CAPTURE_ATTEMPTS)

    def test_identity_change_is_not_considered_stable(self):
        one = snapshot._Observation(True, b"x", b"digest", 1, (1, 10), 20, 30)
        replacement = snapshot._Observation(True, None, b"digest", 1, (1, 11), 20, 30)
        self.assertFalse(snapshot._observations_match(one, replacement))

    def test_windows_path_and_handle_ctime_semantics_do_not_cause_false_drift(self):
        def info(ctime):
            return SimpleNamespace(
                st_dev=1, st_ino=2, st_size=4096,
                st_mtime_ns=100, st_ctime_ns=ctime,
            )

        path_before = info(200)
        path_after = info(200)
        opened_before = info(300)
        opened_after = info(300)
        self.assertTrue(snapshot._read_stats_stable(
            path_before, opened_before, opened_after, path_after, 4096
        ))
        opened_after.st_ctime_ns = 301
        self.assertFalse(snapshot._read_stats_stable(
            path_before, opened_before, opened_after, path_after, 4096
        ))

    def test_reparse_source_is_rejected_when_symlinks_are_available(self):
        link = self.work / "linked.edb"
        try:
            os.symlink(self.database, link)
        except (OSError, NotImplementedError):
            self.skipTest("file symlinks are unavailable for this account")
        with self.assertRaises(snapshot.SnapshotError) as caught:
            snapshot.capture_snapshot(link)
        self.assertEqual(caught.exception.code, "SOURCE_REPARSE")


if __name__ == "__main__":
    unittest.main()
