"""Opt-in synthetic large encrypted reader regression; never opens real LINE data.

Run separately for each size to measure a fresh process's peak working set:
  python -B scripts/benchmark-large-reader.py --size-mib 800
  python -B scripts/benchmark-large-reader.py --size-mib 1024
Requires the same explicitly configured, hash-verified SQLite3MC DLL as tests.
"""
import argparse
import ctypes as ct
from ctypes import wintypes as wt
import datetime as dt
import hashlib
import importlib.util
import json
import os
from pathlib import Path
import sys
import tempfile
import time
import tracemalloc
from unittest import mock
import uuid

PYTHON = Path(__file__).parents[1] / 'src' / 'extensions' / 'python'
sys.path.insert(0, str(PYTHON))
from line_sqlite_engine import Connection, verified_dll_path
from line_encrypted_snapshot import cleanup_snapshot

SPEC = importlib.util.spec_from_file_location('synthetic_large_reader', PYTHON / 'line-reader.py')
reader = importlib.util.module_from_spec(SPEC)
SPEC.loader.exec_module(reader)

MIB = 1024 * 1024
PASSPHRASE = b'synthetic-large-reader-fixture-key'


def hash_file(path):
    with path.open('rb') as stream:
        return hashlib.file_digest(stream, 'sha256').hexdigest()


def peak_working_set():
    if os.name != 'nt':
        return None

    class Counters(ct.Structure):
        _fields_ = [('cb', wt.DWORD), ('PageFaultCount', wt.DWORD)] + [
            (name, ct.c_size_t) for name in (
                'PeakWorkingSetSize', 'WorkingSetSize', 'QuotaPeakPagedPoolUsage',
                'QuotaPagedPoolUsage', 'QuotaPeakNonPagedPoolUsage',
                'QuotaNonPagedPoolUsage', 'PagefileUsage', 'PeakPagefileUsage')]

    kernel = ct.WinDLL('kernel32', use_last_error=True)
    kernel.GetCurrentProcess.restype = wt.HANDLE
    psapi = ct.WinDLL('psapi', use_last_error=True)
    psapi.GetProcessMemoryInfo.argtypes = [wt.HANDLE, ct.POINTER(Counters), wt.DWORD]
    psapi.GetProcessMemoryInfo.restype = wt.BOOL
    counters = Counters()
    counters.cb = ct.sizeof(counters)
    if not psapi.GetProcessMemoryInfo(kernel.GetCurrentProcess(), ct.byref(counters), counters.cb):
        raise RuntimeError('synthetic memory measurement unavailable')
    return counters.PeakWorkingSetSize


def fill_database(db, target_bytes):
    db.execute('PRAGMA journal_mode=OFF')  # Only this synthetic fixture.
    db.execute('PRAGMA synchronous=OFF')
    for sql in (
        'CREATE TABLE _groupChat(_chatMid TEXT, _chatName TEXT)',
        'CREATE TABLE _chat(_id TEXT, _midType INTEGER)',
        'CREATE TABLE _contact(_mid TEXT, _displayNameOverridden TEXT, _displayName TEXT)',
        'CREATE TABLE _profile(_mid TEXT, _displayName TEXT)',
        'CREATE TABLE _message(_id TEXT, _from TEXT, _createdTime INTEGER, _text TEXT, '
        '_contentType INTEGER, _contentMetadata BLOB, _contentInfo BLOB, '
        '_relatedMessageId TEXT, _type INTEGER, _status INTEGER, _rev INTEGER, _chatId TEXT)',
        'CREATE TABLE _synthetic_padding(payload BLOB)',
    ):
        db.execute(sql)
    db.execute("INSERT INTO _groupChat VALUES ('fixture-chat','Synthetic Large Group')")
    db.execute("INSERT INTO _chat VALUES ('fixture-chat',2)")
    db.execute("INSERT INTO _contact VALUES ('fixture-user','','Synthetic User')")
    timestamp = int(dt.datetime(2026, 9, 12, 12, tzinfo=dt.timezone(dt.timedelta(hours=8))).timestamp() * 1000)
    db.execute('INSERT INTO _message VALUES (?,?,?,?,0,NULL,NULL,NULL,0,0,1,?)',
               ('fixture-message', 'fixture-user', timestamp, 'before WAL commit', 'fixture-chat'))
    page_size = db.execute('PRAGMA page_size').fetchall()[0][0]
    while db.execute('PRAGMA page_count').fetchall()[0][0] * page_size < target_bytes:
        db.execute('BEGIN')
        for _ in range(16):
            db.execute('INSERT INTO _synthetic_padding VALUES (zeroblob(1044480))')
        db.execute('COMMIT')
    if db.execute('PRAGMA journal_mode=WAL').fetchall() != [('wal',)]:
        raise RuntimeError('synthetic WAL setup failed')
    db.execute('PRAGMA wal_autocheckpoint=0')
    db.execute("UPDATE _message SET _text='latest committed fixture', _rev=2")


def run(size_mib):
    verified_dll_path()  # Fail instead of silently skipping the native engine.
    root = Path(tempfile.gettempdir()) / ('line-large-fixture-' + uuid.uuid4().hex)
    root.mkdir(mode=0o700)
    database_dir = root / 'LINE' / 'Data' / 'db'
    database_dir.mkdir(parents=True)
    source = database_dir / 'fixture.edb'
    wal = Path(str(source) + '-wal')
    report = None
    try:
        build_started = time.perf_counter()
        with Connection(source, PASSPHRASE, readonly=False) as builder:
            fill_database(builder, size_mib * MIB)
            actual_size = source.stat().st_size
            assert actual_size >= size_mib * MIB
            with source.open('rb') as stream:
                assert stream.read(16) != b'SQLite format 3\0'
            before = (hash_file(source), hash_file(wal))
            print(json.dumps({'phase': 'fixture_ready', 'targetMiB': size_mib,
                              'sourceBytes': actual_size, 'walBytes': wal.stat().st_size}), flush=True)
            build_seconds = time.perf_counter() - build_started
            scope = {'chatName': 'Synthetic Large Group', 'chatType': 'group',
                     'dateFrom': '2026-09-12', 'dateTo': '2026-09-12', 'messageLimit': 50}
            with mock.patch.dict(os.environ, {'LOCALAPPDATA': str(root)}), \
                 mock.patch.object(reader, 'verify_client_build', return_value={'verified': True, 'synthetic': True}), \
                 mock.patch.object(reader, 'acquire_passphrase', return_value=(PASSPHRASE, {'keyAcquisition': 'synthetic-fixture'})):
                os.environ.pop('LINE_MCP_READER_REQUEST_ID', None)
                for setting in ('LINE_MCP_MAX_SOURCE_BYTES', 'LINE_MCP_MAX_WAL_BYTES', 'LINE_MCP_MAX_SNAPSHOT_BYTES'):
                    os.environ.pop(setting, None)
                tracemalloc.start()
                started = time.perf_counter()
                result = reader.run(scope)
                seconds = time.perf_counter() - started
                _, python_peak = tracemalloc.get_traced_memory()
                tracemalloc.stop()
            process_peak = peak_working_set()
            assert result['count'] == 1
            assert result['messages'][0]['text'] == 'latest committed fixture'
            assert result['messages'][0]['sourceRevision'] == 2
            assert result['scope']['snapshot']['wal']['committedFrames'] >= 1
            assert result['scope']['snapshot']['storage'] == 'streamed_encrypted_files'
            assert result['freshness']['capturedAfterInitialization'] is True
            assert python_peak < 32 * MIB, f'Python allocations scaled with file size: {python_peak}'
            if process_peak is not None:
                assert process_peak < 256 * MIB, f'Process memory scaled with file size: {process_peak}'
            assert before == (hash_file(source), hash_file(wal))
            requests = root / 'line-desktop-mcp' / 'line-reader'
            assert list(requests.iterdir()) == []
            report = {'targetMiB': size_mib, 'sourceBytes': actual_size,
                      'sourceWalBytes': wal.stat().st_size, 'fixtureBuildSeconds': round(build_seconds, 3),
                      'readerSeconds': round(seconds, 3), 'pythonPeakBytes': python_peak,
                      'processPeakWorkingSetBytes': process_peak, 'latestCommittedMessageVerified': True,
                      'sourceHashesUnchanged': True, 'requestDirectoryCleaned': True,
                      'readerTiming': result['readerTiming']}
    finally:
        # Only this UUID fixture's exact known paths. No recursive deletion.
        requests = root / 'line-desktop-mcp' / 'line-reader'
        if requests.exists():
            for directory in requests.iterdir():
                if directory.is_dir() and not directory.is_symlink() and directory.name.startswith('line-reader-'):
                    cleanup_snapshot(directory)
                    directory.rmdir()
            requests.rmdir()
            requests.parent.rmdir()
        for suffix in ('-wal', '-shm', '-journal', ''):
            Path(str(source) + suffix).unlink(missing_ok=True)
        for directory in (database_dir, database_dir.parent, database_dir.parent.parent, root):
            directory.rmdir()
    report['fixtureRemoved'] = not root.exists()
    print(json.dumps(report), flush=True)


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--size-mib', type=int, choices=(800, 1024), required=True)
    run(parser.parse_args().size_mib)
