# cli-and-verification.md — Coolify CLI 速查 + 部署後驗證

> **何時讀**：想用 `coolify` CLI 查狀態、看 deployment log、驗證一次性 migration service 是否真的 exit 0、或跑 pos-data-api 部署後 smoke test 時。

---

## ⚠️ 最重要 Gotcha — 先看這條

> **Coolify Application Status "Success" / "running:healthy" 只代表主 app container 通過 healthcheck。**
> **`restart: "no"` 一次性 service（migration、seed）的 exit code 不影響這個綠燈。**
> **必須另外驗證一次性 service。**

---

## CLI 子命令速查

```
coolify [GLOBAL_FLAGS] <SUBCOMMAND> [ARGS]

Global flags:
  --context <name>      切換 config context（對應 ~/.config/coolify/config.json）
  --debug               詳細輸出
  --format table|json|pretty   輸出格式（預設 table）
  -s / --show-sensitive 顯示敏感欄位
  --token <TOKEN>       覆蓋 config 中的 API token

子命令（1.6.2 已驗）：
  app         應用程式操作（list / logs / deployments）
  service     服務操作
  deploy      部署操作（list / get / cancel / name / uuid）
  database    DB 操作
  project     專案管理
  server      伺服器管理
  resource    資源管理
  teams       團隊管理
  context     管理 CLI context（~/.config/coolify/config.json）
  private-key SSH 私鑰管理
  github      GitHub App 設定
  config      顯示目前 config
  update      更新 CLI
  version     顯示版本
```

### 最常用指令

```bash
# 列所有 application（含 UUID）
coolify app list --format json

# 某 app 的 runtime log（需 container 在跑；非 build log）
coolify app logs <app-uuid>

# 某 app 的部署清單
coolify app deployments <app-uuid> --format json

# 取單筆部署詳細（含 build log，需 read:sensitive token）
coolify deploy get <deployment-uuid> --format json

# 列近期部署
coolify deploy list --format json
```

> **`coolify app logs` 讀的是 runtime stdout，不是 build log。**
> Build log（含一次性 service stdout）需要 `read:sensitive` token，詳見 `deployment-logs.md`。
> 若一次性 service 已 exited，runtime log 端點可能回傳空或 "Application is not running"。

---

## 大豐環保 Coolify 專案 UUID 速查

| Application Name | UUID | Branch |
|-----------------|------|--------|
| **pos-data-api** | `d4rzk8hvnc6m0n03m8mvi560` | main |
| ai-battle 正式區 | `a5psibofmob8d2wzlc6gy4x1` | release |
| ai-battle 測試區 | `d4pjn0ucq91vvkqwo6v3pb95` | main |
| backend (onsite-test) | `od1d73rbteaear3h7vaepe9o` | feat/usb4750-onsite-test |
| dafon-product-showcase | `h11f3l7r24o1zqsbgc0b4nue` | design/v2-from-pre-atlas |
| df-om-thermos-data-backups | `mjcvzlc37c50f6ncbnugfngm` | main |
| recycle-station-crm prod | `c7fr9ofdj2cih5bdftyzjgl2` | main |
| recycle-station-crm test | `arvc0xxegjep7gloo3rwf2ut` | staging |

> 上表是快速速查；要抓最新清單跑 `coolify app list --format json`。

---

## 一次性 service（`restart: "no"`）驗證流程

一次性 service 典型寫法：

```yaml
backup-migrate-utm:
  build: { context: ., dockerfile: migrations/Dockerfile.utm }
  restart: "no"
  depends_on:
    backup-db:
      condition: service_healthy
```

### 驗證選項（依可靠度排序）

#### 選項 1（最可靠）— 端點 smoke test 反證

如果 migration 目標是建立資料表，打 API 看表是否存在比讀 log 更可靠——表不存在通常會讓端點回 500/503。

```bash
# pos-data-api 的 QA 儀器原始值端點
# 若 qa_utm_flexural / qa_utm_moisture 表不存在，這個端點會 500/503
curl -s -o /dev/null -w "%{http_code}" \
  -H "Authorization: Bearer <API_KEY>" \
  "https://pos-data-api.<domain>/qa-quality-inspections/items?date_from=2026-06-01&date_to=2026-06-09"

# 期望回傳 200（資料可能為空但端點健康）
```

#### 選項 2 — `coolify deploy get` 看 build log

```bash
# 1. 找最新部署 UUID
coolify app deployments d4rzk8hvnc6m0n03m8mvi560 --format json | \
  python3 -c "import json,sys; ds=json.load(sys.stdin); print(ds[0]['uuid'])"

# 2. 取 build log（需 read:sensitive token，見 deployment-logs.md）
export COOLIFY_LOG_TOKEN='<read:sensitive token>'
python scripts/coolify-logs.py --deployment <deployment-uuid>

# 在 log 裡找一次性 service 的 exit marker，例如：
# === backup-migrate-utm done (exit 0) ===
```

> 若 build log 為空（`No logs available`），代表 token 沒有 `read:sensitive` 權限。
> 見 `deployment-logs.md` 的 two-token 模型。

#### 選項 3 — `coolify app logs` 看 runtime log（未完整實測）

```bash
coolify app logs d4rzk8hvnc6m0n03m8mvi560
```

> `coolify app logs` 打的是 runtime container log 端點。  
> Docker Compose 聚合 log 有時仍保留已 exited service 的 stdout，但行為未實測確認。  
> 若回傳空或 "Application is not running"，改用選項 1（smoke test）或選項 2（build log）。

---

## Case Study — PR #2 部署（2026-06-09，merge commit `14498a0`）

**背景**：pos-data-api 加了 `backup-migrate-utm` 一次性 service，套 2 張 QA 儀器表（`qa_utm_flexural`、`qa_utm_moisture`）。

**觸發流程**：
1. PR #2 merge → main push → Coolify webhook → docker compose build + up
2. `backup-migrate-utm` 用 `restart: "no"` 跑完後自動 exit
3. 主 app container healthcheck pass → Coolify 顯示 "Success"

**Gotcha 印證**：Coolify "Success" 亮起不代表 `backup-migrate-utm` 跑成功。必須走上方驗證流程之一。

**驗收標準（任一通過）**：
- [ ] `/qa-quality-inspections/items` 端點回 200
- [ ] build log 含 `=== backup-migrate-utm done (exit 0) ===`（或相應的 exit marker）

---

## 完整部署後驗證 checklist（pos-data-api）

每次 pos-data-api 部署後 30 分鐘內走完：

- [ ] Coolify Application Status = "Success"（主 app healthcheck）
- [ ] 若本次有一次性 migration service → 走上方 § 驗證選項
- [ ] smoke test：`GET /health` → 200
- [ ] 若改了 QA 相關端點：`GET /qa-quality-inspections/items` → 200
- [ ] Coolify app log 30 分鐘內無新 500 error
- [ ] 若改了 auth：打一次 API key 申請或查詢流程確認未斷

---

## 常見雷

| 症狀 | 根因 | 對策 |
|------|------|------|
| `coolify app logs` 回 "Application is not running" | 打的是 runtime log，container 未跑（或一次性 service 已 exit） | 改看 build log（`coolify deploy get`）或 smoke test |
| `coolify deploy get` 的 JSON 沒有 `logs` 欄位 | Token 缺 `read:sensitive` 權限 | 換 read:sensitive token，見 `deployment-logs.md` |
| Coolify Status "Success" 但功能異常 | 一次性 service 靜默失敗（exit 非 0 但 Coolify 不管） | 查 build log 或 smoke test 反證 |
| Migration 套了但資料表不存在 | `restart: "no"` service 可能啟動失敗（DB 未 ready）或 SQL error | 看 build log 確認 exit code；確認 `depends_on` + `condition: service_healthy` |
