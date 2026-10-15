import os
from pathlib import Path
import sys
import tempfile
import unittest
from unittest import mock
import uuid

PYTHON = Path(__file__).parents[2] / 'src' / 'extensions' / 'python'
sys.path.insert(0, str(PYTHON))
import line_runtime_paths as paths


class ReaderRequestDirectoryTests(unittest.TestCase):
    def setUp(self):
        self.work = Path(tempfile.gettempdir()) / ('reader-request-test-' + uuid.uuid4().hex)
        self.work.mkdir()
        self.environment = mock.patch.dict(os.environ)
        self.environment.start()
        os.environ.pop('LINE_MCP_READER_REQUEST_ID', None)

    def tearDown(self):
        self.environment.stop()
        for child in self.work.iterdir():
            if child.is_symlink():
                child.unlink()
            elif child.is_dir():
                for item in child.iterdir():
                    item.unlink()
                child.rmdir()
            else:
                child.unlink()
        self.work.rmdir()

    def test_standalone_reader_creates_one_exclusive_empty_directory(self):
        first = paths.create_reader_request_directory(self.work)
        second = paths.create_reader_request_directory(self.work)
        self.assertNotEqual(first, second)
        self.assertRegex(first.name, r'^line-reader-[0-9a-f]{32}$')
        self.assertEqual(first.parent, self.work)
        self.assertEqual(list(first.iterdir()), [])

    def test_parent_owned_request_uses_only_the_exact_existing_empty_directory(self):
        request_id = 'a' * 32
        directory = self.work / ('line-reader-' + request_id)
        directory.mkdir()
        os.environ['LINE_MCP_READER_REQUEST_ID'] = request_id
        self.assertEqual(paths.create_reader_request_directory(self.work), directory)
        self.assertEqual(list(self.work.iterdir()), [directory])

    def test_invalid_request_id_and_missing_directory_are_refused(self):
        for value in ('', '../other', 'A' * 32, 'a' * 31, 'a' * 33, 'a' * 32 + ' '):
            with self.subTest(value=value):
                os.environ['LINE_MCP_READER_REQUEST_ID'] = value
                with self.assertRaises(paths.RuntimePathError):
                    paths.create_reader_request_directory(self.work)
        os.environ['LINE_MCP_READER_REQUEST_ID'] = 'b' * 32
        with self.assertRaises(paths.RuntimePathError):
            paths.create_reader_request_directory(self.work)
        self.assertEqual(list(self.work.iterdir()), [])

    def test_preexisting_contents_are_preserved_and_refused(self):
        request_id = 'c' * 32
        directory = self.work / ('line-reader-' + request_id)
        directory.mkdir()
        sentinel = directory / 'snapshot.edb'
        sentinel.write_bytes(b'not-owned-by-this-request')
        os.environ['LINE_MCP_READER_REQUEST_ID'] = request_id
        with self.assertRaises(paths.RuntimePathError):
            paths.create_reader_request_directory(self.work)
        self.assertEqual(sentinel.read_bytes(), b'not-owned-by-this-request')

    def test_parent_request_reparse_is_refused(self):
        directory = self.work / ('line-reader-' + 'd' * 32)
        directory.mkdir()
        os.environ['LINE_MCP_READER_REQUEST_ID'] = 'd' * 32
        original = paths._directory_info

        def reject_reparse(path):
            if path == directory:
                raise paths.RuntimePathError()
            return original(path)

        with mock.patch.object(paths, '_directory_info', side_effect=reject_reparse):
            with self.assertRaises(paths.RuntimePathError):
                paths.create_reader_request_directory(self.work)
        self.assertTrue(directory.is_dir())

    def test_standalone_post_creation_failure_removes_its_empty_directory(self):
        original = Path.iterdir
        failed = False

        def fail_once(directory):
            nonlocal failed
            if directory.name.startswith('line-reader-') and not failed:
                failed = True
                raise OSError('synthetic post-creation failure')
            return original(directory)

        with mock.patch.object(Path, 'iterdir', autospec=True, side_effect=fail_once):
            with self.assertRaises(paths.RuntimePathError):
                paths.create_reader_request_directory(self.work)
        self.assertTrue(failed)
        self.assertEqual(list(self.work.iterdir()), [])


if __name__ == '__main__':
    unittest.main()
