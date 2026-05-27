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
