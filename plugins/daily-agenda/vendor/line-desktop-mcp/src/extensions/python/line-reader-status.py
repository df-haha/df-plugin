"""Read-only public status for the bounded local LINE reader prerequisites."""
from __future__ import annotations

import argparse
import ctypes as ct
from ctypes import wintypes as wt
import hashlib
import json
import os
from pathlib import Path
import sys

from line_client_compatibility import ClientBuildError, verify_client_build


PROCESS_QUERY_LIMITED_INFORMATION = 0x1000
TH32CS_SNAPPROCESS = 0x00000002
ERROR_NO_MORE_FILES = 18


class _ProcessEntry(ct.Structure):
    _fields_ = [
        ('size', wt.DWORD), ('usage', wt.DWORD), ('pid', wt.DWORD),
        ('heap', ct.c_size_t), ('module', wt.DWORD), ('threads', wt.DWORD),
        ('parent', wt.DWORD), ('priority', wt.LONG), ('flags', wt.DWORD),
        ('exe', wt.WCHAR * 260),
    ]


def _default_executable():
    root = os.environ.get('LOCALAPPDATA')
    return Path(root) / 'LINE' / 'bin' / 'current' / 'LINE.exe' if root else None


def _same_executable(left, right):
    try:
        return os.path.normcase(os.path.realpath(os.fspath(left))) == os.path.normcase(os.path.realpath(os.fspath(right)))
    except (OSError, TypeError, ValueError):
        return False


def _process_instance_ref(pid, created_filetime, build_ref):
    payload = f'line-client-process-v1\0{pid}\0{created_filetime:016x}\0{build_ref}'.encode('ascii')
    return 'process:' + hashlib.sha256(payload).hexdigest()


def _query_process(kernel, pid, expected_executable, build_ref):
    """Use one query-limited handle for both image and creation-time checks."""
    handle = kernel.OpenProcess(PROCESS_QUERY_LIMITED_INFORMATION, False, pid)
    if not handle:
        return None
    try:
        image = ct.create_unicode_buffer(32768)
        capacity = wt.DWORD(len(image))
        if not kernel.QueryFullProcessImageNameW(handle, 0, image, ct.byref(capacity)):
            return None
        if not _same_executable(image.value, expected_executable):
            return None
        created, exited, kernel_time, user_time = wt.FILETIME(), wt.FILETIME(), wt.FILETIME(), wt.FILETIME()
        if not kernel.GetProcessTimes(handle, ct.byref(created), ct.byref(exited), ct.byref(kernel_time), ct.byref(user_time)):
            return None
        created_filetime = (int(created.dwHighDateTime) << 32) | int(created.dwLowDateTime)
        if created_filetime <= 0:
            return None
        return {'processInstanceRef': _process_instance_ref(pid, created_filetime, build_ref)}
    finally:
        kernel.CloseHandle(handle)


def _line_pids(kernel):
    snapshot = kernel.CreateToolhelp32Snapshot(TH32CS_SNAPPROCESS, 0)
    if snapshot == ct.c_void_p(-1).value:
        return None
    try:
        entry = _ProcessEntry()
        entry.size = ct.sizeof(entry)
        found = []
        ok = kernel.Process32FirstW(snapshot, ct.byref(entry))
        if not ok:
            return [] if ct.get_last_error() == ERROR_NO_MORE_FILES else None
        while ok:
            if entry.exe.lower() == 'line.exe':
                found.append(int(entry.pid))
            ok = kernel.Process32NextW(snapshot, ct.byref(entry))
            if not ok and ct.get_last_error() != ERROR_NO_MORE_FILES:
                return None
        return found
    finally:
        kernel.CloseHandle(snapshot)


def _running_process(expected_executable, build_ref):
    if os.name != 'nt':
        return {'state': 'not_available'}
    try:
        kernel = ct.WinDLL('kernel32', use_last_error=True)
        kernel.CreateToolhelp32Snapshot.argtypes = [wt.DWORD, wt.DWORD]
        kernel.CreateToolhelp32Snapshot.restype = wt.HANDLE
        kernel.Process32FirstW.argtypes = [wt.HANDLE, ct.POINTER(_ProcessEntry)]
        kernel.Process32FirstW.restype = wt.BOOL
        kernel.Process32NextW.argtypes = [wt.HANDLE, ct.POINTER(_ProcessEntry)]
        kernel.Process32NextW.restype = wt.BOOL
        kernel.OpenProcess.argtypes = [wt.DWORD, wt.BOOL, wt.DWORD]
        kernel.OpenProcess.restype = wt.HANDLE
        kernel.CloseHandle.argtypes = [wt.HANDLE]
        kernel.QueryFullProcessImageNameW.argtypes = [wt.HANDLE, wt.DWORD, wt.LPWSTR, ct.POINTER(wt.DWORD)]
        kernel.QueryFullProcessImageNameW.restype = wt.BOOL
        kernel.GetProcessTimes.argtypes = [wt.HANDLE, ct.POINTER(wt.FILETIME), ct.POINTER(wt.FILETIME),
                                           ct.POINTER(wt.FILETIME), ct.POINTER(wt.FILETIME)]
        kernel.GetProcessTimes.restype = wt.BOOL
        pids = _line_pids(kernel)
    except (AttributeError, OSError, ValueError, TypeError):
        return {'state': 'not_available'}
    if pids is None:
        return {'state': 'not_available'}
    if not pids:
        return {'state': 'not_running'}
    if len(pids) > 1:
        return {'state': 'ambiguous', 'processCount': len(pids)}
    try:
        verified = _query_process(kernel, pids[0], expected_executable, build_ref)
    except (AttributeError, OSError, ValueError, TypeError):
        return {'state': 'unverified'}
    if verified is None:
        return {'state': 'unverified'}
    return {'state': 'running', **verified}


def get_client_status(executable=None):
    """Return only public compatibility/process metadata; never paths or content."""
    expected = _default_executable() if executable is None else executable
    if expected is None:
        return {'ok': False, 'code': 'LINE_BUILD_UNVERIFIED', 'client': {'verified': False},
                'process': {'state': 'not_checked'}}
    try:
        client = verify_client_build(expected)
    except ClientBuildError as error:
        return {'ok': False, 'code': error.code, 'client': {'verified': False}, 'process': {'state': 'not_checked'}}
    return {'ok': True, 'client': client, 'process': _running_process(expected, client['buildRef'])}


def main(argv=None):
    parser = argparse.ArgumentParser(description='Read-only local LINE compatibility status.')
    parser.add_argument('--exe', help='Optional executable path to verify; never echoed.')
    args = parser.parse_args(argv)
    result = get_client_status(args.exe)
    print(json.dumps(result, ensure_ascii=True, separators=(',', ':')))
    return 0 if result['ok'] else 2


if __name__ == '__main__':
    sys.exit(main())
