"""Tests for the recentChats offset pagination patch.

Covers: validate_recent_scope with offset, read_recent_chats slicing/hasMore,
default behavior without offset unchanged.
"""
import datetime as dt
import sqlite3
import sys
import unittest
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parents[2] / 'src' / 'extensions' / 'python'))
from line_scoped_core import ReaderError, read_recent_chats, validate_scope, reference

TZ = dt.timezone(dt.timedelta(hours=8))
NOW = dt.datetime(2026, 9, 24, 12, 0, tzinfo=TZ)


class RecentOffsetScopeTests(unittest.TestCase):
    def test_offset_accepted_in_scope(self):
        scope = validate_scope({'mode': 'recentChats', 'days': 14, 'offset': 0})
        self.assertEqual(scope['offset'], 0)

    def test_offset_999_accepted(self):
        scope = validate_scope({'mode': 'recentChats', 'days': 14, 'offset': 999})
        self.assertEqual(scope['offset'], 999)

    def test_no_offset_unchanged_key_count(self):
        """Without offset, validate_scope returns same number of keys as before."""
        scope = validate_scope({'mode': 'recentChats', 'days': 14})
        self.assertEqual(len(scope), 3)  # mode, days, limit
        self.assertNotIn('offset', scope)

    def test_offset_negative_rejected(self):
        with self.assertRaises(ReaderError):
            validate_scope({'mode': 'recentChats', 'days': 14, 'offset': -1})

    def test_offset_1000_rejected(self):
        with self.assertRaises(ReaderError):
            validate_scope({'mode': 'recentChats', 'days': 14, 'offset': 1000})

    def test_offset_float_rejected(self):
        with self.assertRaises(ReaderError):
            validate_scope({'mode': 'recentChats', 'days': 14, 'offset': 1.5})

    def test_offset_string_rejected(self):
        with self.assertRaises(ReaderError):
            validate_scope({'mode': 'recentChats', 'days': 14, 'offset': '0'})

    def test_offset_none_treated_as_absent(self):
        """offset: None is treated same as absent (no offset in result)."""
        scope = validate_scope({'mode': 'recentChats', 'days': 14, 'offset': None})
        self.assertNotIn('offset', scope)


class RecentOffsetReadTests(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.db.executescript('''
          CREATE TABLE _groupChat(_chatMid TEXT, _chatName TEXT);
          CREATE TABLE _chat(_id TEXT, _midType INTEGER);
          CREATE TABLE _contact(_mid TEXT, _displayNameOverridden TEXT, _displayName TEXT);
          CREATE TABLE _profile(_mid TEXT);
          CREATE TABLE _message(_chatId TEXT, _createdTime INTEGER, _text TEXT,
            _contentMetadata BLOB, _contentInfo BLOB);
        ''')

    def tearDown(self):
        self.db.close()

    def stamp(self, day, hour=0):
        return int(dt.datetime.fromisoformat(f'{day}T{hour:02}:00:00+08:00').timestamp() * 1000)

    def add_group(self, chat_id, name, when):
        self.db.execute('INSERT INTO _groupChat VALUES (?,?)', (chat_id, name))
        self.db.execute('INSERT INTO _message VALUES (?,?,?,?,?)',
                        (chat_id, when, 'text', None, None))

    def test_offset_0_same_as_no_offset(self):
        for i in range(5):
            self.add_group(f'g{i}', f'Group {i}', self.stamp('2026-09-24', i))
        no_offset = read_recent_chats(self.db, {'mode': 'recentChats', 'days': 14}, now=NOW)
        with_offset_0 = read_recent_chats(self.db, {'mode': 'recentChats', 'days': 14, 'offset': 0}, now=NOW)
        self.assertEqual(no_offset['chats'], with_offset_0['chats'])
        self.assertEqual(no_offset['hasMore'], with_offset_0['hasMore'])

    def test_offset_pages_through_all_chats(self):
        """Create 5 chats, page with limit=2."""
        for i in range(5):
            self.add_group(f'g{i}', f'Group {i}', self.stamp('2026-09-24', i))

        page1 = read_recent_chats(self.db,
            {'mode': 'recentChats', 'days': 14, 'limit': 2, 'offset': 0}, now=NOW)
        self.assertEqual(len(page1['chats']), 2)
        self.assertTrue(page1['hasMore'])

        page2 = read_recent_chats(self.db,
            {'mode': 'recentChats', 'days': 14, 'limit': 2, 'offset': 2}, now=NOW)
        self.assertEqual(len(page2['chats']), 2)
        self.assertTrue(page2['hasMore'])

        page3 = read_recent_chats(self.db,
            {'mode': 'recentChats', 'days': 14, 'limit': 2, 'offset': 4}, now=NOW)
        self.assertEqual(len(page3['chats']), 1)
        self.assertFalse(page3['hasMore'])

        # All names unique and complete
        all_names = [c['chatName'] for c in page1['chats'] + page2['chats'] + page3['chats']]
        self.assertEqual(len(set(all_names)), 5)

    def test_offset_beyond_total_returns_empty(self):
        self.add_group('g0', 'Group 0', self.stamp('2026-09-24'))
        result = read_recent_chats(self.db,
            {'mode': 'recentChats', 'days': 14, 'offset': 10}, now=NOW)
        self.assertEqual(len(result['chats']), 0)
        self.assertFalse(result['hasMore'])

    def test_hasmore_correct_at_boundary(self):
        """Exactly limit+offset items: hasMore=False when at last page."""
        for i in range(3):
            self.add_group(f'g{i}', f'Group {i}', self.stamp('2026-09-24', i))

        # offset=0, limit=3: exactly 3 chats, no more
        result = read_recent_chats(self.db,
            {'mode': 'recentChats', 'days': 14, 'limit': 3, 'offset': 0}, now=NOW)
        self.assertEqual(len(result['chats']), 3)
        self.assertFalse(result['hasMore'])

        # offset=1, limit=2: exactly 2 chats, no more
        result = read_recent_chats(self.db,
            {'mode': 'recentChats', 'days': 14, 'limit': 2, 'offset': 1}, now=NOW)
        self.assertEqual(len(result['chats']), 2)
        self.assertFalse(result['hasMore'])

        # offset=1, limit=1: 1 chat, hasMore=True (2 remain)
        result = read_recent_chats(self.db,
            {'mode': 'recentChats', 'days': 14, 'limit': 1, 'offset': 1}, now=NOW)
        self.assertEqual(len(result['chats']), 1)
        self.assertTrue(result['hasMore'])


if __name__ == '__main__':
    unittest.main()
