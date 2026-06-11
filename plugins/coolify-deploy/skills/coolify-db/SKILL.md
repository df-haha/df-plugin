---
name: coolify-db
description: Use when setting up a Coolify-deployed PostgreSQL with role isolation (writer/reader), running one-shot migrations as a compose service, exposing DB via Adminer, granting Claude/AI read-only access (dbhub MCP or /admin/query endpoint), or migrating data across major Postgres versions. Triggers on phrases like "DB 角色"、"writer/reader 分權"、"一次性 migration"、"migration container"、"Adminer 進不去"、"想讓 Claude 查 DB"、"唯讀存取"、"dbhub"、"/admin/query"、"PG18 換 PG16"、"跨版本搬遷"、"DB volume 要重建"。
---

# Coolify DB

## Overview

PostgreSQL 在 Coolify 上的設置紀律：**角色隔離 + 顯式初始化 + one-shot migration + 受控唯讀存取**。本 skill 把 DB 該怎麼初始化、怎麼跑 migration、怎麼開 Adminer、怎麼給 AI / Claude 安全唯讀 channel、跨版本怎麼搬，整成一條鏈。

> **與姊妹 skills 的分工**：本 skill 只管 DB。compose 規則（named volume、healthcheck、`expose` 不開 `ports`、`(application_id, mount_path)` unique-like 限制）的權威在 **coolify-deploy** skill `references/compose.md`；本 skill 引用、不重寫。讀 CLI / build log 走 **coolify-logs**；CLI 安裝與 token 走 **coolify-setup**。

## DB 為何特別容易踩雷

Coolify 在 compose DB 路徑上有三個非顯而易見行為，照普通 PostgreSQL 直覺寫就會中：

1. **DB service 預設不對外開 port**（IT 硬約束 + 安全慣例）→ 外部工具不能直連 `pg_restore`，跨環境搬資料必走 compose 內 migration container。
2. **storage 表的 `(application_id, mount_path)` unique-like 限制** → multi-postgres compose 同時想 mount `/docker-entrypoint-initdb.d` 時後者會被靜默 drop（symptom：init scripts 沒跑、角色沒建、`password authentication failed`）→ 改用 build-time `COPY` 進 service 自家 Dockerfile（見 `references/db-roles-and-init.md`）。
3. **Coolify 的 success 綠燈不等於 init script 跑成功** → healthcheck pass 只代表 PG 接受 connection，roles 沒建、grants 沒設、application 仍會死在 connect。驗收必查實際的 role 登入與 schema 內容，不只看 deploy success。

## 讀哪份 reference

| 場景 | 讀這份 |
|------|--------|
| 第一次寫 DB service：怎麼分 writer/reader 角色、init scripts 的 01/02/03 三段式、build-time `COPY` 繞 mount_path 限制、`docker-compose.override.yml` 開本機 port、volume key v2/v3 suffix 重跑 initdb 的時機與紅線 | `references/db-roles-and-init.md` |
| 要跑 schema migration（CREATE TABLE / ALTER）或 data backfill 但又不想手動 SSH：one-shot service（`restart: "no"` + `depends_on healthy`）+ 「migration 必先於依賴它的 code」紀律 + 用後清理 | `references/one-shot-migration.md` |
| 要開 Adminer 管 UI、登入失敗 debug、Adminer 在無 terminal 環境的工具用法 | `references/adminer.md` |
| 要給 Claude / AI / 內部工具讀 DB 不寫：兩方案決策表（平台級 dbhub MCP vs 應用級 `/admin/query`）+ 為何防線必須在 DB 層 + 對 PG `SELECT 可呼叫 side-effect function` 的真正攔截 | `references/readonly-access.md` |
| 跨版本搬資料（PG18 → PG16 text dump + grep）、舊 PaaS → Coolify 內網、PG18 image 在 Coolify 跑不起來、Volume rename pattern 重跑 initdb | `references/db-migration.md` |

## 三條紅線（永遠遵守）

1. **production DB 改 schema 一律走 migration**：禁直接 `psql` 進 production 改欄位，禁用 volume rename「修」schema（rename = 全新空 volume，舊資料看似消失）。volume suffix bump 僅限**首次建置或可拋棄環境**。
2. **production migration 一律 backward-compatible expand-only；destructive 變更走 expand-then-contract 拆兩次 deploy**：Coolify 預設 rolling deploy —— 舊 app container 在新 app 健康前仍在服務流量。若把 `DROP COLUMN` / `RENAME` / 型別破壞性變更跟新 code 塞同一個 PR，migrate service 跑完瞬間舊 app 即噴 `column does not exist` / type-cast error / lock contention，整個服務洞開。**禁** 把 destructive DDL 跟新 code 同 commit。正解：
    - **PR A（expand）**：加新欄 / 雙寫 / code 讀兩邊 → deploy → 跑穩（觀察一段時間 + 驗 backfill 完成）
    - **PR B（contract）**：code 只讀新欄 → deploy → 跑穩 → migrate DROP 舊欄

   `depends_on service_completed_successfully` 只保證 migrate **成功** app 才啟動 —— 不保證 migrate 跑的 DDL 對舊 app 是安全的；沒 expand-then-contract 紀律，即使依賴關係寫對 production 仍會炸。範例 yaml 與依賴關係細節見 `references/one-shot-migration.md`。
3. **AI / Claude 讀 DB 走唯讀 channel**：禁把 writer 帳號的密碼或 sensitive token 給 AI（DB 寫權即整個業務狀態的後門）。走 reader role + 連線 / endpoint 層強制 `read only` —— regex 過濾只是 UX，真正的防線在 DB（見 `references/readonly-access.md`）。

## 驗收 checklist（第一次部署 DB）

- [ ] `docker compose -f docker-compose.yml config` 解析得到，沒有變數 unset
- [ ] DB healthcheck 用 `pg_isready -U ${POSTGRES_USER} -d ${POSTGRES_DB}` —— 驗實際密碼登入，不只 socket open（image 本身的 superuser 一直能 connect，不代表業務 role 建成功）
- [ ] 部署後在 Coolify 跑 `coolify database get <db-uuid>` 確認 `status=running`，但 **接著用 Adminer 或一次性 `psql` 容器**以 writer role 登入跑 `SELECT current_user, current_schema()` —— 真正的驗收 gate
- [ ] reader role 跑 `INSERT` 應 fail（顯示 `permission denied`），跑 `SELECT` 應成功
- [ ] init scripts 內每個 `:?` 必填 env 已在 Coolify env 面板填好（POSTGRES_PASSWORD / WRITER_PASSWORD / READER_PASSWORD）
