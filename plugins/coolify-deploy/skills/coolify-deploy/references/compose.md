# compose.md — docker-compose.yml 模板與規則

> **何時讀**：撰寫或修改 `docker-compose.yml` 時。

Coolify 直接讀 repo 內的 `docker-compose.yml`（**檔名一定是 `.yml`，不是 `.yaml`**）。本檔給出 canonical（標準）範本、最小範本、dev 變體，以及所有 compose 撰寫規則與檔案儲存規範。

下方 canonical 範本已套用本 skill 全部調和規則，**請以本範本為準**，不要回頭抄任何外部範例（含 v1.3 原始範例——它的 `latest` tag 與 `SEQ_FIRSTRUN_ADMINPASSWORD: ${...}` 寫法與本規則牴觸）。

---

## Canonical 範本（Frontend + Backend + PostgreSQL + Redis + Adminer + Seq）

```yaml
services:
  frontend:
    build:
      context: ./frontend
      dockerfile: Dockerfile
    environment:
      SERVICE_URL_FRONTEND_80:          # Coolify magic env，冒號後留空
      VITE_API_BASE_URL: ${VITE_API_BASE_URL}
      TZ: Asia/Taipei
    expose:
      - "80"
    depends_on:
      backend:
        condition: service_healthy
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:80/"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  backend:
    build:
      context: ./backend
      dockerfile: Dockerfile
    environment:
      SERVICE_URL_BACKEND_8000:         # Coolify magic env，冒號後留空
      APP_ENV: ${APP_ENV}
      # 內建 postgres → inline；改用外部/RDS 時改成 DATABASE_URL: ${DATABASE_URL}（見 env-management.md）
      DATABASE_URL: postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}
      REDIS_URL: "redis://redis:6379"
      JWT_SECRET_KEY: ${JWT_SECRET_KEY}
      CORS_ORIGINS: ${CORS_ORIGINS}
      SEQ_INGESTION_URL: http://seq      # compose 內走 service 名 + port 80
      APP_NAME: backend
      TZ: Asia/Taipei
    expose:
      - "8000"
    depends_on:
      postgres:
        condition: service_healthy
      redis:
        condition: service_healthy
      seq:
        condition: service_started       # Seq 無 healthcheck，禁 service_healthy
    healthcheck:
      test: ["CMD", "curl", "-fsS", "http://localhost:8000/api/v1/health"]
      interval: 10s
      timeout: 5s
      retries: 5
      start_period: 30s
    restart: unless-stopped

  postgres:
    image: postgres:${POSTGRES_VERSION}  # per-service 版本變數，禁 latest
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: ${POSTGRES_PASSWORD}
      POSTGRES_DB: ${POSTGRES_DB}
      TZ: Asia/Taipei
    expose:
      - "5432"
    volumes:
      - postgres-data:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}"]
      interval: 5s
      timeout: 5s
      retries: 10
    restart: unless-stopped

  redis:
    image: redis:${REDIS_VERSION}        # per-service 版本變數，禁 latest
    environment:
      TZ: Asia/Taipei
    expose:
      - "6379"
    volumes:
      - redis-data:/data
    healthcheck:
      test: ["CMD", "redis-cli", "ping"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

  adminer:                                # 選配；正式環境的安全底線見 optional-services.md
    image: adminer:4.8.1-standalone       # 第三方固定版工具，允許 pinned literal
    environment:
      SERVICE_URL_ADMINER_8080:           # 冒號後留空
      ADMINER_DEFAULT_SERVER: postgres
      ADMINER_DEFAULT_DB_DRIVER: pgsql
      TZ: Asia/Taipei
    expose:
      - "8080"
    depends_on:
      postgres:
        condition: service_healthy
    restart: unless-stopped

  seq:                                    # 選配；CLEF / first-run 密碼規則見 optional-services.md
    image: datalust/seq:${SEQ_VERSION}   # per-service 版本變數，禁 latest
    environment:
      ACCEPT_EULA: "Y"
      SERVICE_URL_SEQ_80:                 # 冒號後留空
      SEQ_FIRSTRUN_ADMINUSERNAME: admin
      SEQ_FIRSTRUN_ADMINPASSWORD:         # 留空：非 magic env，不可寫 ${...}（見 optional-services.md）
      TZ: Asia/Taipei
    # ⚠️ 故意不設 healthcheck — datalust/seq 不一定有 curl/wget，易卡死
    expose:
      - "80"
    volumes:
      - seq-data:/data
    restart: unless-stopped

volumes:
  postgres-data:
    name: ${COMPOSE_PROJECT_NAME}-postgres-data
  redis-data:
    name: ${COMPOSE_PROJECT_NAME}-redis-data
  seq-data:
    name: ${COMPOSE_PROJECT_NAME}-seq-data
```

> Adminer / Seq 是**選配**服務。不需要就整段移除（含其 volume 與其他 service 對它的 `depends_on`）；勾選與生成必須一致，否則屬安全/設定事件——詳見 `optional-services.md`。

---

## 最小範本（純前端，Vite）

```yaml
services:
  frontend:
    build:
      context: .
      dockerfile: Dockerfile
    environment:
      SERVICE_URL_FRONTEND_80:
      TZ: Asia/Taipei
    expose:
      - "80"
    healthcheck:
      test: ["CMD", "wget", "-qO-", "http://localhost:80/"]
      interval: 10s
      timeout: 5s
      retries: 5
    restart: unless-stopped

volumes: {}
```

---

## development 變體

本地開發用獨立檔 `docker-compose.development.yml`，通常只跑 `postgres`（後端 / 前端走各自的 dev server）。**dev 變體允許對 postgres 開 `ports`** 給本機 dev server 連線——這是 dev 例外，production 主檔禁開 `ports`。

```yaml
# docker-compose.development.yml
services:
  postgres:
    image: postgres:${POSTGRES_VERSION}
    ports:
      - "5432:5432"                       # dev 例外：開給本機 dev server
    environment:
      POSTGRES_USER: ${POSTGRES_USER}
      POSTGRES_PASSWORD: development
      POSTGRES_DB: myapp_development
      TZ: Asia/Taipei
    volumes:
      - postgres-development-data:/var/lib/postgresql/data

volumes:
  postgres-development-data:
    name: ${COMPOSE_PROJECT_NAME}-postgres-development-data
```

---

## compose 撰寫規則

### 環境變數一律 map 語法
- `environment` 用 `key: value` map，**禁** `- KEY=value` list。同一 service 不可混用兩語法。
- 理由：與 `SERVICE_URL_*:` 的「冒號後留空」一致；避免 `=` / `$` / `#` / 空白的跳脫陷阱；YAML map 可用引號明確保留字串型別。
- 值含 `:` / `#` / 前後空白時用雙引號：`REDIS_URL: "redis://redis:6379"`、`ACCEPT_EULA: "Y"`。

### 鎖版（禁 `latest`）
- **絕對禁** `latest`。
- compose image tag 走 **per-service 版本變數**：`postgres:${POSTGRES_VERSION}`、`redis:${REDIS_VERSION}`、`datalust/seq:${SEQ_VERSION}`——**不是**單一 `${SERVICE_VERSION}`。
- 第三方固定版工具（如 `adminer:4.8.1-standalone`）允許 pinned literal。
- Dockerfile base image 鎖 patch literal 或 build ARG（見 `dockerfile-backend.md` / `dockerfile-frontend.md`）。
- 版本鎖定到 patch；升級走改變數值 + 重新部署。

### healthcheck（用可用的 native probe）
- 每個關鍵路徑 service（app / DB / cache）必設 healthcheck，**用該 image 實際有的探針**：
  - HTTP service → `curl -fsS ...` 或 `wget -qO- ...`
  - Postgres → `pg_isready -U <user> -d <db>`
  - Redis → `redis-cli ping`
- backend `/api/v1/health` 是 acceptance gate（回 200）。
- **Seq 一律不設 healthcheck**（image 不一定有 curl/wget，易永遠失敗卡死）。
- Adminer 是管理 UI、非關鍵路徑，base image 可能無探針——可省略 healthcheck（同 Seq 顧慮）。

### depends_on
- 依賴**有 healthcheck** 的 service 才用 `condition: service_healthy`。
- 依賴 **Seq**（無 healthcheck）**禁** `service_healthy`，只能 plain `depends_on` 或明標 `condition: service_started`。
- **log ingestion 不得因 Seq 未 healthy 阻斷主服務啟動**（logger 要能 fallback console，見 `optional-services.md`）。

### TZ
- 每個 service 必設 `TZ: Asia/Taipei`（時戳對齊，ISO 8601 + offset）。Dockerfile 端也要裝 `tzdata` 並設 `TZ`。

### volumes
- DB / 持久資料必用 **named volume**，命名走 `${COMPOSE_PROJECT_NAME}-*`（避免資料遺失、跨機可遷移）。
- **禁** host bind mount 存 DB 資料（Coolify 跨機難遷移）。

### 網路與對外 port
- **禁**手寫 `networks` 區塊——Coolify 自管同 compose 內的預設 network，service 間用 service 名互連（如 `postgres:5432`、`http://seq`）。
- production **禁** `ports`、**禁** `network_mode: host`；HTTP service 用 `expose` + Coolify 反向代理對外（見 `domains-and-tls.md`）。
- dev 變體可對 postgres 開 `ports`（見上）。

### command 禁用變數
- **禁**在 `command` 中使用 `${Variables}`——變數只放 `environment` 區塊。

### 其餘核心約束
- `${VAR}`，**禁** `${VAR:?error}` 語法。
- 所有機密（token / password / connection string）放 Coolify runtime env，**不**寫死在 compose（見 `env-management.md`）。
- `.env*` 檔**禁**進 git / image。

---

## 檔案儲存規範

- **Binary 檔案禁存進 DB** — 會造成 DB 肥大、備份 / 還原失敗。
- Binary 一律以檔案方式儲存；**優先用 S3**（移機時不需搬檔）。
- 若暫用本地儲存，必須掛 **named volume**（`${COMPOSE_PROJECT_NAME}-*`），不要用匿名 volume 或 host bind mount。

---

## 常見問題（compose）

| 問題 | 原因 | 解法 |
|------|------|------|
| 服務間無法通訊 | 手寫了 `networks` | 移除 `networks` 區塊，用 service 名互連 |
| 環境變數無效 | 用了 `${VAR:?}` 語法 | 改 `${VAR}` |
| `environment` parse error / 變數沒帶入 | 用了 `- KEY=value` list | 改 map 語法 |
| SERVICE_URL 無值 | 冒號後填了內容或用 `${}` 引用 | 冒號後保持空白（見 `service-url.md`） |
| DB / Redis 加 SERVICE_URL 無效 | TCP 服務不支援 magic URL | 不要填，改用 `DATABASE_URL` / `REDIS_URL` |
| 資料遺失 | volume 未命名 | 用 `${COMPOSE_PROJECT_NAME}-*` named volume |
| 網站無法存取 | 沒設 `expose` | 對 HTTP service 加 `expose` |
| 依賴 service 啟動了但其實沒 ready | 用了 `service_started` 或對方沒 healthcheck | 對方加 healthcheck，依賴改 `service_healthy`（Seq 例外） |
| image 拉到非預期版本 | 用了 `latest` | 改 per-service `*_VERSION` 變數鎖版 |
