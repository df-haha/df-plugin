"""Pinned SQLite3MultipleCiphers C API; secrets never enter SQL or command lines."""
import ctypes as ct
import os
from pathlib import Path
from line_encrypted_snapshot import SnapshotError, _SourceChanged, _read_file
from line_scoped_core import ReaderError

DLL_SHA256 = '5030decc6d914539e3b9b7e28aa4f6de1e7161dac6fd4b21eb02e6754d9b175e'
DLL_ENV = 'LINE_MCP_SQLITE3MC_DLL'
MAX_DLL_BYTES = 128 * 1024 * 1024


def configured_dll_path(value=None):
    """Require an explicit absolute DLL path; never download or discover one."""
    if value is None:
        value = os.environ.get(DLL_ENV)
    if value is None:
        raise ReaderError('ENGINE_DLL_UNCONFIGURED')
    if (type(value) is not str or not value or value != value.strip()
            or '\0' in value):
        raise ReaderError('ENGINE_DLL_INVALID_PATH')
    try:
        path = Path(value)
    except (TypeError, ValueError):
        raise ReaderError('ENGINE_DLL_INVALID_PATH') from None
    if not path.is_absolute() or path.suffix.lower() != '.dll':
        raise ReaderError('ENGINE_DLL_INVALID_PATH')
    return path


def verified_dll_path(value=None):
    """Return the configured DLL only after a stable, no-reparse hash check."""
    path = configured_dll_path(value)
    try:
        observed = _read_file(path, optional=False, keep_data=False,
                              max_bytes=MAX_DLL_BYTES)
    except (SnapshotError, _SourceChanged, OSError, TypeError, ValueError):
        raise ReaderError('ENGINE_DLL_UNAVAILABLE') from None
    if (type(observed.digest) is not bytes or len(observed.digest) != 32
            or type(observed.size) is not int or not 1 <= observed.size <= MAX_DLL_BYTES):
        raise ReaderError('ENGINE_DLL_UNAVAILABLE')
    if observed.digest.hex() != DLL_SHA256:
        raise ReaderError('ENGINE_INTEGRITY_FAILED')
    return path


class Rows:
    def __init__(self, rows):
        self.rows = rows

    def fetchall(self):
        return self.rows


class Connection:
    def __init__(self, path, passphrase=None, *, readonly=True):
        dll = verified_dll_path()
        try:
            self.lib = ct.CDLL(str(dll))
        except OSError:
            raise ReaderError('ENGINE_DLL_UNAVAILABLE') from None
        self.db = ct.c_void_p()
        self.closed = False
        P, I, S = ct.c_void_p, ct.c_int, ct.c_char_p
        signatures = {
            'sqlite3_open_v2': ([S, ct.POINTER(P), I, S], I),
            'sqlite3_close_v2': ([P], I), 'sqlite3_db_readonly': ([P,S],I),
            'sqlite3mc_config': ([P,S,I],I), 'sqlite3mc_config_cipher': ([P,S,S,I],I),
            'sqlite3mc_cipher_index': ([S],I),
            'sqlite3_key': ([P,P,I],I), 'sqlite3_libversion': ([],S),
            'sqlite3mc_version': ([],S), 'sqlite3_busy_timeout': ([P,I],I),
            'sqlite3_prepare_v2': ([P,S,I,ct.POINTER(P),ct.POINTER(S)],I),
            'sqlite3_bind_text': ([P,I,S,I,P],I), 'sqlite3_bind_int64': ([P,I,ct.c_int64],I),
            'sqlite3_bind_null': ([P,I],I), 'sqlite3_step': ([P],I),
            'sqlite3_finalize': ([P],I), 'sqlite3_column_count': ([P],I),
            'sqlite3_column_type': ([P,I],I), 'sqlite3_column_int64': ([P,I],ct.c_int64),
            'sqlite3_column_double': ([P,I],ct.c_double), 'sqlite3_column_text': ([P,I],P),
            'sqlite3_column_blob': ([P,I],P), 'sqlite3_column_bytes': ([P,I],I),
            'sqlite3_limit': ([P,I,I],I),
        }
        for name,(arguments,result) in signatures.items():
            fn = getattr(self.lib,name)
            fn.argtypes, fn.restype = arguments,result
        try:
            self.check(self.lib.sqlite3_open_v2(str(path).encode('utf-8'),ct.byref(self.db),1 if readonly else 6,None))
            if readonly and self.lib.sqlite3_db_readonly(self.db,b'main') != 1:
                raise ReaderError('SOURCE_NOT_READONLY')
            if passphrase is not None:
                cipher = self.lib.sqlite3mc_cipher_index(b'aes128cbc')
                if cipher <= 0 or self.lib.sqlite3mc_config(self.db,b'cipher',cipher) != cipher:
                    raise ReaderError('ENGINE_CIPHER_UNAVAILABLE')
                self.lib.sqlite3mc_config(self.db,b'mc_legacy_wal',0)
                self.lib.sqlite3mc_config_cipher(self.db,b'aes128cbc',b'legacy',0)
                key_buffer = ct.create_string_buffer(passphrase)
                try:
                    self.check(self.lib.sqlite3_key(self.db,key_buffer,len(passphrase)))
                finally:
                    ct.memset(key_buffer,0,len(key_buffer))
            self.lib.sqlite3_busy_timeout(self.db,1000)
            self.lib.sqlite3_limit(self.db,0,8*1024*1024)  # maximum cell/row bytes
            self.execute('PRAGMA trusted_schema=OFF')
            self.execute('PRAGMA temp_store=MEMORY')
            if readonly:
                self.execute('PRAGMA query_only=ON')
            self.version = {'sqlite':self.lib.sqlite3_libversion().decode(),
                            'cipherEngine':self.lib.sqlite3mc_version().decode()}
        except Exception:
            self.close()
            raise

    @staticmethod
    def check(code):
        if code != 0:
            # Never include sqlite3_errmsg: it may contain private source content.
            raise ReaderError('DATABASE_READ_FAILED')

    def execute(self, sql, params=()):
        statement, tail = ct.c_void_p(), ct.c_char_p()
        try:
            sql_bytes = sql.encode('utf-8')  # pzTail points into this buffer.
            self.check(self.lib.sqlite3_prepare_v2(self.db,sql_bytes,-1,ct.byref(statement),ct.byref(tail)))
            if tail.value and tail.value.strip():
                raise ReaderError('MULTIPLE_STATEMENTS_REFUSED')
            for index,value in enumerate(params,1):
                if value is None:
                    code = self.lib.sqlite3_bind_null(statement,index)
                elif type(value) is int:
                    code = self.lib.sqlite3_bind_int64(statement,index,value)
                elif isinstance(value,str):
                    data = value.encode('utf-8')
                    code = self.lib.sqlite3_bind_text(statement,index,data,len(data),ct.c_void_p(-1))
                else:
                    raise ReaderError('INVALID_BIND_VALUE')
                self.check(code)
            rows, total = [],0
            while True:
                state = self.lib.sqlite3_step(statement)
                if state == 101:
                    break
                if state != 100:
                    raise ReaderError('DATABASE_READ_FAILED')
                row = []
                for index in range(self.lib.sqlite3_column_count(statement)):
                    kind = self.lib.sqlite3_column_type(statement,index)
                    if kind == 1:
                        value = self.lib.sqlite3_column_int64(statement,index)
                    elif kind == 2:
                        value = self.lib.sqlite3_column_double(statement,index)
                    elif kind in (3,4):
                        size = self.lib.sqlite3_column_bytes(statement,index)
                        total += size
                        if total > 16*1024*1024:
                            raise ReaderError('RESULT_TOO_LARGE')
                        pointer = (self.lib.sqlite3_column_text if kind == 3 else self.lib.sqlite3_column_blob)(statement,index)
                        value = ct.string_at(pointer,size) if size else b''
                        if kind == 3:
                            value = value.decode('utf-8')
                    else:
                        value = None
                    row.append(value)
                rows.append(tuple(row))
                if len(rows) > 2000:
                    raise ReaderError('RESULT_TOO_LARGE')
            return Rows(rows)
        finally:
            if statement:
                self.lib.sqlite3_finalize(statement)

    def close(self):
        if self.db and not self.closed:
            self.lib.sqlite3_close_v2(self.db)
            self.closed = True

    def restrict_reads(self):
        allowed = {
            '_chat': {'_id', '_midType'},
            '_groupChat': {'_chatMid','_chatName'},
            '_contact': {'_mid','_displayNameOverridden','_displayName'},
            '_profile': {'_mid','_displayName'},
            '_message': {'_id','_from','_createdTime','_text','_contentType','_contentMetadata',
                         '_contentInfo','_relatedMessageId','_type','_status','_rev','_chatId'},
        }
        callback = ct.CFUNCTYPE(ct.c_int,ct.c_void_p,ct.c_int,ct.c_char_p,ct.c_char_p,ct.c_char_p,ct.c_char_p)
        def authorize(_,action,arg1,arg2,database,trigger):
            if trigger:
                return 1
            if action == 21:  # SELECT
                return 0
            if action == 31 and arg2 in (b'instr', b'max'):
                return 0
            if action == 20 and database == b'main':
                table = arg1.decode() if arg1 else ''
                column = arg2.decode() if arg2 else ''
                if column in allowed.get(table,set()):
                    return 0
            return 1
        self.authorizer = callback(authorize)
        self.lib.sqlite3_set_authorizer.argtypes = [ct.c_void_p,callback,ct.c_void_p]
        self.lib.sqlite3_set_authorizer.restype = ct.c_int
        self.check(self.lib.sqlite3_set_authorizer(self.db,self.authorizer,None))

    def __enter__(self):
        return self

    def __exit__(self,*_):
        self.close()
