# db-roles-and-init.md — DB 角色隔離 + init scripts + build-time COPY

> **何時讀**：第一次寫 compose DB service、或既有 DB 要補上 writer/reader 角色隔離時。

PostgreSQL 預設 superuser（`postgres` 角色或你在 `POSTGRES_USER` 設的那個）連線權力過大。production 應用層應用**較低權限的 writer role 連線**，AI / Claude / 內部 read-only 工具走更低的 **reader role**。本檔給出 init scripts 三段式、build-time `COPY` 繞 Coolify storage 限制、`docker-compose.override.yml` 開本機 port、與 volume key suffix 重跑 initdb 的安全紅線。

---

## 三段式 init scripts（01 / 02 / 03）

PostgreSQL 容器啟動時會把 `/docker-entrypoint-initdb.d/` 內檔案**按檔名排序**執行（`*.sh` / `*.sql`）。三段式：

```
migrations/init/
├── 01_roles.sh        # 建 writer + reader role，密碼從 env 讀
├── 02_schema.sql      # CREATE SCHEMA / 業務 schema 初版
└── 03_grants.sh       # 把 schema 權限授給 writer/reader（連 default privileges）
```

### 01_roles.sh

```bash
#!/usr/bin/env bash
set -euo pipefail

# bash :? 是 POSIX 標準的「未設或空就 abort」—— compose 端 ${VAR:?} 在 Coolify 有風險
# （見 coolify-deploy/references/env-management.md），改在 script 內守。
: "${WRITER_USER:?WRITER_USER must be set}"
: "${WRITER_PASSWORD:?WRITER_PASSWORD must be set}"
: "${READER_USER:?READER_USER must be set}"
: "${READER_PASSWORD:?READER_PASSWORD must be set}"

# psql -v 把 shell 變數安全傳進去；用 :'var' 引用會自動 quote 字串值，
# 避免 SQL 內字串拼接漏出特殊字元。-v ON_ERROR_STOP=1 確保任一句失敗整個 init 失敗。
psql -v ON_ERROR_STOP=1 \
     -v writer_user="$WRITER_USER" -v writer_password="$WRITER_PASSWORD" \
     -v reader_user="$READER_USER" -v reader_password="$READER_PASSWORD" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
  DO $$
  BEGIN
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'writer_user') THEN
      EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', :'writer_user', :'writer_password');
    END IF;
    IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = :'reader_user') THEN
      EXECUTE format('CREATE ROLE %I LOGIN PASSWORD %L', :'reader_user', :'reader_password');
    END IF;
  END
  $$;
EOSQL
```

**為什麼 `format(... %I, %L)`**：`%I` 是 SQL identifier-quote、`%L` 是 SQL literal-quote。比手寫 `'%s'` 字串拼接安全（防 password 內含 `'` 造成 SQL 注入）。

### 02_schema.sql

```sql
-- 業務 schema 初版。後續所有 schema 變更走 migration（見 one-shot-migration.md），
-- 不要回頭改這份 —— 已存在的 DB volume 不會重跑 init。
CREATE SCHEMA IF NOT EXISTS app;
SET search_path TO app, public;

CREATE TABLE IF NOT EXISTS users (
  id          BIGSERIAL PRIMARY KEY,
  email       TEXT NOT NULL UNIQUE,
  created_at  TIMESTAMPTZ NOT NULL DEFAULT now()
);
-- ...
```

### 03_grants.sh

```bash
#!/usr/bin/env bash
set -euo pipefail
: "${WRITER_USER:?}"
: "${READER_USER:?}"

psql -v ON_ERROR_STOP=1 \
     -v writer_user="$WRITER_USER" -v reader_user="$READER_USER" \
     --username "$POSTGRES_USER" --dbname "$POSTGRES_DB" <<-'EOSQL'
  -- writer：app schema 內 CRUD，但禁 DDL
  GRANT USAGE ON SCHEMA app TO :"writer_user";
  GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA app TO :"writer_user";
  GRANT USAGE, SELECT ON ALL SEQUENCES IN SCHEMA app TO :"writer_user";
  ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT SELECT, INSERT, UPDATE, DELETE ON TABLES TO :"writer_user";
  ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT USAGE, SELECT ON SEQUENCES TO :"writer_user";

  -- reader：只 SELECT
  GRANT USAGE ON SCHEMA app TO :"reader_user";
  GRANT SELECT ON ALL TABLES IN SCHEMA app TO :"reader_user";
  ALTER DEFAULT PRIVILEGES IN SCHEMA app
    GRANT SELECT ON TABLES TO :"reader_user";

  -- reader 預設 transaction read only（強化第二層；endpoint / app 端會 SET LOCAL 再強化一次）
  ALTER ROLE :"reader_user" SET default_transaction_read_only = on;

  -- public schema 收乾淨：reader 不該透過 public.* 繞道
  REVOKE ALL ON SCHEMA public FROM :"reader_user";
EOSQL
```

`ALTER DEFAULT PRIVILEGES` 必加 —— 否則之後跑 migration 新增的 table，reader / writer 都讀不到（典型「上線一週才發現新功能 500」）。

---

## build-time COPY：繞過 Coolify mount_path 限制

當同一個 Coolify application 內有**兩個 postgres service**（如 `app-db` 加 `backup-db`），兩者都想 mount `./migrations/init:/docker-entrypoint-initdb.d`，Coolify storage 表的 `(application_id, mount_path)` unique-like 限制會**靜默 drop** 後加的那個（見 coolify-deploy `references/compose.md` Quirk 3）：

- compose YAML 寫了
- `coolify app storage list` 看不到那筆
- 容器啟動時 `/docker-entrypoint-initdb.d/` 是空的 → init script 沒跑 → 角色 / schema 從未建 → app 端 `password authentication failed`

**繞道**：第二個 service（甚至所有 service，為了 build 可重現性更建議全部統一）的 init scripts 改 **build-time `COPY` 進該 service 自家的 Dockerfile**：

```dockerfile
# app-db/Dockerfile
ARG POSTGRES_VERSION=16
FROM postgres:${POSTGRES_VERSION}

# 整個目錄 COPY —— 不用 *.sh / *.sql glob，避免目錄只含其中一種副檔名時
# Docker 因 unmatched glob 報 build error。
COPY migrations/init/ /docker-entrypoint-initdb.d/

# postgres entrypoint 對 .sh 要求可執行；在 image 內補 +x，不依賴 host 端權限。
RUN find /docker-entrypoint-initdb.d -name '*.sh' -exec chmod +x {} +
```

```yaml
# docker-compose.yml
services:
  app-db:
    build:
      context: .
      dockerfile: ./app-db/Dockerfile
      args:
        POSTGRES_VERSION: ${POSTGRES_VERSION:-16}
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
      WRITER_USER: ${WRITER_USER}
      WRITER_PASSWORD: ${WRITER_PASSWORD}
      READER_USER: ${READER_USER}
      READER_PASSWORD: ${READER_PASSWORD}
      TZ: Asia/Taipei
    expose:
      - "5432"
    volumes:
      - app-db-data:/var/lib/postgresql/data
    healthcheck:
      # 用 writer role 試登 —— 純 pg_isready 只測 socket，role 沒建也會綠
      test: ["CMD-SHELL", "PGPASSWORD=$$WRITER_PASSWORD psql -h 127.0.0.1 -U $$WRITER_USER -d $$POSTGRES_DB -c 'SELECT 1' >/dev/null 2>&1"]
      interval: 10s
      timeout: 5s
      retries: 10
      start_period: 20s
    restart: unless-stopped

volumes:
  app-db-data:
    name: ${COMPOSE_PROJECT_NAME}-app-db-data
```

⚠️ `.dockerignore` 配套：根 `.dockerignore` 若排除整個 `migrations/`（典型寫法），需 **negation pattern** 讓 build context 看得到子目錄**及其內所有檔案**：

```
migrations
!migrations/init/
!migrations/init/**
```

單寫 `!migrations/init` 在某些 Docker 版本只 unignore 目錄入口、descendants 仍被排除 → `COPY` 找不到檔。

---

## healthcheck 用實際密碼登入

`pg_isready -U postgres -d postgres` 只測 PG socket 在不在接 connection —— **superuser 一直能 connect，role 沒建成功也會綠**，這是踩雷常見來源。

正確的 healthcheck：用 writer role 跑 `SELECT 1`（如上面 yaml）。若 role 沒建、密碼錯、grants 沒到位，healthcheck 會持續 fail，Coolify 不會把流量切過去，避免 app 端「上線後第一個 query 才發現 DB 進不去」。

---

## `docker-compose.override.yml` 開本機 port

production 主檔 **只能 `expose`，不能 `ports`**（見 coolify-deploy `references/compose.md`「`expose` vs `ports`」段）。本機想用 DBeaver / Datagrip 連 DB：

```yaml
# docker-compose.override.yml（與主檔同目錄，git tracked 也 OK —— 不含機密）
services:
  app-db:
    ports:
      - "127.0.0.1:5433:5432"
```

`docker compose up` 自動疊加；Coolify 部署時忽略 override。`127.0.0.1` 綁定避開公網；`5433` 避主機已有 PG 服務在 5432 衝突。

---

## Volume suffix bump（重跑 initdb）—— 紅線

PostgreSQL 的 init scripts 只在 **空 PGDATA 首次初始化**時跑一次。改了 init script 後重新 deploy，**舊 volume 內已存在的 DB 不會再跑 init** —— 你的新角色、新 schema、新 grants 不會生效。

bump volume key 強迫換一顆空 volume：

```yaml
# Before
volumes:
  app-db-data:
    name: ${COMPOSE_PROJECT_NAME}-app-db-data

# After
volumes:
  app-db-data-v2:
    name: ${COMPOSE_PROJECT_NAME}-app-db-data-v2
```

下次 deploy：docker 認不出新名字 → 建新空 volume → PG 跑完整 initdb。舊 volume 孤立在 host 上，**保留 rollback**（key rename 回舊名即瞬間切回舊資料）。

### ⚠️ 紅線

- **只用於首次建置或可拋棄環境**（development / staging / 尚未 cutover 的 production 空殼）。
- **已有真實業務資料的 production 禁直接 rename volume** —— 新 volume = 空 DB，app 起來會發現「資料消失」（其實只是孤立在舊 volume 但 app 角度等同遺失）。正解：先用 `references/db-migration.md` 的 dump → 新 volume → restore 流程，再切 key。
- **嚴禁拿 volume rename 來「修」schema** —— production schema 變更一律走 migration + backup/restore，不是 wipe + reinit。

清理孤兒 volume（新 volume 跑穩後）：

```bash
# 到 Coolify host
docker volume ls | grep <舊 volume name>
docker volume rm <舊 volume name>
```

---

## 一致性 checklist

- [ ] `01_roles.sh` 用 `psql -v` + `format(%I, %L)`，不字串拼接
- [ ] `03_grants.sh` 必含 `ALTER DEFAULT PRIVILEGES`
- [ ] reader role 設 `default_transaction_read_only = on`
- [ ] reader 對 `public` schema 已 REVOKE
- [ ] healthcheck 用 writer role `SELECT 1`，不只 `pg_isready -U postgres`
- [ ] `.dockerignore` 用 negation pattern 放行 `migrations/init/**`
- [ ] override.yml 的 `ports` bind `127.0.0.1`，避開公網
- [ ] 改 init script 時若舊 volume 已含真實資料，**走 dump/restore 不 rename volume**
