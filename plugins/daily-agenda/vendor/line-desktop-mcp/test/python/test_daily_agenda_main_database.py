"""Tests for the LINE_MCP_MAIN_DATABASE environment variable patch.

Covers: filename-only validation, path traversal rejection, album/keep/chatStats
rejection, presence in filtered list, and upstream behavior when unset.
"""
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

from line_main_database import select_main_database, MainDatabaseError


class MainDatabaseTests(unittest.TestCase):
    def setUp(self):
        TMP_ROOT.mkdir(mode=0o777, exist_ok=True)
        self.temp = tempfile.TemporaryDirectory(dir=TMP_ROOT)
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)

    def _make_dbs(self, *names):
        paths = []
        for name in names:
            p = self.root / name
            p.write_bytes(b'db')
            paths.append(p)
        return paths

    def _assert_code(self, code, callback):
        with self.assertRaises(MainDatabaseError) as ctx:
            callback()
        self.assertEqual(ctx.exception.code, code)

    # --- ENV not set: upstream behavior ---

    def test_no_env_single_main_db(self):
        """Unset env + exactly one non-album/keep/chatStats DB → that one."""
        paths = self._make_dbs('main.edb', 'album_x.edb', 'chatStats_y.edb', 'keep_z.edb')
        result = select_main_database(paths, configured=None)
        self.assertEqual(result.name, 'main.edb')

    def test_no_env_multiple_main_dbs(self):
        """Unset env + multiple main DBs → MAIN_DATABASE_AMBIGUOUS."""
        paths = self._make_dbs('a.edb', 'b.edb')
        self._assert_code('MAIN_DATABASE_AMBIGUOUS',
                          lambda: select_main_database(paths, configured=None))

    def test_no_env_no_main_dbs(self):
        """Unset env + only album/keep/chatStats → MAIN_DATABASE_AMBIGUOUS."""
        paths = self._make_dbs('album_x.edb', 'keep_y.edb')
        self._assert_code('MAIN_DATABASE_AMBIGUOUS',
                          lambda: select_main_database(paths, configured=None))

    # --- ENV set: valid cases ---

    def test_valid_filename_selects_correct_db(self):
        paths = self._make_dbs('a.edb', 'b.edb')
        result = select_main_database(paths, configured='b.edb')
        self.assertEqual(result.name, 'b.edb')

    def test_valid_filename_with_multiple_dbs(self):
        """Configured name disambiguates when multiple main DBs exist."""
        paths = self._make_dbs('qw0b.edb', 'xr1c.edb', 'album_z.edb')
        result = select_main_database(paths, configured='qw0b.edb')
        self.assertEqual(result.name, 'qw0b.edb')

    # --- ENV set: rejection cases ---

    def test_slash_rejected(self):
        paths = self._make_dbs('main.edb')
        self._assert_code('MAIN_DATABASE_INVALID',
                          lambda: select_main_database(paths, configured='sub/main.edb'))

    def test_backslash_rejected(self):
        paths = self._make_dbs('main.edb')
        self._assert_code('MAIN_DATABASE_INVALID',
                          lambda: select_main_database(paths, configured='sub\\main.edb'))

    def test_dotdot_rejected(self):
        paths = self._make_dbs('main.edb')
        self._assert_code('MAIN_DATABASE_INVALID',
                          lambda: select_main_database(paths, configured='..'))

    def test_dotdot_in_name_rejected(self):
        paths = self._make_dbs('main.edb')
        self._assert_code('MAIN_DATABASE_INVALID',
                          lambda: select_main_database(paths, configured='..\\main.edb'))

    def test_absolute_path_rejected(self):
        paths = self._make_dbs('main.edb')
        self._assert_code('MAIN_DATABASE_INVALID',
                          lambda: select_main_database(paths, configured='/tmp/main.edb'))

    def test_windows_absolute_rejected(self):
        paths = self._make_dbs('main.edb')
        self._assert_code('MAIN_DATABASE_INVALID',
                          lambda: select_main_database(paths, configured='C:\\main.edb'))

    def test_nul_byte_rejected(self):
        paths = self._make_dbs('main.edb')
        self._assert_code('MAIN_DATABASE_INVALID',
                          lambda: select_main_database(paths, configured='main\x00.edb'))

    def test_control_char_rejected(self):
        paths = self._make_dbs('main.edb')
        self._assert_code('MAIN_DATABASE_INVALID',
                          lambda: select_main_database(paths, configured='main\x01.edb'))

    def test_empty_string_rejected(self):
        paths = self._make_dbs('main.edb')
        self._assert_code('MAIN_DATABASE_INVALID',
                          lambda: select_main_database(paths, configured=''))

    def test_album_prefix_rejected(self):
        """album_ prefixed names are always rejected even if file exists."""
        paths = self._make_dbs('album_qw0b.edb')
        self._assert_code('MAIN_DATABASE_INVALID',
                          lambda: select_main_database(paths, configured='album_qw0b.edb'))

    def test_keep_prefix_rejected(self):
        paths = self._make_dbs('keep_qw0b.edb')
        self._assert_code('MAIN_DATABASE_INVALID',
                          lambda: select_main_database(paths, configured='keep_qw0b.edb'))

    def test_chatStats_prefix_rejected(self):
        paths = self._make_dbs('chatStats_qw0b.edb')
        self._assert_code('MAIN_DATABASE_INVALID',
                          lambda: select_main_database(paths, configured='chatStats_qw0b.edb'))

    def test_name_not_in_filtered_list(self):
        """Valid filename but doesn't match any filtered (non-album/keep/chatStats) DB."""
        paths = self._make_dbs('a.edb', 'album_b.edb')
        self._assert_code('MAIN_DATABASE_NOT_FOUND',
                          lambda: select_main_database(paths, configured='nonexistent.edb'))

    def test_case_sensitive_match(self):
        """Filename matching is case-sensitive (fail-closed on Windows case-insensitive FS)."""
        paths = self._make_dbs('Main.edb')
        self._assert_code('MAIN_DATABASE_NOT_FOUND',
                          lambda: select_main_database(paths, configured='main.edb'))


if __name__ == '__main__':
    unittest.main()
