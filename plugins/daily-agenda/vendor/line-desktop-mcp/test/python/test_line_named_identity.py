"""Offline regression: unrelated nameless entries must not block a named chat."""
import importlib.util
from pathlib import Path
import sqlite3
import unittest

root = Path(__file__).resolve().parents[2]
spec = importlib.util.spec_from_file_location('scoped_core', root / 'src/extensions/python/line_scoped_core.py')
core = importlib.util.module_from_spec(spec)
spec.loader.exec_module(core)

class NamedIdentityTest(unittest.TestCase):
    def setUp(self):
        self.db = sqlite3.connect(':memory:')
        self.addCleanup(self.db.close)
        self.db.executescript('''CREATE TABLE _groupChat (_chatMid TEXT, _chatName TEXT);
            CREATE TABLE _chat (_id TEXT, _midType INTEGER);
            CREATE TABLE _contact (_mid TEXT, _displayName TEXT, _displayNameOverridden TEXT);
            INSERT INTO _chat VALUES ('nameless', 0);
            INSERT INTO _groupChat VALUES ('missing-group', NULL);''')
        self.db.execute('INSERT INTO _groupChat VALUES (?,?)', ('target', '測試♋️'))

    def test_named_target_survives_unrelated_null_names(self):
        found, identity = core.resolve_gui_chat(self.db, {'chatName':'測試♋️'})
        self.assertEqual(found, 'target')
        self.assertEqual(identity['kind'], 'group')

    def test_known_equivalent_names_still_refuse(self):
        self.db.execute('INSERT INTO _groupChat VALUES (?,?)', ('other', '測 試♋️ (2)'))
        with self.assertRaises(core.ReaderError) as caught:
            core.resolve_gui_chat(self.db, {'chatName':'測試♋️'})
        self.assertEqual(caught.exception.code, 'CHAT_AMBIGUOUS')

    def test_send_identity_counts_matching_contacts_without_chat_rows(self):
        scope = {'chatName': '測試♋️', 'chatType': 'group',
                 'dateFrom': '2026-09-23', 'dateTo': '2026-09-23',
                 'identityOnly': True, 'requireUniqueName': True}
        self.db.execute('INSERT INTO _contact VALUES (?,?,?)',
                        ('unopened', '測試♋️', None))
        with self.assertRaises(core.ReaderError) as caught:
            core.read_scoped(self.db, scope, {})
        self.assertEqual(caught.exception.code, 'CHAT_AMBIGUOUS')
        gui_scope = {key: value for key, value in scope.items()
                     if key != 'requireUniqueName'}
        gui_scope['guiIdentityOnly'] = True
        with self.assertRaises(core.ReaderError) as caught:
            core.read_scoped(self.db, gui_scope, {})
        self.assertEqual(caught.exception.code, 'CHAT_AMBIGUOUS')
        # Ordinary scoped reads keep their existing group-only resolution.
        ordinary = core.read_scoped(self.db,
                                    {key: value for key, value in scope.items()
                                     if key != 'requireUniqueName'}, {})
        self.assertEqual(ordinary['chatIdentity']['kind'], 'group')

        self.db.execute('UPDATE _contact SET _displayName=? WHERE _mid=?',
                        ('測 試♋️ (2)', 'unopened'))
        with self.assertRaises(core.ReaderError) as caught:
            core.read_scoped(self.db, scope, {})
        self.assertEqual(caught.exception.code, 'CHAT_AMBIGUOUS')

        self.db.execute('UPDATE _contact SET _displayName=? WHERE _mid=?',
                        ('Unrelated', 'unopened'))
        unique = core.read_scoped(self.db, scope, {})
        self.assertEqual(unique['chatIdentity']['kind'], 'group')

if __name__ == '__main__':
    unittest.main()
