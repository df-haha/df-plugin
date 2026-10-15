"""Bounded read-only wxSQLite3 interoperability and session scanning helpers.

No network, injection, process writes, database writes, key files, plaintext
database dumps, or message queries. Candidate secrets exist only in this process.
Crypto interoperability follows the reviewed MIT SQLite3MultipleCiphers code:
https://github.com/utelle/SQLite3MultipleCiphers/tree/7dd52e99ea918c1babf633e30749298c688ce6d7/src
Copyright notices, MIT terms and historical references are
retained in docs/THIRD_PARTY.md.
Local read-only memory scanning design reference/prior art:
https://github.com/yung13yubabie/line-summary/blob/bd3569e80af56cff342cab9690e6a6d0cb2f4f93/key_extractor.py
"""
import collections
import ctypes as ct
from ctypes import wintypes as wt
import hashlib
import os
import re
import sqlite3
import struct
import time

from cryptography.hazmat.primitives.ciphers import Cipher, algorithms, modes
from cryptography.hazmat.decrepit.ciphers.algorithms import ARC4

PADDING = bytes.fromhex('28bf4e5e4e758a4164004e56fffa01082e2e00b6d0683e802f0ca9fe6453697a')
SQLITE_HEADER = b'SQLite format 3\0'
SAFE_NAME = re.compile(r'^[A-Za-z_][A-Za-z0-9_]{0,100}$')
MAX_DB_BYTES = 256 * 1024 * 1024
MAX_LOCATOR_WINDOW_BYTES = 4 * 1024 * 1024 + 128
SCAN_CHUNK_BYTES = 4 * 1024 * 1024
# A literal first character class lets Python's regex engine skip non-hex
# bytes in C. Leading lookbehind disables that fast path on large buffers.
# Boundaries remain byte-exact and are checked only around actual matches.
CANDIDATE_ASCII_PATTERN = re.compile(rb'[0-9a-fA-F][0-9a-fA-F]{31}')
CANDIDATE_UTF16_PATTERN = re.compile(rb'[0-9a-fA-F]\x00(?:[0-9a-fA-F]\x00){31}')
ASCII_ALNUM = bytes(int(48 <= value <= 57 or 65 <= value <= 90 or 97 <= value <= 122)
                    for value in range(256))


class ProbeError(Exception):
    """Only fixed messages may be raised; exceptions must not expose secrets."""


def iter_candidate_values(data, *, complete=True, start_is_boundary=True):
    """Yield exact ASCII/UTF16 candidates, preserving ASCII-first ordering.

    Memory scan chunks use complete=False: a candidate whose right boundary
    is outside the observed bytes waits for the next overlap window. Standalone
    complete buffers retain the earlier extractor's end-of-buffer semantics.
    """
    for match in CANDIDATE_ASCII_PATTERN.finditer(data):
        start, end = match.span()
        if not start_is_boundary and start == 0:
            continue
        if start and ASCII_ALNUM[data[start - 1]]:
            continue
        if end < len(data):
            if ASCII_ALNUM[data[end]]:
                continue
        elif not complete:
            continue
        yield match.group()
    for match in CANDIDATE_UTF16_PATTERN.finditer(data):
        start, end = match.span()
        if not start_is_boundary and start < 2:
            continue
        if start >= 2 and data[start - 1] == 0 and ASCII_ALNUM[data[start - 2]]:
            continue
        if end + 1 < len(data):
            if data[end + 1] == 0 and ASCII_ALNUM[data[end]]:
                continue
        elif not complete:
            continue
        yield match.group()[::2]


def md5(data):
    return hashlib.md5(data).digest()


def rc4(key, data):
    cipher = Cipher(ARC4(key), mode=None).encryptor()
    return cipher.update(data) + cipher.finalize()


def derive_key(passphrase):
    padded = (passphrase[:32] + PADDING)[:32]
    owner_seed = PADDING
    for _ in range(51):
        owner_seed = md5(owner_seed)
    owner = padded
    for i in range(20):
        owner = rc4(bytes(b ^ i for b in owner_seed), owner)
    key = padded + owner
    for _ in range(51):
        key = md5(key)
    return key


def page_iv(page_no):
    seed = page_no + 1
    words = []
    for _ in range(4):
        seed = (40692 * seed) % 2147483399
        words.append(seed)
    return md5(struct.pack('<4I', *words))


def aes_page(data, base_key, page_no, encrypt=False):
    key = md5(base_key + struct.pack('<I', page_no) + b'sAlT')
    cipher = Cipher(algorithms.AES(key), modes.CBC(page_iv(page_no)))
    op = cipher.encryptor() if encrypt else cipher.decryptor()
    return op.update(data) + op.finalize()


def decrypt_page(data, base_key, page_no):
    if len(data) < 512 or len(data) % 16:
        raise ProbeError('invalid page size')
    if page_no == 1:
        clear_header = data[16:24]
        if clear_header[5:] != bytes((64, 32, 32)):
            raise ProbeError('unsupported database header layout')
        # wxSQLite3 preserves bytes16..23 and relocates their ciphertext to8..15.
        payload = data[8:16] + data[24:]
        plaintext = aes_page(payload, base_key, page_no)
        if plaintext[:8] != clear_header:
            raise ProbeError('candidate did not validate')
        return SQLITE_HEADER + plaintext
    return aes_page(data, base_key, page_no)


def first_page_valid(data, base_key):
    try:
        page = decrypt_page(data, base_key, 1)
        return (page[:16] == SQLITE_HEADER and page[100] in (5, 13)
                and int.from_bytes(page[56:60], 'big') in (1, 2, 3))
    except (ProbeError, ValueError):
        return False


def varint(data, offset):
    value = 0
    for i in range(9):
        if offset >= len(data):
            raise ProbeError('truncated schema varint')
        byte = data[offset]
        offset += 1
        value = (value << (8 if i == 8 else 7)) | (byte if i == 8 else byte & 127)
        if byte < 128 or i == 8:
            return value, offset
    raise ProbeError('invalid schema varint')


def schema_pages(path, key, limit=256):
    """Read only page1 and the sqlite_schema btree/overflow pages it reaches.

    No WAL is replayed: this is the on-disk main-file schema at the read time,
    NOT a consistent/current chat-history snapshot. Source stat drift is fatal.
    """
    before = path.stat()
    if not 4096 <= before.st_size <= MAX_DB_BYTES:
        raise ProbeError('database exceeds bounded probe size')
    pages = {}
    with path.open('rb') as stream:
        raw = stream.read(4096)
        size = int.from_bytes(raw[16:18], 'big')
        if size != 4096 or before.st_size % size:
            raise ProbeError('only aligned 4096-byte LINE pages are supported')
        if raw[20] != 0:
            raise ProbeError('reserved page bytes are not supported by this probe')
        def read(number):
            if number in pages:
                raise ProbeError('schema page repeated or cyclic')
            if not 1 <= number <= before.st_size // size or len(pages) >= limit:
                raise ProbeError('schema page bounds exceeded')
            stream.seek((number - 1) * size)
            page = decrypt_page(stream.read(size), key, number)
            pages[number] = page
            return page
        stack = [1]
        while stack:
            number = stack.pop()
            page = read(number)
            start = 100 if number == 1 else 0
            if page[start] not in (5, 13):
                raise ProbeError('unexpected sqlite_schema btree page')
            count = int.from_bytes(page[start + 3:start + 5], 'big')
            interior = page[start] == 5
            header_size = 12 if interior else 8
            if start + header_size + count * 2 > size:
                raise ProbeError('invalid schema cell array')
            if interior:
                stack.append(int.from_bytes(page[start + 8:start + 12], 'big'))
            for i in range(count):
                pointer_at = start + header_size + i * 2
                cell = int.from_bytes(page[pointer_at:pointer_at + 2], 'big')
                if not start + header_size + count * 2 <= cell < size:
                    raise ProbeError('invalid schema cell offset')
                if interior:
                    if cell + 4 > size:
                        raise ProbeError('truncated schema interior pointer')
                    stack.append(int.from_bytes(page[cell:cell + 4], 'big'))
                    continue
                payload, offset = varint(page, cell)
                _, offset = varint(page, offset)
                usable = size - raw[20]
                if not 0 < payload <= limit * usable:
                    raise ProbeError('schema payload bounds exceeded')
                if payload > usable - 35:
                    minimum = ((usable - 12) * 32 // 255) - 23
                    local = minimum + (payload - minimum) % (usable - 4)
                    if local > usable - 35:
                        local = minimum
                    if offset + local + 4 > usable:
                        raise ProbeError('truncated schema overflow pointer')
                    next_page = int.from_bytes(page[offset + local:offset + local + 4], 'big')
                    remaining = payload - local
                    while remaining > 0:
                        overflow = read(next_page)
                        next_page = int.from_bytes(overflow[:4], 'big')
                        remaining -= min(remaining, usable - 4)
                    if next_page:
                        raise ProbeError('schema overflow has trailing link')
                elif offset + payload > usable:
                    raise ProbeError('truncated schema leaf payload')
    after = path.stat()
    if (before.st_size, before.st_mtime_ns) != (after.st_size, after.st_mtime_ns):
        raise ProbeError('source changed during schema probe')
    # Missing non-schema pages stay zero. SQLite is only allowed to query schema.
    buffer = bytearray(before.st_size)
    for number, page in pages.items():
        buffer[(number - 1) * size:number * size] = page
    # sqlite3.deserialize does not open WAL files; make only the in-memory view
    # use rollback-journal version fields. Source bytes are never modified.
    buffer[18:20] = b'\x01\x01'
    conn = sqlite3.connect(':memory:')
    conn.deserialize(bytes(buffer))
    conn.execute('PRAGMA query_only=ON')
    conn.execute('PRAGMA trusted_schema=OFF')
    def authorize(action, arg1, arg2, database, trigger):
        if action == sqlite3.SQLITE_SELECT:
            return sqlite3.SQLITE_OK
        if action == sqlite3.SQLITE_READ and arg1 in ('sqlite_master', 'sqlite_schema'):
            return sqlite3.SQLITE_OK
        return sqlite3.SQLITE_DENY
    conn.set_authorizer(authorize)
    try:
        rows = conn.execute("SELECT type,name,tbl_name,sql FROM sqlite_master ORDER BY type,name").fetchall()
    finally:
        conn.close()
        buffer[:] = bytes(len(buffer))
    tables = []
    for kind, name, table, sql in rows:
        if kind == 'table' and SAFE_NAME.fullmatch(name):
            # SQL is schema metadata; include only identifier tokens, not SQL or
            # quoted literals/defaults. No schema statement is executed.
            columns = re.findall(r'(?:\(|,)\s*([A-Za-z_][A-Za-z0-9_]*)\s+(?:TEXT|INTEGER|INT|REAL|BLOB|VARCHAR|BOOLEAN|BOOL|BIGINT|DOUBLE|NUMERIC)\b', sql or '', re.I)
            tables.append({'name': name, 'columns': columns})
    return {'status': 'decrypted_schema', 'schema_object_count': len(rows), 'table_count': len(tables), 'schema_pages_decrypted': len(pages), 'schema_bytes_decrypted': len(pages) * size, 'tables': tables, 'wal_replayed': False, 'source_size_and_mtime_stable': True}


class MemoryInfo(ct.Structure):
    _fields_ = [('base', ct.c_void_p), ('allocation_base', ct.c_void_p),
                ('allocation_protect', wt.DWORD), ('region_size', ct.c_size_t),
                ('state', wt.DWORD), ('protect', wt.DWORD), ('kind', wt.DWORD)]


def _locator_filetime(value):
    if isinstance(value, str) and re.fullmatch(r'[0-9a-fA-F]{16}', value):
        return int(value, 16)
    if type(value) is int and 0 <= value <= 0xFFFFFFFFFFFFFFFF:
        return value
    raise ProbeError('invalid locator process identity')


def read_locator_window(pid, expected_exe, expected_created_filetime, window_address,
                        max_bytes=MAX_LOCATOR_WINDOW_BYTES):
    """Read one verified, precise private-memory window for a warm lookup.

    The caller receives only a bounded raw window to run the same shaped-key
    regex and encrypted first-page validator.  No candidate address or value is
    computed here, and no raw bytes are logged.
    """
    if (type(pid) is not int or not 1 <= pid <= 0xFFFFFFFF
            or type(window_address) is not int or window_address < 0
            or type(max_bytes) is not int or not 1 <= max_bytes <= MAX_LOCATOR_WINDOW_BYTES):
        raise ProbeError('invalid locator window')
    expected_filetime = _locator_filetime(expected_created_filetime)
    pointer_bits = ct.sizeof(ct.c_void_p) * 8
    pointer_max = (1 << pointer_bits) - 1
    if window_address > pointer_max:
        raise ProbeError('invalid locator window')
    kernel = ct.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wt.BOOL
    kernel.GetProcessTimes.argtypes = [wt.HANDLE, ct.POINTER(wt.FILETIME), ct.POINTER(wt.FILETIME),
                                       ct.POINTER(wt.FILETIME), ct.POINTER(wt.FILETIME)]
    kernel.GetProcessTimes.restype = wt.BOOL
    kernel.VirtualQueryEx.argtypes = [wt.HANDLE, ct.c_void_p, ct.POINTER(MemoryInfo), ct.c_size_t]
    kernel.VirtualQueryEx.restype = ct.c_size_t
    kernel.ReadProcessMemory.argtypes = [wt.HANDLE, ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.POINTER(ct.c_size_t)]
    kernel.ReadProcessMemory.restype = wt.BOOL
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        raise ProbeError('LINE read-only process access denied')
    buffer = None
    try:
        path_buffer = ct.create_unicode_buffer(32768)
        capacity = wt.DWORD(len(path_buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, path_buffer, ct.byref(capacity)):
            raise ProbeError('could not verify LINE executable')
        if os.path.normcase(os.path.realpath(path_buffer.value)) != os.path.normcase(os.path.realpath(expected_exe)):
            raise ProbeError('PID is not the expected installed LINE executable')
        created = wt.FILETIME()
        exited = wt.FILETIME()
        kernel_time = wt.FILETIME()
        user_time = wt.FILETIME()
        if not kernel.GetProcessTimes(handle, ct.byref(created), ct.byref(exited),
                                      ct.byref(kernel_time), ct.byref(user_time)):
            raise ProbeError('could not verify LINE process creation time')
        actual_filetime = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
        if actual_filetime != expected_filetime:
            raise ProbeError('LINE process changed')
        info = MemoryInfo()
        if not kernel.VirtualQueryEx(handle, ct.c_void_p(window_address), ct.byref(info), ct.sizeof(info)):
            raise ProbeError('locator window unavailable')
        base = int(info.base or 0)
        size = int(info.region_size)
        end = base + size
        readable = (info.state == 0x1000 and info.kind == 0x20000
                    and (info.protect & 0xFF) in (2, 4, 8, 32, 64, 128)
                    and not info.protect & 0x100)
        if (not readable or base < 0 or size <= 0 or end <= base
                or end - 1 > pointer_max or not base <= window_address < end):
            raise ProbeError('locator window unavailable')
        length = min(max_bytes, end - window_address)
        if length <= 0 or window_address + length - 1 > pointer_max:
            raise ProbeError('locator window unavailable')
        buffer = ct.create_string_buffer(length)
        received = ct.c_size_t()
        if (not kernel.ReadProcessMemory(handle, ct.c_void_p(window_address), buffer, length, ct.byref(received))
                or received.value != length):
            raise ProbeError('locator window unavailable')
        return bytes(buffer.raw[:length])
    finally:
        if buffer is not None:
            ct.memset(buffer, 0, len(buffer))
        kernel.CloseHandle(handle)


def find_candidates(pid, expected_exe, max_bytes=1024 * 1024 * 1024, max_seconds=90,
                    candidate_validator=None, max_candidates=10000,
                    validated_window_recorder=None,
                    process_created_filetime_recorder=None):
    """Read only private readable memory of the exact verified LINE executable.

    Bounded buffers are discarded; only shaped candidates are held for validation.
    No raw memory, surrounding text, address, identifier, or candidate is output.
    """
    kernel = ct.WinDLL('kernel32', use_last_error=True)
    kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
    kernel.OpenProcess.restype = wt.HANDLE
    kernel.CloseHandle.argtypes = [wt.HANDLE]
    kernel.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD)]
    kernel.QueryFullProcessImageNameW.restype = wt.BOOL
    if process_created_filetime_recorder is not None:
        kernel.GetProcessTimes.argtypes = [wt.HANDLE, ct.POINTER(wt.FILETIME), ct.POINTER(wt.FILETIME),
                                           ct.POINTER(wt.FILETIME), ct.POINTER(wt.FILETIME)]
        kernel.GetProcessTimes.restype = wt.BOOL
    kernel.VirtualQueryEx.argtypes = [wt.HANDLE, ct.c_void_p, ct.POINTER(MemoryInfo), ct.c_size_t]
    kernel.VirtualQueryEx.restype = ct.c_size_t
    kernel.ReadProcessMemory.argtypes = [wt.HANDLE, ct.c_void_p, ct.c_void_p, ct.c_size_t, ct.POINTER(ct.c_size_t)]
    kernel.ReadProcessMemory.restype = wt.BOOL
    handle = kernel.OpenProcess(0x0400 | 0x0010, False, pid)
    if not handle:
        raise ProbeError('LINE read-only process access denied')
    counts = collections.Counter()
    validated = set()
    metrics = {'bytes_read': 0, 'regions_read': 0, 'failed_chunks': 0, 'limit_reached': False}
    start = time.monotonic()
    try:
        path_buffer = ct.create_unicode_buffer(32768)
        capacity = wt.DWORD(len(path_buffer))
        if not kernel.QueryFullProcessImageNameW(handle, 0, path_buffer, ct.byref(capacity)):
            raise ProbeError('could not verify LINE executable')
        if os.path.normcase(os.path.realpath(path_buffer.value)) != os.path.normcase(os.path.realpath(expected_exe)):
            raise ProbeError('PID is not the expected installed LINE executable')
        if process_created_filetime_recorder is not None:
            created = wt.FILETIME()
            exited = wt.FILETIME()
            kernel_time = wt.FILETIME()
            user_time = wt.FILETIME()
            if kernel.GetProcessTimes(handle, ct.byref(created), ct.byref(exited),
                                      ct.byref(kernel_time), ct.byref(user_time)):
                try:
                    process_created_filetime_recorder(
                        (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime))
                except Exception:
                    pass
        address = 0
        while True:
            info = MemoryInfo()
            if not kernel.VirtualQueryEx(handle, ct.c_void_p(address), ct.byref(info), ct.sizeof(info)):
                break
            next_address = (info.base or 0) + info.region_size
            if next_address <= address:
                raise ProbeError('invalid process memory region sequence')
            readable = info.state == 0x1000 and info.kind == 0x20000 and (info.protect & 0xFF) in (2, 4, 8, 32, 64, 128) and not info.protect & 0x100
            if readable and info.region_size <= 128 * 1024 * 1024:
                tail = b''
                offset = 0
                metrics['regions_read'] += 1
                while offset < info.region_size:
                    if metrics['bytes_read'] >= max_bytes or time.monotonic() - start > max_seconds:
                        metrics['limit_reached'] = True
                        break
                    length = min(SCAN_CHUNK_BYTES, info.region_size - offset, max_bytes - metrics['bytes_read'])
                    buf = ct.create_string_buffer(length)
                    received = ct.c_size_t()
                    previous_tail_length = 0
                    ok = kernel.ReadProcessMemory(handle, ct.c_void_p((info.base or 0) + offset), buf, length, ct.byref(received))
                    if 0 < received.value <= length:
                        previous_tail_length = len(tail)
                        data = tail + buf.raw[:received.value]
                        for candidate in iter_candidate_values(data,
                                complete=(received.value == length and offset + length == info.region_size),
                                start_is_boundary=(offset == 0)):
                            counts[candidate] += 1
                        # A short read leaves unobserved bytes before the next
                        # requested chunk. Never join a key across that gap.
                        tail = data[-128:] if received.value == length else b''
                        if received.value < length:
                            metrics['failed_chunks'] += 1
                        metrics['bytes_read'] += received.value
                        del data
                    else:
                        metrics['failed_chunks'] += 1
                        tail = b''
                    ct.memset(buf, 0, length)
                    if candidate_validator is not None:
                        for candidate, _ in counts.most_common():
                            if candidate in validated:
                                continue
                            if len(validated) >= max_candidates or time.monotonic() - start > max_seconds:
                                metrics['limit_reached'] = True
                                break
                            validated.add(candidate)
                            if candidate_validator(candidate):
                                if validated_window_recorder is not None:
                                    # The scanner records only the <=4 MiB chunk
                                    # start plus its preceding bounded overlap.
                                    validated_window_recorder((info.base or 0) + offset - previous_tail_length)
                                metrics.update({'candidate_count': len(counts),
                                                'validated_candidates': len(validated),
                                                'stopped_after_validation': True,
                                                'elapsed_seconds': round(time.monotonic() - start, 2)})
                                return [candidate], metrics
                    if metrics['limit_reached']:
                        break
                    offset += length
                if metrics['limit_reached']:
                    break
            address = next_address
        metrics['candidate_count'] = len(counts)
        metrics['elapsed_seconds'] = round(time.monotonic() - start, 2)
        metrics['validated_candidates'] = len(validated)
        metrics['stopped_after_validation'] = False
        return ([] if candidate_validator is not None else [key for key, frequency in counts.most_common()]), metrics
    finally:
        counts.clear()
        validated.clear()
        kernel.CloseHandle(handle)
