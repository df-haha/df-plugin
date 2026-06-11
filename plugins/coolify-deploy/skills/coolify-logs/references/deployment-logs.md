# deployment-logs.md — 安全讀取 Coolify 部署 build log

> **何時讀**：部署失敗要看 build log、設定「讀 log 用」的 API token、或做「部署失敗自動撈 log」的 monitor 時。

部署失敗時，build log 是定位根因的關鍵。但 Coolify 的 build log 取得方式有個陷阱：**一般 API token 讀不到它**，而能讀的 token 又權限過大。本檔說明為什麼、以及如何在「讀得到 log」與「不外洩機密」之間取得平衡。

---

## 為什麼 CLI / API 預設讀不到 build log

Coolify 把 deployment build log 存在 PostgreSQL 的 `application_deployment_queues.logs` 欄位。API `GET /api/v1/deployments/{uuid}` 的 schema 雖然有 `logs` 欄位，但 **controller 在 token 沒有 `read:sensitive` 權限時會 `makeHidden(['logs'])`** 把它整個拿掉。

實際症狀（會浪費你很多時間，先記住）：

- `coolify app deployments logs <app> <dep>` → `No logs available`（即使部署進行中即時 `--follow` 也是 0 bytes）
- `coolify deploy get <dep> --format json` → 回傳沒有 `logs` 欄位
- `coolify app logs <app>` → `Application is not running`（這是 **runtime** log 端點，不是 build log，且要 container 在跑）
- `coolify server` → **沒有 exec 子指令**，無法叫 server 跑 `docker logs`

→ 結論：**要讀 build log 一定要 `read:sensitive` token**。Web UI 看得到是因為它用你登入的 session（完整權限）直讀 DB。

> 早期失敗（如 30 秒內 failed、log 0 bytes）若連 `read:sensitive` 都撈不到 `logs`，才是「部署 job 在寫 log 前就死」（資源不足 / queue 異常），那時要看 Coolify 自身的 `docker logs coolify` 與 host 資源，見 `observability.md`。

---

## Token 權限模型（Coolify 官方）

五種權限：`read` / `read:sensitive` / `write` / `deploy` / `root`。重點：

- **`read:sensitive` 本身已包含 `read`**（官方定義：read 加上 passwords、private keys、environment variables、logs）→ 建這把 token **單勾 `read:sensitive` 即可，不要再加 `read`**。
- Token 是 **team-scoped**：只能存取「建立時所在 team」的資源，但該 team 內**所有專案**都讀得到。
- 支援 **token 過期**（7/30/60/90 天、1 年、或無期限）與 **API Allowed IPs**（Settings → Advanced，全域 allowlist，非 per-token）。

### 建議：兩把分離的 token（least-privilege）

| Token | 權限 | 用途 |
|-------|------|------|
| 日常 | `read` + `deploy` | 觸發部署、monitor 看狀態（`status`）。**不含機密讀取** |
| 讀 log | `read:sensitive`（單勾） | 只在部署失敗要撈 log 時用 |

常用路徑（部署 + 看狀態）用低權限的那把；只有撈 log 才動到 `read:sensitive`。

---

## ⚠️ `read:sensitive` 是高風險憑證——護欄不可省

這把 token 能讀**整個 team 所有專案**的 env 明文值（DB 密碼、JWT secret、第三方 API key）**以及 Coolify 管理的 SSH 私鑰明文**（`/security/keys` 的 `private_key`）。等於「該 team 的機密保險箱 + 主機 SSH 入口」。沒有 `write` 不代表低風險——讀到 DB 密碼與 SSH 私鑰後，攻擊者可繞過 Coolify 直接連 DB、SSH 進主機、偽造 JWT。

建立這把 token 時**必須**同時做：

1. **API Allowed IPs**（最強的一條）：只允許跑 monitor 那台機器 / VPN 的 IP。就算 token 外洩，別的來源也用不了。
2. **設過期**：選有限期（如 90 天），別選「無期限」，並排輪替提醒。
3. **存放**：放環境變數或 `.env`，該檔 **gitignore + `chmod 600`**，token **絕不 echo、絕不貼進對話 / transcript / commit**。
4. **（建議）拆 team**：把 production 專案放獨立 team，這把 token 的 blast radius 就只剩該專案，不會橫掃其他專案。
5. **用完即撤**：若只是一次性 debug，建短效 token、查完立刻在 Coolify → Security → API Tokens 刪除。

> token 一旦進過任何明文檔 / 對話 / log，視為已洩漏 → 撤銷重建，別只是刪檔。

---

## 怎麼安全地讀 log：`scripts/coolify-logs.py`

直接吐原始 `logs` 很危險——裡面 Coolify 標 `hidden=true` 的 entry 含 `--build-arg`、`build-time.env` 等機密值。本 skill 附的腳本預設**丟掉所有 `hidden=true` entry**（等同 Web UI 正常檢視看到的安全版），再對殘留行跑一層 pattern 遮罩（連線字串、Bearer、PEM 私鑰、`*SECRET/PASSWORD/TOKEN/KEY*=值`、長高熵字串），偏保守寧可多遮。

```bash
export COOLIFY_URL='https://coolify.example.com'
export COOLIFY_LOG_TOKEN='<read:sensitive token>'   # 從安全來源讀，勿寫死

# 抓某 app 最新一筆部署的 log（最常用）
python scripts/coolify-logs.py --app <app-uuid>

# 指定某次部署
python scripts/coolify-logs.py --deployment <deployment-uuid>
```

輸出可安全貼給 AI / 寫進 transcript。腳本只用 Python 標準庫，無需 pip。

- `--include-hidden`：**危險**，連 hidden（含密鑰）也輸出，會 stderr 警告——勿貼給 AI。
- `--no-redact`：關 pattern 遮罩（仍丟 hidden，除非也下 `--include-hidden`）。

---

## 「部署失敗自動撈 log」monitor 流程

把上面組起來，monitor 的邏輯是：

```
日常 token (read+deploy) 觸發/輪詢部署
        │
        ▼  coolify deploy get <uuid> → status
   status == failed ?
        │ 是
        ▼  改用 read:sensitive token
   python scripts/coolify-logs.py --deployment <uuid>   # drop hidden + 遮罩
        ▼
   輸出乾淨 log → 交給人 / AI 定位根因
```

要點：

- **偵測失敗只需 `read`**（看 `status`），不必動 `read:sensitive`。只有「撈 log」那一步才升權。
- log 一律經 `coolify-logs.py` 清洗後才外流，不要把原始 API `logs` 直接餵出去。

---

## 已知雷

- **正常 log 安全，「Show Debug Logs」不安全**：Web UI 正常檢視（與本腳本 drop-hidden 的輸出）不含機密值；但 Coolify 的「Show Debug Logs」/ 本腳本的 `--include-hidden` 會露出 `.env` / build-time env 明文（參考 Coolify issue #7235）。debug 模式只在本機自己看，別外流。
- **Dockerfile 別在 build 時 `echo` 機密**：drop-hidden 只能丟 Coolify 自己標記的行；若你的 Dockerfile / build script 自己 `echo $SECRET`，那行不是 hidden，會殘留（pattern 遮罩是第二道防線但不保證全中）。build 階段不要印機密。
- **`NODE_ENV=production` build-arg → build 失敗**：這是另一個常見「部署失敗」根因（`npm ci` 跳過 devDependencies → `tsc`/`vite` not found → exit 127）。屬 build 規則，見 SKILL.md Compose 核心規則第 13 條與 `dockerfile-frontend.md`，不在本檔重複。

---

## 相關 API 端點（用 `read:sensitive` token）

| 端點 | 用途 |
|------|------|
| `GET /api/v1/deployments/{uuid}` | 單筆部署（含 `logs`，需 read:sensitive） |
| `GET /api/v1/deployments/applications/{app-uuid}?skip=0&take=N` | 某 app 的部署清單 |
| `GET /api/v1/applications/{uuid}/logs` | **runtime** container log（非 build log，需 container 在跑） |
