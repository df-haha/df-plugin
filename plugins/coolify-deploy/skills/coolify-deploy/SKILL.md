---
name: coolify-deploy
description: Use when deploying apps on Coolify or editing a Coolify docker-compose.yml, Dockerfile, env/secrets, SERVICE_URL/SERVICE_FQDN magic env, Adminer or Seq services, deploy webhooks, rollback, custom domains, or TLS (self-hosted PaaS + Docker Compose).
---

# Coolify Deploy

## Overview

Coolify 是**自架 PaaS（平台即服務）+ Docker Compose**。部署模型：`git push` → GitHub webhook → Coolify 在自架機器上拉 git → `docker build` → healthcheck → 健康才切流量。本 skill 給出整條 CD（持續部署）生命週期的撰寫規則：compose、Dockerfile、env/機密、對外曝露、選配服務、部署/回滾、觀測、網域/TLS。

本檔放**永遠遵守的核心規則 + 導航**；細節分流到 `references/`，依任務只讀需要的那幾份。

> **範例棧聲明**：reference 中的 Dockerfile / 程式碼以 **FastAPI + uv** 與 **Vite / Next.js** 為範例棧。其他技術棧自行替換 base image 與啟動指令，但 compose 規則、env/機密規則、部署/回滾規則與安全底線**一律適用**。

## 部署模型（永遠遵守）

```
git push origin main
   │
   ▼  GitHub webhook
Coolify（自架，於自架機器拉 git）
   │  docker build（用 repo 內 Dockerfile）
   │  或 docker compose（用 repo 內 docker-compose.yml）
   ▼
新容器啟動 → healthcheck pass
   ▼
切流量（Coolify 內建零停機；不健康則自動回滾）
```

- Coolify **從 git 拉**並自行 build，**禁** CI 推 image（GitHub Actions 只負責 merge 前綠燈，不負責 deploy）。
- 主分支 `main` → production；`staging` 分支 → staging。兩者必獨立 Coolify application（各自 env / DB）。
- 機密一律由 Coolify runtime env 注入，**禁** `.env*` 檔進容器 / git / image。

## Compose 核心規則（永遠遵守）

撰寫 `docker-compose.yml` 時這些不可違反（完整模板與逐項說明見 `references/compose.md`）：

1. **檔名 `docker-compose.yml`**（`.yml`，不是 `.yaml`）。
2. **禁手寫 `networks`** — Coolify 自管；service 間用 service 名互連（`postgres:5432`、`http://seq`）。
3. **production 禁 `ports`、禁 `network_mode: host`** — HTTP service 用 `expose` + Coolify 反代對外（dev 變體可對 postgres 開 `ports`）。
4. **`environment` 一律 map 語法**（`key: value`），禁 `- KEY=value` list；同一 service 不混用。值含 `:`/`#`/空白用雙引號。
5. **`command` 禁用 `${Variables}`** — 變數只放 `environment`。
6. **named volume**：持久資料用 `${COMPOSE_PROJECT_NAME}-*` 命名，禁 host bind mount 存 DB。
7. **機密走 Coolify runtime env**，禁寫死在 compose；用 `${VAR}`，**禁** `${VAR:?}` 語法。
8. **healthcheck 用可用的 native probe**：HTTP→`curl`/`wget`、Postgres→`pg_isready`、Redis→`redis-cli ping`。依賴**有 healthcheck** 的 service 才用 `condition: service_healthy`。**Seq 一律不設 healthcheck**（image 不一定有 curl/wget，易卡死）→ 依賴 Seq 只能 plain `depends_on` 或 `service_started`，且 log ingestion 不得阻斷主服務啟動。
9. **鎖版，絕對禁 `latest`**：image tag 走 **per-service 變數**（`postgres:${POSTGRES_VERSION}`、`redis:${REDIS_VERSION}`、`datalust/seq:${SEQ_VERSION}`，**不是**單一 `${SERVICE_VERSION}`）；第三方固定版工具（`adminer:4.8.1-standalone`）允許 pinned literal；Dockerfile base image 鎖 patch literal 或 build ARG。
10. **每個 service 設 `TZ: Asia/Taipei`**；Dockerfile 也裝 `tzdata` 並設 `TZ`。
11. **DATABASE_URL 是拓撲決策、非預設值**：compose 內建 postgres → inline `postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}`；外部 / RDS / managed DB → **必用** `DATABASE_URL: ${DATABASE_URL}` 整串注入。不寫「預設用哪個」，依 DB 拓撲選（env checklist 同步見 `references/env-management.md`）。
12. **Adminer 安全底線**：Adminer 無內建存取控制，**正式環境未加 Basic Auth / IP allowlist 就不要生成 adminer**；image 綁版（禁 `adminer:latest`）；**SERVICE_URL 不得貼進文件 / README / 聊天工具**（等同 DB 後門外洩）。
13. **`NODE_ENV=production` 禁放 compose `environment:`**：Coolify 會把 compose 的 `environment:` **同時**展開成 build-time `--build-arg`，`NODE_ENV=production` 注入 builder stage 會讓 `npm ci` 跳過 devDependencies，導致 `tsc` / `vite` / `next` 等 build 工具找不到（exit 127）。`NODE_ENV=production` 應**只在 Dockerfile runtime stage 用 `ENV` 設定**，不走 compose env 注入。若 builder stage 確需覆寫，加 `ENV NODE_ENV=development` 在 `RUN npm ci` 之前（見 `references/dockerfile-frontend.md` + `references/env-management.md`）。

## 兩種對外曝露機制（可並存）

| 機制 | 用途 | 怎麼寫 | reference |
|------|------|--------|-----------|
| **SERVICE_URL magic env** | 讓 Coolify 自動分配對外 URL 注入 container | `SERVICE_URL_<SVC>_<PORT>:`（冒號後留空） | `service-url.md` |
| **custom domain** | 綁自己的網域（`api.<company>.com`）+ 自動 TLS | Coolify Domains 面板 + DNS | `domains-and-tls.md` |

兩者正交：需要自訂網域用 custom domain；只要自動網址用 magic env；可同時使用。

> **magic env 例外 — Seq 密碼不走 magic env**：Coolify 只展開 `SERVICE_URL_*` / `SERVICE_FQDN_*` / `SERVICE_PASSWORD_*` / `COMPOSE_PROJECT_NAME`。`SEQ_FIRSTRUN_ADMINPASSWORD` **不在其列**：compose 寫 `SEQ_FIRSTRUN_ADMINPASSWORD:`（冒號後留空），**禁**寫 `${SEQ_FIRSTRUN_ADMINPASSWORD}`、**禁**借 `$SERVICE_PASSWORD_SEQ`，改由部署者在 Coolify env 面板手動填隨機值（或後端自動注入）。詳見 `optional-services.md`。

## When to read which reference

| 任務 | 讀這份 |
|------|--------|
| 寫 / 改 `docker-compose.yml`（含最小範本、dev 變體、檔案儲存規範） | `references/compose.md` |
| 要 Coolify 自動分配的對外 URL（SERVICE_URL / SERVICE_FQDN） | `references/service-url.md` |
| 加 Adminer / Seq、Seq CLEF logging、雙向 Lint 一致性檢查 | `references/optional-services.md` |
| 寫 backend Dockerfile（FastAPI + uv 範例棧） | `references/dockerfile-backend.md` |
| 寫 frontend Dockerfile（Vite / Next 範例棧） | `references/dockerfile-frontend.md` |
| Coolify env 注入、build/runtime、機密、APP_ENV 分層、fail-fast、部署前 checklist | `references/env-management.md` |
| 分支策略 / webhook / migration 時機 / 部署後驗證 / 兩條回滾路徑 / bootstrap | `references/deploy-and-rollback.md` |
| Log / Metric / Sentry / healthcheck 端點 | `references/observability.md` |
| 自訂網域 / TLS / HSTS / CORS / cookie domain / preview env | `references/domains-and-tls.md` |

## Bootstrap checklist（首次 push 到 Coolify 前）

scaffold 工具通常不產部署檔；未補完就 push 會在 build 階段失敗。第一次 push 前必補（細節見 `deploy-and-rollback.md`）：

- [ ] `docker-compose.yml`（見 `compose.md`）
- [ ] `backend/Dockerfile`、`frontend/Dockerfile`（見 `dockerfile-*.md`）
- [ ] Coolify 端 env 注入（`DATABASE_URL` 或 `POSTGRES_*`、`JWT_SECRET_KEY`、`CORS_ORIGINS`… 見 `env-management.md`）

驗收 3 步：

1. `docker compose -f docker-compose.yml config` 通過（yaml 合法、所有 service / image / env 解析得到）。
2. 連上 Coolify 後 `curl <coolify-host>/api/v1/health` 回 200。
3. Coolify 首次 deploy 顯示 healthcheck pass、流量切換成功。
