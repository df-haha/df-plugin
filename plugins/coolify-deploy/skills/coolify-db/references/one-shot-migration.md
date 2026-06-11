# one-shot-migration.md — Compose 內 one-shot migration service

> **何時讀**：要在 Coolify 上跑 schema migration / data backfill，又不想 SSH 進 host 手動 `psql` 時。

對於需要在部署時跑一次、跑完就退出的工作（schema migration、backfill、初始化資料、跨環境 dump+restore），canonical 模式是 compose 加一個 `restart: "no"` + `depends_on db: service_healthy` 的 service，讓 Coolify 啟動它一次，app service 用 `service_completed_successfully` 等它跑完。

---

## 完整範本

```yaml
services:
  db:
    # ... 見 db-roles-and-init.md

  migrate:
    build:
      context: ./migrate
      dockerfile: Dockerfile
    container_name: ${COMPOSE_PROJECT_NAME}-migrate
    restart: "no"                                  # ← 關鍵：跑完就退，不會自動重啟
    environment:
      DB_HOST: db
      DB_PORT: 5432
      DB_USER: ${WRITER_USER}
      DB_PASSWORD: ${WRITER_PASSWORD}
      DB_NAME: ${POSTGRES_DB}
      TZ: Asia/Taipei
    depends_on:
      db:
        condition: service_healthy
    # command 可寫死執行 migrate 工具，或保留給 Dockerfile CMD
    command: ["alembic", "upgrade", "head"]

  app:
    build:
      context: ./backend
      dockerfile: Dockerfile
    environment:
      DATABASE_URL: postgresql+asyncpg://${WRITER_USER}:${WRITER_PASSWORD}@db:5432/${POSTGRES_DB}
      # ...
    depends_on:
      db:
        condition: service_healthy
      migrate:
        condition: service_completed_successfully   # ← 等 migrate 退出且 exit code 為 0
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/api/v1/health"]
      # ...
    restart: unless-stopped
```

`service_completed_successfully` 的精確語義：等到該 service 的 container exit 且 **exit code 為 0**。exit 非 0 → app service 不啟動，整個 deploy 標記失敗。這正是我們要的：migration 失敗時 app 不能上線。

---

## 紅線：migration 必須先於依賴它的 code 部署

最常見的 production incident pattern：

1. PR 改了 schema 加新欄位 + 改了 code 讀新欄位 + 改了 service 順序
2. 同一個 commit merge 進 main
3. Coolify 部署：app build 完 → migrate service 起 → 但 `depends_on` 寫錯，app 與 migrate 平行起 → app 還沒等到 migrate 跑完就接到流量 → 讀新欄位 → 全 500

**保證時序的兩條紀律**：

1. **`app.depends_on.migrate.condition` 必是 `service_completed_successfully`**（不是 `service_started`、不是 `service_healthy` —— `restart: "no"` service 沒有 healthcheck）。
2. **rolling deploy / blue-green 時要注意**：Coolify 部署新版時舊 app container 仍在服務，新 migrate 跑完才起新 app。如果 migration 是 **destructive**（DROP COLUMN / RENAME），舊 app 會立刻噴錯。解法：
   - **expand-then-contract pattern**：先 deploy 「migration 加新欄 + code 雙寫 / 讀兩邊」，跑穩；再 deploy「移除舊欄位 + code 只讀新欄」。
   - 不可逆 schema 改動拆兩個 PR / 兩次 deploy 完成，**不是**塞同一個 commit 求快。

---

## 何時用 one-shot service vs 何時用啟動腳本

| 形狀 | 用途 | 範例 |
|------|------|------|
| **one-shot service**（`restart: "no"`） | 跑完就退、要 audit trail、要明確 fail → 全 stack 失敗 | schema migration、跨環境 dump+restore（見 `db-migration.md`）、首次資料 backfill |
| **app entrypoint 啟動腳本** | 每次 container 起來都跑（idempotent） | `alembic upgrade head` 放 entrypoint（多 replica 時要 lock）、創 system user、warm cache |
| **長駐 idle container + Scheduled Task** | 週期性（cron-like） | 每天備份、定期同步、清理（見 coolify-deploy `references/deploy-and-rollback.md`） |

混用紀律：「啟動腳本跑 idempotent migration」+「one-shot service 跑 destructive / 跨環境一次性 task」，不要兩種都塞進 app entrypoint（多 replica 時跑兩次很糟）。

---

## 用後清理（與 db-migration.md 同步）

跨環境 dump+restore 這類**一次性** migration 跑完一定要回頭刪：

1. 移除 `migrate` service 整段（含 `build:` / `command:` / `depends_on:`）
2. 移除 `app.depends_on.migrate`（否則 compose 引用不存在的 service，redeploy 會失敗）
3. 刪除 Coolify env vars 中只有這次 migration 用的 `SOURCE_DB_*` 變數
4. Redeploy 確認 app 正常啟動

⚠️ **在移除前，任何 redeploy 都會重新執行 migrate** —— 可能覆蓋已新增的資料。確認搬遷成功後盡快清理。

對於 **schema migration**（如 alembic），migrate service 本身可以保留（它 idempotent、每次只跑新版本），但要確保 migration tool 自己有 lock 機制（alembic、flyway、liquibase 都有），避免 rolling deploy 時並行跑出 race。

---

## debug 撇步

migrate service 跑失敗時你最需要的是它的 stdout / stderr。但：

- `coolify app logs <uuid>` 只回 **running** container 的 log → migrate 已退就抓不到
- runtime container log 在 host 上 `docker logs <container>` 還在，但要 SSH

最簡單的辦法：在 migrate 內部把 stdout/stderr 同時 tee 到 file（mount volume）：

```yaml
migrate:
  # ...
  volumes:
    - migrate-logs:/var/log/migrate
  command: ["sh", "-c", "alembic upgrade head 2>&1 | tee -a /var/log/migrate/$(date +%Y%m%d-%H%M%S).log"]
```

或在 migration 容器內裝個小 wrapper，跑完成功也好失敗也好都把 log POST 到 Seq（見 coolify-deploy `references/seq.md`）。

Coolify build log 的讀取（含 `read:sensitive` token 紀律）走姊妹 skill **coolify-logs**。
