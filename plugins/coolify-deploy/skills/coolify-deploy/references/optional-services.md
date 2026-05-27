# optional-services.md — Adminer + Seq（選配服務）

> **何時讀**：要在 compose 加入 Adminer（DB 管理 UI）或 Seq（集中式 log）時。

本檔是 **Seq 與 Adminer 的 single source of truth**。observability.md 只**引用**本檔的 Seq 內容，不重寫。

Adminer / Seq 是**選配**服務：不需要就不要生成。生成與否必須與實際需求一致——**未刻意需要卻生成 Adminer/Seq 屬安全/設定事件**（見下方 Lint 矩陣的反向檢查）。

---

## Adminer（DB 管理容器）

輕量 DB 管理 Web UI（單檔 PHP），透過 Coolify 分配的對外網址用瀏覽器連線。

### 環境變數

| 變數 | 必填 | 用途 |
|------|------|------|
| `SERVICE_URL_ADMINER_8080:` | ✅ | Coolify 分配對外網址（冒號後留空） |
| `ADMINER_DEFAULT_SERVER` | 建議 | 登入畫面預設 server hostname，填 compose DB service 名（如 `postgres`） |
| `ADMINER_DEFAULT_DB_DRIVER` | 建議 | `pgsql` / `server`(MySQL/MariaDB) / `sqlite` / `mssql` / `oracle` / `mongo` |
| `ADMINER_DESIGN` | 選配 | UI 主題，如 `pepa-linha` / `hydra` |
| `ADMINER_PLUGINS` | 選配 | 啟用額外 plugins，如 `tables-filter dump-json` |

### 登入欄位對照

| Adminer 欄位 | 填什麼 |
|--------------|--------|
| **System** | PostgreSQL（或對應 DB 類型） |
| **Server** | `postgres`（compose service 名，**不是** localhost） |
| **Username** | DB 使用者（`${POSTGRES_USER}`） |
| **Password** | DB 密碼（`${POSTGRES_PASSWORD}`） |
| **Database** | DB 名（`${POSTGRES_DB}`），可留空登入後再選 |

### 安全要求（⚠️ core 規則，非 trivia）

Adminer 本身**無任何內建存取控制**，知道網址即可嘗試登入；Adminer 登入密碼 = DB 密碼，洩漏即 DB 被拿。

1. **`POSTGRES_PASSWORD` 絕對不能為空**。
2. **正式環境未加 Basic Auth / IP allowlist 就不要生成 adminer**（或平時停用 service）。
3. **Image 綁版**，禁 `adminer:latest` 進正式環境。
4. **SERVICE_URL 不得貼進文件 / README / 聊天工具**——等同洩漏 DB 後門。

### 替代工具

| 工具 | Image | 適用 |
|------|-------|------|
| **Adminer**（預設） | `adminer:4.8.1-standalone` | 輕量、快速查看 |
| pgAdmin | `dpage/pgadmin4` | PostgreSQL 重度、ER 圖 / explain plan |
| DBGate | `dbgate/dbgate` | 多類型 DB、SQL history |
| CloudBeaver | `dbeaver/cloudbeaver` | 企業級多人協作、帳號權限 |

---

## Seq（集中式 log 收集）

集中式 log 查詢工具。推薦**應用程式直接 HTTP 推送 CLEF**（透過語言對應 SDK），不要用 Docker gelf driver + `seq-input-gelf` sidecar 的舊架構。

### 架構

```
應用程式 → SDK（批次 / 重試 / flush）→ POST http://seq/api/events/raw (CLEF) → Seq UI
```

相較舊 gelf 方案：少一個 sidecar、支援結構化欄位（message template + properties）、跨語言一致、無須 publish UDP port。只有在應用是黑箱 image 無法改 code 時才退回舊 gelf。

### first-run 管理員密碼（⚠️ 高優先，易踩雷）

`datalust/seq` 啟動時若未提供 first-run admin password 會 crash（`No default admin password was supplied`）。但**密碼設定方式有陷阱**：

- canonical compose 必寫 `SEQ_FIRSTRUN_ADMINPASSWORD:`（**冒號後留空**）。
- `SEQ_FIRSTRUN_ADMINPASSWORD` **不是** Coolify magic env（Coolify 只展開 `SERVICE_URL_*` / `SERVICE_FQDN_*` / `SERVICE_PASSWORD_*` / `COMPOSE_PROJECT_NAME`）。因此 **禁** 寫 `${SEQ_FIRSTRUN_ADMINPASSWORD}`，也**禁**借用 `$SERVICE_PASSWORD_SEQ`。
- 正確注入方式：**部署者先在 Coolify Application → Environment Variables 面板手動填一個 10–15 字元隨機值**（標 `is_secret`），Coolify 以同名 env var 注入 container；compose 端維持冒號後留空、不做 interpolation。
  - （若是有後端自動化的系統，可由後端於部署時產生隨機值寫入 Coolify Env Vars；非自動化的專案就手動填。）
- 登入：部署後到 Coolify → Environment Variables 查 `SEQ_FIRSTRUN_ADMINPASSWORD` 的值。密碼只在 volume **初次**初始化時寫入，之後改 env 無效（要到 Seq UI 改）。

### 其餘 Seq 規則

1. **不要對 Seq 設 healthcheck** — image 不一定有 curl/wget，易永遠失敗卡死。依賴 Seq 的 service 只能 plain `depends_on` 或 `condition: service_started`，**禁** `service_healthy`。
2. **volume 損壞要砍掉重建** — 多次部署失敗後 `seq-data` 可能殘留半成品，噴 `Failed to initialize storage`。把 compose 內的 seq volume 刪除後 commit 讓 Coolify 處理，再補回 commit 重新部署。
3. **compose 網路內走 `http://seq`（port 80）** — 對應 `expose: "80"` 與 `SERVICE_URL_SEQ_80:`。SDK 自動 append `/api/events/raw`。
4. **`SEQ_INGESTION_URL` 留空就 fallback console** — logger wrapper 要寫成「沒設此 env 就只走 `console.log` / `print`」，這樣 `npm run dev` / CI 單測不會嘗試連線。
5. **log ingestion 不得阻斷主服務啟動** — 主服務不可因 Seq 未起來而卡住（呼應規則 1 的 `service_started`）。

### 應用程式配合方式

| 語言 / 框架 | 套件 | 掛接方式 |
|-------------|------|---------|
| Node.js / Next.js | `seq-logging` | 自訂 logger wrapper |
| .NET / ASP.NET Core | `Serilog.Sinks.Seq` | Serilog sink |
| Java / Spring Boot | `seq-logback-appender` | `logback-spring.xml` |
| Python / FastAPI / Django | `seqlog` | 標準 `logging` |
| Go | `github.com/nullseed/logruseq` | logrus hook |

通用導入 pattern：

1. 裝 SDK 寫 logger wrapper（讀 `SEQ_INGESTION_URL` / `SEQ_API_KEY` / `APP_NAME`）。
2. 沒設 `SEQ_INGESTION_URL` 時 fallback 只印 console。
3. 替換程式內 `console.log` / `print` / `logger.info` → wrapper 呼叫。
4. 用 **message template**（`"user {UserId} logged in"` + `{ UserId: 123 }`），不要字串拼接。
5. 註冊 `SIGTERM` / `SIGINT` / `beforeExit` → 呼叫 SDK `close()` / `flush()`。
6. 統一把 `Error` 物件序列化進 CLEF 的 `exception` 欄位。

---

## Adminer / Seq 一致性檢查（Lint 矩陣）

生成 adminer / seq 時必須帶齊其必填 env。下表是完整 error / warning 矩陣。

> **校驗來源**：在有 Wizard 的系統，雙向校驗以 Wizard `selected_services` 為 source of truth（勾選但沒生成、生成但沒勾選皆 error）。**無 Wizard 的專案**沒有 `selected_services`，規則簡化為：**若生成 adminer/seq 必含其必填 env；非刻意需要不要生成（安全考量）**。

### Error（必擋）

| 情境 | 說明 |
|------|------|
| 生成 adminer 但缺 `SERVICE_URL_ADMINER_8080` | adminer 對外無網址 |
| 生成 seq 但缺 `SERVICE_URL_SEQ_80` | seq 對外無網址 |
| 生成 seq 但缺 `ACCEPT_EULA: "Y"` | Seq 拒絕啟動 |
| 生成 seq 但缺 `SEQ_FIRSTRUN_ADMINPASSWORD`（冒號後留空那行） | Seq crash `No default admin password` |
| （有 Wizard）勾選 adminer/seq 但 compose 無該 service | UI 與實際部署脫鉤 |
| （有 Wizard）**未勾選卻生成** adminer/seq | 反向檢查＝安全事件：可能在管理員不知情下暴露 DB 管理介面 |

### Warning（提示）

| 情境 | 建議 |
|------|------|
| adminer 缺 `ADMINER_DEFAULT_SERVER` | 設為 DB service 名（如 `postgres`），方便登入 |
| adminer 缺 `ADMINER_DEFAULT_DB_DRIVER` | 設為 `pgsql` 等對應 driver |
| seq 缺 `SEQ_FIRSTRUN_ADMINUSERNAME` | 設為 `admin` |

### 反向檢查為何是 error，不是 warning

「compose 有、需求沒有」會讓服務清單與實際部署脫鉤；更嚴重的是 adminer 可能在無人知情下暴露 DB 管理介面（安全事件）。因此反向檢查為 error。本規則**僅針對選配服務**（adminer / seq）；app-level service（frontend / backend / 業務 container）不受反向規則限制。
