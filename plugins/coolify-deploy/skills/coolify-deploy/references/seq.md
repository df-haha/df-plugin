# seq.md — Seq（集中式 log 收集，選配）

> **何時讀**:要在 compose 加入 Seq（集中式結構化 log）時。

> **這份是 Seq 的 single source of truth**。`observability.md` 引用本檔的 Seq 內容,不重寫。Adminer(DB 管理 UI)已移到姊妹 skill `coolify-db` 的 `references/adminer.md`。

Seq 是**選配**服務:不需要就不要生成。

---

## 架構

```
應用程式 → SDK(批次 / 重試 / flush)→ POST http://seq/api/events/raw (CLEF) → Seq UI
```

推薦**應用程式直接 HTTP 推送 CLEF**(透過語言對應 SDK),不要用 Docker gelf driver + `seq-input-gelf` sidecar 的舊架構。少一個 sidecar、支援結構化欄位(message template + properties)、跨語言一致、無須 publish UDP port。只有在應用是黑箱 image 無法改 code 時才退回舊 gelf。

---

## first-run 管理員密碼(⚠️ 高優先,易踩雷)

`datalust/seq` 啟動時若未提供 first-run admin password 會 crash(`No default admin password was supplied`)。但**密碼設定方式有陷阱**:

- canonical compose 必寫 `SEQ_FIRSTRUN_ADMINPASSWORD:`(**冒號後留空**)。
- `SEQ_FIRSTRUN_ADMINPASSWORD` **不是** Coolify magic env(Coolify 只展開 `SERVICE_URL_*` / `SERVICE_FQDN_*` / `SERVICE_PASSWORD_*` / `COMPOSE_PROJECT_NAME`)。因此 **禁** 寫 `${SEQ_FIRSTRUN_ADMINPASSWORD}`,也**禁**借用 `$SERVICE_PASSWORD_SEQ`。
- 正確注入方式:**部署者先在 Coolify Application → Environment Variables 面板手動填一個 10–15 字元隨機值**(標 `is_secret`),Coolify 以同名 env var 注入 container;compose 端維持冒號後留空、不做 interpolation。
- 登入:部署後到 Coolify → Environment Variables 查 `SEQ_FIRSTRUN_ADMINPASSWORD` 的值。密碼只在 volume **初次**初始化時寫入,之後改 env 無效(要到 Seq UI 改)。

---

## 其餘 Seq 規則

1. **不要對 Seq 設 healthcheck** — image 不一定有 curl/wget,易永遠失敗卡死。依賴 Seq 的 service 只能 plain `depends_on` 或 `condition: service_started`,**禁** `service_healthy`。
2. **volume 損壞要砍掉重建** — 多次部署失敗後 `seq-data` 可能殘留半成品,噴 `Failed to initialize storage`。把 compose 內的 seq volume 刪除後 commit 讓 Coolify 處理,再補回 commit 重新部署。
3. **compose 網路內走 `http://seq`(port 80)** — 對應 `expose: "80"` 與 `SERVICE_URL_SEQ_80:`。SDK 自動 append `/api/events/raw`。
4. **`SEQ_INGESTION_URL` 留空就 fallback console** — logger wrapper 要寫成「沒設此 env 就只走 `console.log` / `print`」,這樣 `npm run dev` / CI 單測不會嘗試連線。
5. **log ingestion 不得阻斷主服務啟動** — 主服務不可因 Seq 未起來而卡住(呼應規則 1 的 `service_started`)。

---

## 應用程式配合方式

| 語言 / 框架 | 套件 | 掛接方式 |
|-------------|------|---------|
| Node.js / Next.js | `seq-logging` | 自訂 logger wrapper |
| .NET / ASP.NET Core | `Serilog.Sinks.Seq` | Serilog sink |
| Java / Spring Boot | `seq-logback-appender` | `logback-spring.xml` |
| Python / FastAPI / Django | `seqlog` | 標準 `logging` |
| Go | `github.com/nullseed/logruseq` | logrus hook |

通用導入 pattern:

1. 裝 SDK 寫 logger wrapper(讀 `SEQ_INGESTION_URL` / `SEQ_API_KEY` / `APP_NAME`)。
2. 沒設 `SEQ_INGESTION_URL` 時 fallback 只印 console。
3. 替換程式內 `console.log` / `print` / `logger.info` → wrapper 呼叫。
4. 用 **message template**(`"user {UserId} logged in"` + `{ UserId: 123 }`),不要字串拼接。
5. 註冊 `SIGTERM` / `SIGINT` / `beforeExit` → 呼叫 SDK `close()` / `flush()`。
6. 統一把 `Error` 物件序列化進 CLEF 的 `exception` 欄位。

---

## Seq 一致性檢查(Lint)

### Error(必擋)

| 情境 | 說明 |
|------|------|
| 生成 seq 但缺 `SERVICE_URL_SEQ_80` | seq 對外無網址 |
| 生成 seq 但缺 `ACCEPT_EULA: "Y"` | Seq 拒絕啟動 |
| 生成 seq 但缺 `SEQ_FIRSTRUN_ADMINPASSWORD`(冒號後留空那行) | Seq crash `No default admin password` |
| 未刻意需要卻生成 seq | 反向檢查:可能在管理員不知情下暴露 log UI(含可被搜尋的請求內容) |

### Warning

| 情境 | 建議 |
|------|------|
| seq 缺 `SEQ_FIRSTRUN_ADMINUSERNAME` | 設為 `admin` |

> 反向檢查為何是 error,不是 warning:「compose 有、需求沒有」會讓服務清單與實際部署脫鉤;Seq UI 內含全部請求 log,在無人知情下暴露 = 安全事件。本規則**僅針對選配服務**(seq);app-level service 不受反向規則限制。
