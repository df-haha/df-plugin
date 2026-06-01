# db-migration.md — 首次 DB 搬遷（外部來源 → Coolify compose DB）

> **何時讀**：第一次把外部 DB（Zeabur / Supabase / RDS / 其他 PaaS）的資料搬進 Coolify 的 compose 內建 PostgreSQL 時。

Coolify 的 DB service 不對外開 port（IT 硬約束 / 安全慣例），所以不能從外部直接 `pg_restore` 進去。本文件記錄經過實戰驗證的「compose 臨時 migrate service」搬遷法。

---

## 架構

```
外部 DB（公開端點）                    Coolify compose 內網
┌──────────────────┐                ┌──────────────────────┐
│ Zeabur / RDS / …  │  ← pg_dump ←  │  migrate container    │
│ host:port         │                │  (postgres:XX-alpine) │
└──────────────────┘                │         │             │
                                    │    psql -f crm.sql    │
                                    │         ▼             │
                                    │  db service (PG16)    │
                                    │  :5432 (internal)     │
                                    └──────────────────────┘
```

- migrate container 用 **source DB 版本的 image**（如 `postgres:18-alpine`）執行 `pg_dump`，確保 dump 工具版本匹配
- restore 到 target DB 走 compose internal network（`db:5432`），不需開 port
- `restart: "no"` + `app depends_on migrate: service_completed_successfully` 確保搬遷完成後才啟動 app

## 跨版本搬遷（如 PG18 → PG16）

直接用 `-Fc`（custom format）+ `pg_restore` 在跨 major version 降級時會失敗（dump archive version 不相容）。改用 **text format** + grep 過濾高版本語法：

```bash
pg_dump --no-owner --no-acl -h <source> -U <user> -d <db> \
  | grep -v '^\restrict' \
  | grep -v '^\unrestrict' \
  | grep -v 'SET transaction_timeout' \
  > /tmp/crm.sql

psql -h db -p 5432 -U <target_user> -d <target_db> -f /tmp/crm.sql
```

已知需過濾的 PG18 專屬語法：
- `\restrict` / `\unrestrict`（psql meta-command，PG18 dump 新增）
- `SET transaction_timeout = 0;`（PG17+ 新 GUC）

## compose 範例（migrate service 段）

```yaml
services:
  db:
    image: postgres:16-alpine
    # ... （正常 db service 設定）

  migrate:
    image: postgres:18-alpine          # 用 source DB 版本的 image
    container_name: ${COMPOSE_PROJECT_NAME}-migrate
    restart: "no"
    environment:
      SOURCE_HOST: ${SOURCE_DB_HOST}
      SOURCE_PORT: ${SOURCE_DB_PORT}
      SOURCE_USER: ${SOURCE_DB_USER}
      SOURCE_DB: ${SOURCE_DB_NAME}
      SOURCE_PASSWORD: ${SOURCE_DB_PASSWORD}
      TARGET_USER: ${POSTGRES_USER:-postgres}
      TARGET_PASSWORD: ${POSTGRES_PASSWORD}
      TARGET_DB: ${POSTGRES_DB:-mydb}
      PGSSLMODE: prefer                # source 連線 SSL；internal 會自動切 disable
      TZ: Asia/Taipei
    depends_on:
      db:
        condition: service_healthy
    command:
      - sh
      - -c
      - |
        echo "=== DB Migration: $$SOURCE_HOST:$$SOURCE_PORT → db:5432 ==="

        echo "--- Step 1: Dump (text format, filter incompatible syntax) ---"
        PGPASSWORD="$$SOURCE_PASSWORD" pg_dump --no-owner --no-acl \
          -h "$$SOURCE_HOST" -p "$$SOURCE_PORT" \
          -U "$$SOURCE_USER" -d "$$SOURCE_DB" \
          > /tmp/dump_raw.sql 2>/tmp/dump_err.log
        DUMP_RC=$$?
        if [ $$DUMP_RC -ne 0 ]; then
          echo "FATAL: pg_dump failed (rc=$$DUMP_RC):"
          cat /tmp/dump_err.log
          exit 1
        fi
        grep -v '^\restrict' /tmp/dump_raw.sql \
          | grep -v '^\unrestrict' \
          | grep -v 'SET transaction_timeout' \
          > /tmp/dump.sql
        echo "Dump: $$(wc -l < /tmp/dump.sql) lines, $$(wc -c < /tmp/dump.sql) bytes"

        echo "--- Step 2: Restore ---"
        export PGSSLMODE=disable
        PGPASSWORD="$$TARGET_PASSWORD" psql \
          -h db -p 5432 -U "$$TARGET_USER" -d "$$TARGET_DB" \
          -f /tmp/dump.sql > /tmp/restore.log 2>&1
        RESTORE_RC=$$?
        if [ $$RESTORE_RC -ne 0 ]; then
          echo "WARN: psql returned rc=$$RESTORE_RC, last 20 lines:"
          tail -20 /tmp/restore.log
        fi

        echo "--- Step 3: Verification ---"
        PGPASSWORD="$$TARGET_PASSWORD" psql -h db -p 5432 -U "$$TARGET_USER" -d "$$TARGET_DB" \
          -c "ANALYZE; SELECT relname, n_live_tup FROM pg_stat_user_tables ORDER BY relname;" || true

        echo "=== Migration finished (dump_rc=$$DUMP_RC, restore_rc=$$RESTORE_RC) ==="

  app:
    # ...
    depends_on:
      db:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully
```

## Coolify env vars 設定

在 Coolify Web UI 或 API 設定（不進 git）：

| Key | 說明 |
|-----|------|
| `SOURCE_DB_HOST` | 來源 DB hostname（如 `cgk1.clusters.zeabur.com`） |
| `SOURCE_DB_PORT` | 來源 DB port（如 `20370`） |
| `SOURCE_DB_USER` | 來源 DB 帳號（如 `root`） |
| `SOURCE_DB_NAME` | 來源 DB 名稱（如 `zeabur`） |
| `SOURCE_DB_PASSWORD` | 來源 DB 密碼 |

## 搬遷後清理（必做）

搬遷驗證通過後，**同一個 commit** 完成以下清理：

1. 移除 `migrate` service 整段
2. 移除 `app.depends_on.migrate`（否則 compose 引用不存在的 service，redeploy 會失敗）
3. 刪除 Coolify env vars 中的 `SOURCE_DB_*` 變數
4. Redeploy 確認 app 正常啟動

⚠️ **在移除前，任何 redeploy 都會重新執行 migrate**（重新 dump + restore），可能覆蓋已新增的資料。確認搬遷成功後盡快清理。

## 踩坑紀錄

| 問題 | 原因 | 解法 |
|------|------|------|
| PG18 image 在 Coolify 無法啟動 | PG18 Docker image 改 PGDATA 路徑為 `/var/lib/postgresql/18/docker`，與 Coolify volume mount `/var/lib/postgresql/data` 衝突 | 改回 PG16 image；或設 `PGDATA: /var/lib/postgresql/data` env 強制傳統路徑（Coolify 環境未驗證） |
| `-Fc` dump + `pg_restore` 跨 major 降版失敗 | PG18 custom dump archive v1.16，PG16 `pg_restore` 不認識 | 改用 text format `pg_dump`（無 `-Fc`）+ `psql -f` |
| `PGSSLMODE=require` 連線失敗 | Alpine image 缺 CA certs 或 source DB 不完整支援 SSL | 改 `prefer`（搬遷後輪替密碼） |
| `@@map` 導致表名不一致 | Prisma `@@map("snake_case")` 讓實際表名與 model 名不同 | 驗證 SQL 用實際表名（`users` 非 `"User"`），先 `grep @@map schema.prisma` |
| Coolify runtime logs 看不到已退出容器 | Coolify API `/logs` 只回 running container 的 log | 移除 `app→migrate` 依賴讓 app 正常啟動，或在 migrate script 內寫 debug echo |
| 驗證 block 讓 app 掛不起來 | `set -e` + verification SQL 錯誤 → migrate exit non-zero → app 不啟動 | 驗證段用 `|| true`（informational），只有 dump/restore 失敗才 exit 1 |

## 同版本搬遷（如 PG16 → PG16）

同版本可直接用 custom format，效率更高：

```bash
pg_dump -Fc -h <source> -U <user> -d <db> -f /tmp/dump.pgc

pg_restore --no-owner --no-acl --exit-on-error --single-transaction \
  -h db -p 5432 -U <target_user> -d <target_db> /tmp/dump.pgc
```

不需要 grep 過濾。其餘 compose 結構相同。
