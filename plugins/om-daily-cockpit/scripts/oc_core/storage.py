"""Optional 模組（intel/tender/fb）的儲存後端 adapter（Phase 4.5 框架）。

三後端，由 `config.modules.<key>.storage` 決定：
- quick_only：**不落任何 DB**（預設）。保證「停用 / quick 模式不寫任何資料庫」。
- sqlite：本機 SQLite 檔（零外部依賴）。
- postgres：**tenant 自己的** Postgres（env DATABASE_URL），用通用資料表名，
  **不寫死任何 `cockpit` schema**（別的 tenant 沒有那個 schema）。

MVP：框架打包但預設 quick_only / 停用；真正啟用前 tenant 須在 config 選定後端。
"""

from __future__ import annotations

import sqlite3
from pathlib import Path

from .config import Config, ConfigError, Module, require_env

# 只允許模組對應的固定資料表名，杜絕表名注入。
_ALLOWED_TABLES = {"intel", "tender", "fb"}

_DDL_COLUMNS = (
    "url TEXT, title TEXT, summary TEXT, published_at TEXT"
)


def _safe_table(table: str) -> str:
    if table not in _ALLOWED_TABLES:
        raise ConfigError(f"不允許的資料表名：{table!r}（限 {sorted(_ALLOWED_TABLES)}）")
    return f"{table}_items"


class QuickOnlyStorage:
    """不落 DB —— store 為 no-op。用於 quick_only（預設）與停用模組。"""

    backend = "quick_only"

    def store(self, table: str, items: list[dict]) -> int:
        _safe_table(table)  # 仍驗證表名合法
        return 0  # 不持久化

    def recent(self, table: str, since_iso: str) -> list[dict]:
        return []


class SqliteStorage:
    """本機 SQLite 後端。"""

    backend = "sqlite"

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)

    def _ensure(self, conn: sqlite3.Connection, table: str) -> str:
        t = _safe_table(table)
        conn.execute(
            f"CREATE TABLE IF NOT EXISTS {t} "
            f"(id INTEGER PRIMARY KEY AUTOINCREMENT, {_DDL_COLUMNS}, "
            f"created_at TEXT DEFAULT CURRENT_TIMESTAMP, UNIQUE(url))"
        )
        return t

    def store(self, table: str, items: list[dict]) -> int:
        if not items:
            _safe_table(table)
            return 0
        with sqlite3.connect(self.db_path) as conn:
            t = self._ensure(conn, table)
            n = 0
            for it in items:
                cur = conn.execute(
                    f"INSERT OR IGNORE INTO {t} (url, title, summary, published_at) "
                    f"VALUES (?, ?, ?, ?)",
                    (it.get("url"), it.get("title"), it.get("summary"), it.get("published_at")),
                )
                n += cur.rowcount
            return n

    def recent(self, table: str, since_iso: str) -> list[dict]:
        with sqlite3.connect(self.db_path) as conn:
            t = self._ensure(conn, table)
            conn.row_factory = sqlite3.Row
            rows = conn.execute(
                f"SELECT * FROM {t} WHERE created_at >= ? ORDER BY published_at DESC",
                (since_iso,),
            ).fetchall()
            return [dict(r) for r in rows]


class PostgresStorage:
    """tenant 自己的 Postgres 後端（schema 預設 public，非寫死 cockpit）。"""

    backend = "postgres"

    def __init__(self, dsn: str, schema: str = "public"):
        self.dsn = dsn
        self.schema = schema

    def _connect(self):
        try:
            import psycopg2  # lazy import：未啟用此後端者免裝
        except ImportError:  # pragma: no cover
            raise ConfigError("postgres 後端需要 psycopg2-binary：pip install psycopg2-binary")
        return psycopg2.connect(self.dsn)

    def _qualified(self, table: str) -> str:
        return f'"{self.schema}"."{_safe_table(table)}"'

    @staticmethod
    def _ddl(q: str) -> str:
        return (
            f"CREATE TABLE IF NOT EXISTS {q} "
            f"(id SERIAL PRIMARY KEY, url TEXT UNIQUE, title TEXT, summary TEXT, "
            f"published_at TEXT, created_at TIMESTAMPTZ DEFAULT now())"
        )

    def store(self, table: str, items: list[dict]) -> int:
        if not items:
            _safe_table(table)
            return 0
        q = self._qualified(table)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(self._ddl(q))
            n = 0
            for it in items:
                cur.execute(
                    f"INSERT INTO {q} (url, title, summary, published_at) "
                    f"VALUES (%s, %s, %s, %s) ON CONFLICT (url) DO NOTHING",
                    (it.get("url"), it.get("title"), it.get("summary"), it.get("published_at")),
                )
                n += cur.rowcount
            return n

    def recent(self, table: str, since_iso: str) -> list[dict]:
        q = self._qualified(table)
        with self._connect() as conn, conn.cursor() as cur:
            cur.execute(self._ddl(q))  # 首跑時表未建 → 先確保存在（鏡像 SQLite，避免 undefined-table）
            cur.execute(
                f"SELECT url, title, summary, published_at FROM {q} "
                f"WHERE created_at >= %s ORDER BY published_at DESC",
                (since_iso,),
            )
            cols = [c[0] for c in cur.description]
            return [dict(zip(cols, row)) for row in cur.fetchall()]


def get_storage(module: Module, config: Config, base_dir: Path | None = None):
    """工廠：依 module.storage 回傳對應 adapter。

    - quick_only（預設）→ QuickOnlyStorage（不落 DB）
    - sqlite → SqliteStorage（檔案放 base_dir 下 .om-cockpit/<tenant>.sqlite3）
    - postgres → PostgresStorage（DSN 從 config.services.database_url_env 指向的環境變數讀）
    """
    backend = module.storage
    if backend == "quick_only":
        return QuickOnlyStorage()
    if backend == "sqlite":
        root = Path(base_dir) if base_dir else Path.cwd()
        db_path = root / ".om-cockpit" / f"{config.tenant_id}.sqlite3"
        return SqliteStorage(db_path)
    if backend == "postgres":
        dsn = require_env(config.services.database_url_env)
        return PostgresStorage(dsn)
    raise ConfigError(f"未知 storage 後端：{backend!r}")
