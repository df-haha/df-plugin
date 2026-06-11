# deploy-and-rollback.md — 部署流程 + 回滾

> **何時讀**：設定 Coolify webhook、改部署觸發策略、跑 migration、或部署出問題要回滾時。

`git push` → GitHub webhook → Coolify build → deploy 的端到端流程，加上兩條回滾路徑。

---

## Bootstrap（首次 push 到 Coolify 前必補）

很多 scaffold / init 工具**不會**自動產出部署檔。scaffold → push 到 GitHub → Coolify 拉 → 若以下檔未補，**deploy 直接失敗**（找不到 build context）。

採用方在第一次 push 到 Coolify 前必須手動補上：

| 檔案 | 對應 reference | 必要性 |
|------|---------------|--------|
| `docker-compose.yml` | `compose.md` | 必要 — Coolify 讀此檔判定 services |
| `backend/Dockerfile` | `dockerfile-backend.md` | 必要 — backend build 來源 |
| `frontend/Dockerfile` | `dockerfile-frontend.md` | 必要 — frontend build 來源 |
| Coolify 端 env 注入 | `env-management.md` | 必要 — `DATABASE_URL` / `JWT_SECRET_KEY` / `CORS_ORIGINS` 等 |

未補完就 push，Coolify 會在 build 階段 ERR（找不到 Dockerfile）或 healthcheck timeout（service 沒起來）。

驗收 3 步：
1. `docker compose -f docker-compose.yml config` 通過（yaml 合法、所有 service / image / env 解析得到）。
2. 連上 Coolify 後，`curl <coolify-host>/api/v1/health` 回 200。
3. Coolify 後台首次 deploy 顯示 healthcheck pass、流量切換成功。

---

## 分支策略

| Branch | 環境 | 部署觸發 |
|--------|------|---------|
| `main` | **production** | push to `main` → Coolify production app 自動部署 |
| `staging` | **staging** | push to `staging` → Coolify staging app 自動部署 |
| feature 分支 | development / preview（可選） | 不自動部署；走 PR + 本地 development |

- **禁**從 feature 分支直接部署 production（必走 PR → main）。
- **禁**讓 staging 與 production 共用同一 Coolify app（必獨立 application，各自 env / DB）。
- **部署不靠 CI 推 image**：CI（GitHub Actions）只負責 merge 前綠燈；Coolify 自行拉 git + build。

## End-to-end flow

```
1. developer 在 feature 分支寫程式 + 跑 dev server（localhost）
       ↓
2. 開 PR 進 main / staging
       ↓
3. CI（GitHub Actions）綠燈（merge 前必綠）
       ↓
4. reviewer approve + merge（squash）
       ↓
5. GitHub webhook → Coolify
       ↓
6. Coolify pull git + docker build（用 repo Dockerfile）
       ↓
7. 新 container 啟動 → healthcheck 等待 healthy
       ↓
8. healthy → Coolify 切流量（零停機 rolling）
       ↓
9. 不健康 → Coolify 自動回滾（見下方回滾段）
```

## Scheduled Task / 排程備份 —— 必用「長駐 idle container + docker exec」

Coolify v4 的 Scheduled Task 設計上**只能 `docker exec` 進已運作中的容器**跑指令，**不支援 `docker compose run` / `docker run` 建臨時容器**（這個限制目前沒有 UI 揭露，安排上去會看似成功但實際無事發生，或在 docker exec 階段噴 `Container is not running`）。

對「每天一次跑 backup-to-r2.sh / sync-from-r2.sh / cron-like 任務」這類需求，**正解**是：在 compose 加一個**長駐 idle container** 專門讓 Scheduled Task 進去 exec：

```yaml
services:
  backup:
    build:
      context: ./backup
      dockerfile: Dockerfile
    environment:
      TZ: Asia/Taipei
      # 連 DB 用 compose 內網 service 名
      DB_HOST: postgres
      DB_USER: ${POSTGRES_USER}
      DB_PASSWORD: ${POSTGRES_PASSWORD}
      DB_NAME: ${POSTGRES_DB}
      # 對外服務憑證走 runtime env
      R2_ACCESS_KEY_ID: ${R2_ACCESS_KEY_ID}
      R2_SECRET_ACCESS_KEY: ${R2_SECRET_ACCESS_KEY}
    depends_on:
      postgres:
        condition: service_healthy
    # 關鍵：常駐睡眠，等 Scheduled Task 進來 exec
    command: ["sleep", "infinity"]
    restart: unless-stopped
```

Coolify Application → Scheduled Tasks → Add Task：
- Frequency：cron expression（例 `0 3 * * *` 每天 03:00）
- Command：`/app/backup-to-r2.sh`（image 內絕對路徑；script 自己處理錯誤碼與 log）
- Container：選 `backup` service

**禁** 把備份腳本塞進 backend service 跑 cron —— 違反「一 container 一進程」、混在一起難 debug、且 backend 重啟就漏跑。**禁** 用 `restart: "no"` + entrypoint 跑一次 —— 那是 one-shot migration 的形狀（見 coolify-db skill），不是 cron。

---

## Dockerfile ENTRYPOINT 與 compose `command:` 互咬

ENTRYPOINT + command 的組合在 Docker 是「ENTRYPOINT 是不可變的前綴，CMD/command 變成它的參數」。寫 sync / backup script 時最常見的踩雷：

```dockerfile
# backup/Dockerfile
ENTRYPOINT ["/app/sync.sh"]   # 寫死執行 sync.sh
```

```yaml
# docker-compose.yml
backup:
  build: ./backup
  command: ["sleep", "infinity"]   # 想用 idle 模式
```

實際執行的是 `/app/sync.sh sleep infinity` —— sync.sh 不認識這兩個 argv，要嘛無視、要嘛吃錯參數崩潰、要嘛卡在 sync.sh 自己的等待邏輯不會 idle。

**規則**：

- 想讓 compose `command:` **完全覆寫**啟動指令 → Dockerfile 用 `CMD` 不要用 `ENTRYPOINT`，或顯式 `ENTRYPOINT []` 清空。
- 想讓 ENTRYPOINT 固定但 command 帶可變參數（典型：migration script 接環境名）→ ENTRYPOINT 寫成接受 argv 的 dispatcher：
  ```dockerfile
  ENTRYPOINT ["/app/entrypoint.sh"]
  ```
  ```bash
  # entrypoint.sh
  #!/bin/sh
  case "$1" in
    sleep) exec sleep infinity ;;
    sync)  exec /app/sync.sh ;;
    *)     exec "$@" ;;        # fallback 透傳
  esac
  ```
- one-shot migration container：用 `command:` 寫腳本即可，**禁** Dockerfile 另設 ENTRYPOINT 蓋過。

`docker compose config` 印不出來這個問題（YAML 合法），會在 container 啟動才炸 —— Coolify deployment log 只看到 `Container is unhealthy`，要靠 runtime log 才找得到根因。所以 **寫了 ENTRYPOINT 的 image，compose 端任何 `command:` 都要實際 `docker compose up` 跑一遍驗**，不要相信只跑 config。

---

## Migration 時機

DB migration（如 Alembic）**必**在容器**啟動**時跑，**禁**手動在跳板機跑 `alembic upgrade head`（失去 audit trail、易與部署不同步）：

```Dockerfile
# Dockerfile（backend）— 不在 build 時跑，CMD 改成 entrypoint script
CMD ["./entrypoint.sh"]
```

```bash
# entrypoint.sh
#!/bin/sh
set -e
uv run alembic upgrade head      # migration
exec uvicorn app.main:app --host 0.0.0.0 --port 8000
```

注意：

- 容器**啟動**時跑（不是 build）；多 replica 時各自跑會競爭 → 用 migration 工具內建 lock 解決，但**禁**長時 migration（一個 migration 一件事）。
- 大遷移（例 backfill 1M 列）拆 task 走 zero-downtime 流程（雙寫 → migrate → 下版移除舊欄），不要在啟動時做。

## Webhook 設定

Coolify Application → Settings → Webhook URL：

```
https://coolify.<company>.internal/webhooks/<app-id>
```

GitHub Repo → Settings → Webhooks → Add webhook：
- Payload URL：Coolify webhook URL
- Content type：application/json
- Secret：Coolify 提供
- Events：Just the push event
- Active

## 部署視窗

production 部署視窗：

- ✅ 正常時段 09:00–17:00（可即時應變）
- ❌ 非工作時段、週末、長假前一天（沒人 standby）
- ❌ 公告凍結期

緊急 hotfix 例外，**必**通知 oncall + 寫修正紀錄。

## 部署後驗證

每次部署後 30 分鐘內走：

- [ ] 主要 user journey 手測通過（登入 / 主要 CRUD）
- [ ] error log 無新增 5xx（見 `observability.md`）
- [ ] healthcheck 持續 healthy
- [ ] 第三方 callback（若有，例 SSO / payment）無中斷

任一失敗 → 走下方回滾。

---

## 回滾判準（任一觸發）

- healthcheck 連 ≥ 3 次失敗（Coolify 自動回滾）
- 部署後 30 分鐘內 5xx 比例 > 5%
- 主要 user journey 手測無法完成
- 客戶回報關鍵功能掛（支援 / 業務告知）

## 路徑 A：Coolify 內建 rollback（快，首選）

Coolify Application → Deployments 頁 → 選上一版 deployment → **Redeploy this version**。

- 適用：image 還在（Coolify 預設保留前 N 版）、且問題不在 DB schema。
- 時間：< 2 分鐘（同新部署，但跑舊 image）。
- **不**會回滾 DB migration（若新版有 migration，DB 仍是新 schema）。

## 路徑 B：git revert + push（主流程）

```bash
git checkout main
git pull origin main
git revert <bad-commit-hash>          # 產生反向 commit
git push origin main
```

→ 觸發 Coolify webhook → 跑 CI → build → deploy。

- 適用：任何問題，**首選**（歷史可追、可走 CI 二次驗證）。
- 時間：5–10 分鐘（等 CI + build）。

## DB schema 已往前的情境

新版若已跑 migration → app 回退 ≠ DB 回退。原則 **forward fix > 反向 migration**：

1. **首選**：寫修復 commit + push，讓 app 補相容（例：舊 code 也能讀新欄位）。
2. 不可逆 / 沒辦法兼容 → 寫反向 migration + 評估資料風險（可能丟新欄位資料）。
3. 大量資料 backfill 已執行 → 不能 revert，只能 forward fix。

## 回滾後 follow-up

回滾完成 ≤ 24 hr 內：寫修正紀錄（根因 / 影響範圍 / 修正計畫）；開重新部署 task；若涉資料異動通報 stakeholder。

## 不要做

- ❌ `git push --force` / `git reset --hard <old> && git push --force`（改寫歷史，team 災難；禁強推規則見使用者全域 `~/.claude/rules/git-workflow.md`）
- ❌ 直接在跳板機改 image（失去 audit trail）
- ❌ 關 Coolify 自動 rollback（失去 healthcheck guard）
