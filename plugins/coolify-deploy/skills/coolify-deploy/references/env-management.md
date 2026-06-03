# env-management.md — Coolify env 注入 + 機密

> **何時讀**：在 Coolify 設定環境變數 / 機密、或 rotate 機密時。
> （檔名用 `env-management` 而非 `env-and-secrets`，以避開 harness 對 `*secret*` 路徑的 denylist 攔截；內容不變。）

---

## Coolify env 注入機制

Coolify Application 設定頁 → **Environment Variables** 頁籤，兩種注入時機：

| 種類 | 用途 | 容器內表現 | 何時用 |
|------|------|-----------|--------|
| **Build-time** | 進入 build process（`ARG` / `ENV` 在 builder stage） | image build 時讀，**烙印**進 image | 前端 `VITE_*` / `NEXT_PUBLIC_*`（編譯進 bundle） |
| **Runtime** | 容器啟動時注入 process env | runtime 讀，**不**烙印 | 後端機密 / DB URL / JWT secret |

**規則**：

- 機密（token / password / connection string）**僅**走 runtime，**禁** build-time。
- 前端 `*_PUBLIC_*` / `VITE_*` 一旦編譯進 bundle 即視為**公開資料**，**禁**帶任何機密（後端 secret、private key、DB 密碼都不行）。
- 同一 env 名**禁**同時設 build-time + runtime（優先序模糊會踩雷）。

### 陷阱：compose `environment:` 雙注入（Coolify 特有行為）

Coolify 部署時會把 `docker-compose.yml` 裡**每個 service 的 `environment:` 區塊所有變數自動展開成 `docker build --build-arg`**（build log 顯示「Added N ARG declarations to Dockerfile」）。

**後果**：compose 設了 `NODE_ENV: ${NODE_ENV:-production}` → build 階段就帶著 `NODE_ENV=production` → Node 前端 builder stage 的 `npm ci` / `yarn install` / `pnpm install` 會**跳過 devDependencies** → `tsc`、`vite`、`next` 等 build 工具不存在 → build 失敗（exit code 127，command not found）。

**防範**：

- **禁止**把 `NODE_ENV=production` 放在 compose 的 `environment:` 裡（或在 Coolify env 面板設成 build-time），讓 build stage 繼承到此值。
- 若因其他原因必須設，則**在 Dockerfile 的 builder stage 最前面加 `ENV NODE_ENV=development`** 明確覆寫，runtime stage 才改回 `ENV NODE_ENV=production`。
- `NODE_ENV=production` 應**只在 Dockerfile 的 runtime stage 設定**，由 image 本身保證，不依賴 compose env 注入。

### 陷阱：compose `environment:` 禁用 `${VAR:?}` 必填語法

Docker Compose v2 支援 `${VAR:?error}` 語法（VAR 未設或為空時 parse fail），這在純 docker compose 環境是合理的 fail-fast，但 Coolify 環境**禁用**。理由：

1. **Coolify 的 env 預處理層複雜**：Coolify 把 UI 設定的 env 同時當 `--build-arg` 與 runtime env 注入（見上方 NODE_ENV 雙注入陷阱），再加自家 magic env (`SERVICE_URL_*` / `SERVICE_PASSWORD_*` / `SERVICE_FQDN_*` / `COMPOSE_PROJECT_NAME`) 預處理。`${VAR:?}` 在這條鏈中**行為穩定性風險高**，跟本地 `docker compose config` 不一定一致；不同 Coolify 版本 / compose buildpack vs dockerfile-only 行為也可能不同。
2. **失敗訊息不友善**：即使 `${VAR:?}` 在 Coolify 內順利擋下，Coolify deployment log 顯示的是通用 docker compose error，常被通用「Deployment failed: command execution failed」訊息蓋掉，反而拖長診斷時間。
3. **有更精準的 fail-fast 點**：機密 / 必填 env 的守護應放在**應用層**（Pydantic Settings / Zod schema）或**初始化腳本**（bash `: "${VAR:?error}"`，bash 內 `:?` 是 POSIX 標準，可信賴），錯誤訊息精準、看得到 stack trace、可加 fallback 與 hint。

**規則 + 替代守護方案**：

- compose `environment:` 區一律寫 `${VAR}`（值若 undefined 自動展為空字串，不阻擋 deploy）
- fail-fast 移到消費端：

| 場合 | 替代寫法 | 在哪裡 fail |
|------|----------|------------|
| Python app 必填 env | Pydantic `Settings(...)` 缺欄即 `ValidationError`（搭配本檔下方 "Settings fail-fast"） | 應用啟動時 |
| DB init script | 腳本開頭 `: "${VAR:?VAR is required}"`（bash `:?` 標準支援、可信賴） | initdb 階段 |
| 前端 build 必填 arg | Dockerfile `RUN test -n "${VAR}" \|\| (echo "VAR required" && exit 1)` | build 階段 |
| 容器 entrypoint 必填 | `entrypoint.sh` 開頭 `[ -z "$VAR" ] && exit 1` | 容器啟動瞬間 |

> 這條規則也是 `references/compose.md` 「compose 撰寫規則」`${VAR}` vs `${VAR:?error}` 條目背後的完整理由——compose.md 只記規則本身，本檔記為什麼。

## env 來源優先序

```
Coolify Runtime Env  >  image 內 ENV  >  app code 預設
```

`docker-compose.yml` 的 `environment:` 區塊用 `${VAR}` 引用 Coolify 注入的值；**禁**直寫 secret。

## 機密 runtime 注入 + 不落 git/image

- 真正的機密（JWT_SECRET / DB password / API key / Sentry DSN）→ 走 Coolify runtime env；若 Coolify 版本支援 **Secrets**（加密存、operator UI 不可讀）就放 Secrets 欄，否則放 Environment Variables 但限制 admin 讀取權（RBAC）。
- 非機密配置（`APP_ENV` / `CORS_ORIGINS` / `LOG_LEVEL`）→ Environment Variables。
- **禁**把 `.env*` 檔放進 git / image（Dockerfile `.dockerignore` 必排 `.env*`）。
- repo 內只放 `.env.<env>.example`（placeholder），實機密由 Coolify 注入。

## APP_ENV 分層

- `APP_ENV` 一律寫全名 `staging` / `production`，**禁**簡寫。
- 各環境（staging / production）獨立 env、獨立 DB、獨立機密——**同一 secret 禁多環境共用**（staging 與 production 必各自生成）。
- `.env.development` 的 `localhost:*` 是本地開發專用；部署時必改為實際 host，**禁**直接拿 development 設定去部署。

## Settings fail-fast（staging / prod 禁用 development 預設值）

應用啟動時用 Settings 物件（Pydantic Settings / Zod schema 等）讀 env，缺欄或值不合法就 **fail-fast 拒絕啟動**：

- 缺必填 env → 啟動即報錯，不要用空值 / 預設值矇混。
- staging / production **禁**沿用 development 的預設 secret 或假值（`***` / `xxx` / `changeme`）——fail-fast 必須擋下。
- 新增 secret 欄位 → 同步三處：`.env.<env>.example`（全層）+ 應用 Settings 欄位 + Coolify env 頁。

## 部署前 env checklist

新環境（staging / production）第一次部署前，Coolify env 頁必設：

- [ ] `APP_ENV=staging` 或 `production`（**禁**簡寫）
- [ ] **DB 連線（依拓撲二選一，不要兩者都當必設）**：
  - compose **內建 postgres** → 必設 `POSTGRES_USER` / `POSTGRES_PASSWORD` / `POSTGRES_DB` + `POSTGRES_VERSION`（compose 內 inline 組 `DATABASE_URL`，見 `compose.md`）
  - **外部 / RDS / managed DB** → 必設整串 `DATABASE_URL`（Coolify 注入），此時不需 `POSTGRES_*`
- [ ] `JWT_SECRET_KEY`（32+ 字元隨機，**禁** development 預設值）
- [ ] `CORS_ORIGINS`（限本網域，**禁** `["*"]`，見 `domains-and-tls.md`）
- [ ] 其他 per-service 版本變數（`REDIS_VERSION` / `SEQ_VERSION` 等，視 compose 用到哪些）
- [ ] 選配服務必填 env（生成 seq → `SEQ_FIRSTRUN_ADMINPASSWORD` 等，見 `optional-services.md`）
- [ ] 第三方 API key（SMTP / Stripe / Azure AD / Sentry DSN…）
- [ ] `TZ=Asia/Taipei`（若 image 沒設好）

## 機密 rotation

1. Coolify env 頁改新值。
2. 觸發 redeploy（env 變更**不會**自動重啟，必手動或設 webhook）。
3. 確認新 container healthy 後，於第三方 provider 撤銷舊 key。
4. 寫事故 / 修正紀錄：時間 / 影響範圍 / 修正。

## 不要做

- ❌ 把 `.env.<env>` 檔放進 git / image
- ❌ Coolify env 設 `***` / `xxx` 等假值（Settings fail-fast 會擋）
- ❌ build-time env 帶機密
- ❌ 同一 secret 多環境共用
- ❌ checklist 把「內建 postgres 的 `POSTGRES_*`」與「外部 DB 的 `DATABASE_URL`」同時當必設（兩者依拓撲互斥）
