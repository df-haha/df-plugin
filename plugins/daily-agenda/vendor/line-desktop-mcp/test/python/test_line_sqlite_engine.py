import os
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest import mock


PYTHON = Path(__file__).parents[2] / 'src' / 'extensions' / 'python'
if str(PYTHON) not in sys.path:
    sys.path.insert(0, str(PYTHON))

import line_sqlite_engine as engine
from line_scoped_core import ReaderError, read_scoped


class SQLiteEngineConfigurationTests(unittest.TestCase):
    def assert_reader_code(self, expected, operation):
        with self.assertRaises(ReaderError) as caught:
            operation()
        self.assertEqual(caught.exception.code, expected)
        self.assertEqual(str(caught.exception), expected)

    def test_requires_an_explicit_absolute_dll_path(self):
        with mock.patch.dict(os.environ, {}, clear=True):
            self.assert_reader_code('ENGINE_DLL_UNCONFIGURED', engine.configured_dll_path)

        candidate = Path(tempfile.gettempdir()) / 'synthetic-sqlite3mc.dll'
        with mock.patch.dict(os.environ, {engine.DLL_ENV: str(candidate)}, clear=True):
            self.assertEqual(engine.configured_dll_path(), candidate)

        for value in ('', ' relative.dll', 'relative.dll', 'not-a-dll.txt', 'bad\0.dll', 123):
            with self.subTest(value=repr(value)):
                self.assert_reader_code(
                    'ENGINE_DLL_INVALID_PATH',
                    lambda value=value: engine.configured_dll_path(value),
                )

    def test_stable_hash_maps_missing_and_untrusted_libraries_to_fixed_codes(self):
        missing = Path(tempfile.gettempdir()) / 'missing-sqlite3mc.dll'
        self.assert_reader_code(
            'ENGINE_DLL_UNAVAILABLE',
            lambda: engine.verified_dll_path(str(missing)),
        )
        synthetic = Path(tempfile.gettempdir()) / 'synthetic-sqlite3mc.dll'
        with mock.patch.object(engine, '_read_file', return_value=SimpleNamespace(
                digest=bytes(32), size=1)):
            self.assert_reader_code(
                'ENGINE_INTEGRITY_FAILED',
                lambda: engine.verified_dll_path(str(synthetic)),
            )

    def test_dynamic_loader_failure_has_no_native_error_text(self):
        synthetic = Path(tempfile.gettempdir()) / 'synthetic-sqlite3mc.dll'
        with mock.patch.object(engine, 'verified_dll_path', return_value=synthetic), \
             mock.patch.object(engine.ct, 'CDLL', side_effect=OSError('synthetic loader error')):
            self.assert_reader_code(
                'ENGINE_DLL_UNAVAILABLE',
                lambda: engine.Connection(
                    Path(tempfile.gettempdir()) / 'synthetic.edb', b'key'
                ),
            )


class SQLiteEngineTests(unittest.TestCase):
    def setUp(self):
        try:
            engine.verified_dll_path()
        except ReaderError as error:
            self.skipTest(f'configured SQLite3MultipleCiphers DLL is unavailable: {error.code}')
        self.work = Path(tempfile.mkdtemp(prefix='engine-test-'))
        self.addCleanup(self._cleanup_work)
        self.database = self.work / 'engine.edb'
        self.passphrase = b'synthetic-engine-test-key'

        with engine.Connection(self.database, self.passphrase, readonly=False) as connection:
            connection.execute(
                'CREATE TABLE _groupChat(_chatMid TEXT, _chatName TEXT, _private TEXT)'
            )
            connection.execute(
                'CREATE TABLE _message('
                '_id, _from, _createdTime, _text, _contentType, _contentMetadata, '
                '_contentInfo, _relatedMessageId, _type, _status, _rev, _chatId, _private)'
            )
            connection.execute('CREATE TABLE _secret(value TEXT)')
            connection.execute(
                "INSERT INTO _groupChat VALUES ('c1','Synthetic Group','hidden')"
            )
            connection.execute(
                "INSERT INTO _message VALUES "
                "(7,1.25,123,'line\nemoji',0,X'00FF',NULL,NULL,0,0,1,'c1','hidden')"
            )
            connection.execute("INSERT INTO _secret VALUES ('hidden')")

    def _cleanup_work(self):
        # This test owns the temporary directory; SQLite creates direct files only.
        if not self.work.exists():
            return
        for child in tuple(self.work.iterdir()):
            if child.is_file() or child.is_symlink():
                child.unlink()
        self.work.rmdir()

    def assert_reader_code(self, expected, operation):
        with self.assertRaises(ReaderError) as caught:
            operation()
        self.assertEqual(caught.exception.code, expected)
        self.assertEqual(str(caught.exception), expected)

    def test_readonly_and_query_only_are_both_active_and_writes_fail(self):
        with engine.Connection(self.database, self.passphrase, readonly=True) as connection:
            self.assertEqual(connection.lib.sqlite3_db_readonly(connection.db, b'main'), 1)
            self.assertEqual(connection.execute('PRAGMA query_only').fetchall(), [(1,)])
            self.assert_reader_code(
                'DATABASE_READ_FAILED',
                lambda: connection.execute("INSERT INTO _groupChat VALUES ('c2','x','x')"),
            )

    def test_gui_identity_passes_the_native_read_authorizer_and_refuses_cross_kind_aliases(self):
        with engine.Connection(self.database, self.passphrase, readonly=False) as connection:
            connection.execute('CREATE TABLE _contact(_mid TEXT, _displayName TEXT, _displayNameOverridden TEXT)')
            connection.execute('CREATE TABLE _chat(_id TEXT, _midType INTEGER)')
            connection.execute("INSERT INTO _contact VALUES ('u1','Another Contact','')")
            connection.execute("INSERT INTO _chat VALUES ('u1',0)")
        args = {'chatName': 'Synthetic Group', 'dateFrom': '2026-09-11', 'dateTo': '2026-09-11',
                'identityOnly': True, 'guiIdentityOnly': True}
        with engine.Connection(self.database, self.passphrase, readonly=True) as connection:
            connection.execute('BEGIN')
            connection.restrict_reads()
            result = read_scoped(connection, args, {})
            self.assertTrue(result['chatIdentity']['guiDisplayNameUnique'])
            self.assertEqual(result['chatIdentity']['kind'], 'group')
            self.assertEqual(result['messages'], [])
        with engine.Connection(self.database, self.passphrase, readonly=False) as connection:
            connection.execute("UPDATE _contact SET _displayName = 'SyntheticGroup (2)'")
        with engine.Connection(self.database, self.passphrase, readonly=True) as connection:
            connection.execute('BEGIN')
            connection.restrict_reads()
            self.assert_reader_code('CHAT_AMBIGUOUS', lambda: read_scoped(connection, args, {}))

    def test_multiple_statements_are_consistently_refused(self):
        with engine.Connection(self.database, self.passphrase, readonly=True) as connection:
            for _ in range(100):
                self.assert_reader_code(
                    'MULTIPLE_STATEMENTS_REFUSED',
                    lambda: connection.execute('SELECT 1; SELECT 2'),
                )

    def test_authorizer_allows_scoped_select_and_converts_sqlite_rows(self):
        with engine.Connection(self.database, self.passphrase, readonly=False) as connection:
            connection.restrict_reads()
            rows = connection.execute(
                'SELECT _id,_from,_text,_contentMetadata,_contentInfo '
                'FROM _message WHERE _chatId = ? AND _createdTime >= ? '
                'AND _createdTime < ? AND instr(_text, ?) > 0',
                ('c1', 100, 200, 'emoji'),
            ).fetchall()
        self.assertEqual(rows, [(7, 1.25, 'line\nemoji', b'\x00\xff', None)])

    def test_authorizer_denies_forbidden_table_and_column(self):
        with engine.Connection(self.database, self.passphrase, readonly=False) as connection:
            connection.restrict_reads()
            self.assert_reader_code(
                'DATABASE_READ_FAILED',
                lambda: connection.execute('SELECT value FROM _secret'),
            )
            self.assert_reader_code(
                'DATABASE_READ_FAILED',
                lambda: connection.execute('SELECT _private FROM _message'),
            )

    def test_authorizer_denies_writes_even_on_a_writable_connection(self):
        with engine.Connection(self.database, self.passphrase, readonly=False) as connection:
            connection.restrict_reads()
            self.assert_reader_code(
                'DATABASE_READ_FAILED',
                lambda: connection.execute("INSERT INTO _groupChat VALUES ('c2','x','x')"),
            )
        with engine.Connection(self.database, self.passphrase, readonly=True) as connection:
            rows = connection.execute(
                'SELECT _chatMid FROM _groupChat ORDER BY _chatMid'
            ).fetchall()
        self.assertEqual(rows, [('c1',)])


if __name__ == '__main__':
    unittest.main()
