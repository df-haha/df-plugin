---
name: coolify-deploy
description: Use when editing or authoring a Coolify docker-compose.yml, Dockerfile, env/secrets, SERVICE_URL/SERVICE_FQDN magic env, deploy webhooks, rollback flow, custom domains, TLS configuration, or Seq logging service (self-hosted PaaS + Docker Compose). For reading build/runtime logs or debugging WHY a deploy failed, use coolify-logs instead; for DB-related deploys (migrations, role isolation, Adminer, readonly access) use coolify-db. Triggers on phrases like "compose 寫不出來"、"Dockerfile 怎麼寫"、"SERVICE_URL"、"網域綁定"、"TLS / Let's Encrypt"、"rollback"、"webhook 設定"、"env 注入"、"加 Seq"、"NODE_ENV build 失敗"。
---

# Coolify Deploy

## Overview

Coolify 是**自架 PaaS(平台即服務)+ Docker Compose**。部署模型:`git push` → GitHub webhook → Coolify 在自架機器上拉 git → `docker build` → healthcheck → 健康才切流量。本 skill 給出 CD(持續部署)生命週期的撰寫規則:compose、Dockerfile、env/機密、對外曝露、部署/回滾、觀測、網域/TLS、Seq。

本檔放**永遠遵守的核心規則 + 導航**;細節分流到 `references/`,依任務只讀需要的那幾份。

> **範例棧聲明**:reference 中的 Dockerfile / 程式碼以 **FastAPI + uv** 與 **Vite / Next.js** 為範例棧。其他技術棧自行替換 base image 與啟動指令,但 compose 規則、env/機密規則、部署/回滾規則與安全底線**一律適用**。

## 姊妹 skills 與分工(routing)

此 plugin 內共 4 個 skills。本 skill(coolify-deploy)只負責**部署建構**;其他三件事去對應 skill:

| 你想做 | 去哪個 skill |
|-------|-------------|
| 寫/改 `docker-compose.yml`、Dockerfile、env、SERVICE_URL、TLS、Seq、回滾 | **coolify-deploy**(本 skill) |
| DB schema 初始化(roles/grants)、one-shot migration、Adminer、唯讀存取(dbhub MCP / `/admin/query`)、跨版本搬遷 | **coolify-db** |
| 看 build log / runtime log、CLI 指令(`coolify app logs`、`coolify deploy get`)、部署失敗自動撈 log monitor、`read:sensitive` token 使用紀律 | **coolify-logs** |
| 第一次裝 Coolify CLI、申請 API token、寫 Claude Code permission 範本(deny 破壞性指令、allow 唯讀子指令) | **coolify-setup** |

**跨域 handoff**:常見「部署 200 上線但 API 500」的根因其實是 DB schema(走 coolify-db)或 build log 解讀(走 coolify-logs)。本 skill 的 deploy-and-rollback 段會明確 handoff。

---

## 部署模型(永遠遵守)

```
git push origin main
   │
   ▼  GitHub webhook
Coolify(自架,於自架機器拉 git)
   │  docker build(用 repo 內 Dockerfile)
   │  或 docker compose(用 repo 內 docker-compose.yml)
   ▼
新容器啟動 → healthcheck pass
   ▼
切流量(Coolify 內建零停機;不健康則自動回滾)
```

- Coolify **從 git 拉**並自行 build,**禁** CI 推 image(GitHub Actions 只負責 merge 前綠燈,不負責 deploy)。
- 主分支 `main` → production;`staging` 分支 → staging。兩者必獨立 Coolify application(各自 env / DB)。
- 機密一律由 Coolify runtime env 注入,**禁** `.env*` 檔進容器 / git / image。

## Compose 核心規則(永遠遵守)

撰寫 `docker-compose.yml` 時這些不可違反(完整模板與逐項說明見 `references/compose.md`):

1. **檔名 `docker-compose.yml`**(`.yml`,不是 `.yaml`)。
2. **禁手寫 `networks`** — Coolify 自管;service 間用 service 名互連(`postgres:5432`、`http://seq`)。
3. **production 禁 `ports`、禁 `network_mode: host`** — HTTP service 用 `expose` + Coolify 反代對外(dev 變體可對 postgres 開 `ports`)。
4. **`environment` 一律 map 語法**(`key: value`),禁 `- KEY=value` list;同一 service 不混用。值含 `:`/`#`/空白用雙引號。
5. **`command` 禁用 `${Variables}`** — 變數只放 `environment`。
6. **named volume**:持久資料用 `${COMPOSE_PROJECT_NAME}-*` 命名,禁 host bind mount 存 DB。
7. **機密走 Coolify runtime env**,禁寫死在 compose;用 `${VAR}`,**禁** `${VAR:?}` 語法。
8. **healthcheck 用可用的 native probe**:HTTP→`curl`/`wget`、Postgres→`pg_isready`、Redis→`redis-cli ping`。依賴**有 healthcheck** 的 service 才用 `condition: service_healthy`。**Seq 一律不設 healthcheck**(image 不一定有 curl/wget,易卡死)→ 依賴 Seq 只能 plain `depends_on` 或 `service_started`,且 log ingestion 不得阻斷主服務啟動。
9. **鎖版,絕對禁 `latest`**:image tag 走 **per-service 變數**(`postgres:${POSTGRES_VERSION}`、`redis:${REDIS_VERSION}`、`datalust/seq:${SEQ_VERSION}`,**不是**單一 `${SERVICE_VERSION}`);第三方固定版工具(`adminer:4.8.1-standalone`)允許 pinned literal;Dockerfile base image 鎖 patch literal 或 build ARG。
10. **每個 service 設 `TZ: Asia/Taipei`**;Dockerfile 也裝 `tzdata` 並設 `TZ`。
11. **DATABASE_URL 是拓撲決策、非預設值**:compose 內建 postgres → inline `postgresql+asyncpg://${POSTGRES_USER}:${POSTGRES_PASSWORD}@postgres:5432/${POSTGRES_DB}`;外部 / RDS / managed DB → **必用** `DATABASE_URL: ${DATABASE_URL}` 整串注入。
12. **`NODE_ENV=production` 禁放 compose `environment:`**:Coolify 會把 compose 的 `environment:` **同時**展開成 build-time `--build-arg`,`NODE_ENV=production` 注入 builder stage 會讓 `npm ci` 跳過 devDependencies,導致 `tsc` / `vite` / `next` 等 build 工具找不到(exit 127)。`NODE_ENV=production` 應**只在 Dockerfile runtime stage 用 `ENV` 設定**(見 `references/dockerfile-frontend.md` + `references/env-management.md`)。
13. **Multi-service compose 禁同 `mount_path` 的 bind mount**:Coolify `file_storages` 表對 `(application_id, mount_path)` 有 unique-like 限制——同 app 內若兩 service 想各自 bind mount 到同一容器路徑,**後加的會被靜默 drop**。改 **build-time `COPY` 進該 service 自家的 Dockerfile** 繞過(見 `references/compose.md` Quirk 3,DB init scripts 應用見 coolify-db skill)。
14. **production 禁 `ports`,本機 dev 走 override**:本機若需要對 DB 開 port 給 IDE / 工具用,寫進 `docker-compose.override.yml`(與 `docker-compose.yml` 同目錄,**Coolify 預設不讀 override**——這是設計上的安全分離)。production 主檔保持 `expose` only,override.yml 只在 `docker compose up` 在本機跑時自動疊加。

## 兩種對外曝露機制(可並存)

| 機制 | 用途 | 怎麼寫 | reference |
|------|------|--------|-----------|
| **SERVICE_URL magic env** | 讓 Coolify 自動分配對外 URL 注入 container | `SERVICE_URL_<SVC>_<PORT>:`(冒號後留空) | `service-url.md` |
| **custom domain** | 綁自己的網域(`api.<company>.com`)+ 自動 TLS | Coolify Domains 面板 + DNS | `domains-and-tls.md` |

兩者正交:需要自訂網域用 custom domain;只要自動網址用 magic env;可同時使用。

> **magic env 例外 — Seq 密碼不走 magic env**:Coolify 只展開 `SERVICE_URL_*` / `SERVICE_FQDN_*` / `SERVICE_PASSWORD_*` / `COMPOSE_PROJECT_NAME`。`SEQ_FIRSTRUN_ADMINPASSWORD` 不在其列,詳見 `references/seq.md`。

## When to read which reference

| 任務 | 讀這份 |
|------|--------|
| 寫 / 改 `docker-compose.yml`(含最小範本、dev 變體、`expose` vs `ports`、override.yml、檔案儲存規範) | `references/compose.md` |
| 要 Coolify 自動分配的對外 URL(SERVICE_URL / SERVICE_FQDN) | `references/service-url.md` |
| 加 Seq、CLEF logging、Lint 一致性檢查 | `references/seq.md` |
| 寫 backend Dockerfile(FastAPI + uv 範例棧) | `references/dockerfile-backend.md` |
| 寫 frontend Dockerfile(Vite / Next 範例棧、`NODE_ENV` builder 覆寫) | `references/dockerfile-frontend.md` |
| Coolify env 注入、build/runtime、機密、APP_ENV 分層、fail-fast、部署前 checklist | `references/env-management.md` |
| 分支策略 / webhook / migration 時機 / 部署後驗證 / 兩條回滾路徑 / Scheduled Task / ENTRYPOINT vs command 互咬 / bootstrap | `references/deploy-and-rollback.md` |
| Log / Metric / Sentry / healthcheck 端點 | `references/observability.md` |
| 自訂網域 / TLS / HSTS / CORS / cookie domain / preview env | `references/domains-and-tls.md` |
| DB 角色隔離 / one-shot migration / Adminer / 唯讀存取 / 跨版本搬遷 | → **coolify-db** skill |
| build log 解讀、CLI 指令、token 紀律 | → **coolify-logs** skill |
| 第一次裝 CLI / 申請 token / 寫 permission 範本 | → **coolify-setup** skill |

## Bootstrap checklist(首次 push 到 Coolify 前)

scaffold 工具通常不產部署檔;未補完就 push 會在 build 階段失敗。第一次 push 前必補(細節見 `deploy-and-rollback.md`):

- [ ] `docker-compose.yml`(見 `compose.md`)
- [ ] `backend/Dockerfile`、`frontend/Dockerfile`(見 `dockerfile-*.md`)
- [ ] Coolify 端 env 注入(`DATABASE_URL` 或 `POSTGRES_*`、`JWT_SECRET_KEY`、`CORS_ORIGINS`… 見 `env-management.md`)
- [ ] CLI + 至少一把 read token + Claude permission 範本(走 **coolify-setup** skill)
- [ ] 部署失敗時讀 build log 的能力(走 **coolify-logs** skill)

驗收 3 步:

1. `docker compose -f docker-compose.yml config` 通過(yaml 合法、所有 service / image / env 解析得到)。
2. 連上 Coolify 後 `curl <coolify-host>/api/v1/health` 回 200。
3. Coolify 首次 deploy 顯示 healthcheck pass、流量切換成功。
