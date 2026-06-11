# service-url.md — SERVICE_URL / SERVICE_FQDN magic env

> **何時讀**：要讓某個 HTTP 服務拿到 Coolify 自動分配的對外網址時。

Coolify 會自動為 HTTP/HTTPS 服務分配一個對外網址，並透過 **magic env**（魔法環境變數）注入 container。你只要在 compose 宣告變數名、**冒號後留空**，Coolify 就會填值。

這套機制與「自訂網域」（custom domain）是**正交、可並存**的兩種對外曝露方式——magic env 讓 Coolify 給你一個自動網址；custom domain 讓你綁自己的網域。何時用哪個見 `domains-and-tls.md`。

---

## Coolify 會展開的 magic env（僅這幾類）

Coolify **只**展開以下前綴的變數，其餘變數一律當普通 env 處理（要自己在 Coolify env 面板填值）：

| Magic env | 用途 |
|-----------|------|
| `SERVICE_URL_*` | 自動分配的完整對外 URL（含 scheme） |
| `SERVICE_FQDN_*` | 自動分配的 FQDN（網域名，不含 scheme） |
| `SERVICE_PASSWORD_*` | Coolify 產生的隨機密碼 |
| `COMPOSE_PROJECT_NAME` | 專案名（用於 named volume 命名） |

> **重要例外**：`SEQ_FIRSTRUN_ADMINPASSWORD` **不在**此清單，**不可**寫成 `${SEQ_FIRSTRUN_ADMINPASSWORD}`，也**不可**借用 `$SERVICE_PASSWORD_SEQ`。它的正確處理見 `seq.md`。不要把 Seq 密碼泛化成 magic env。

---

## 命名格式

`SERVICE_URL_{SERVICE_NAME}_{PORT}`

- `{SERVICE_NAME}`：必須對應 compose 中**實際的 service 名稱**（大寫）。
- `{PORT}`：該 service `expose` 的 port。

範例：service `backend` expose `8000` → `SERVICE_URL_BACKEND_8000:`。

---

## 寫法規則：冒號後留空

Coolify 會自動填值。**手動填值或用 `${}` 引用都會錯。**

```yaml
# ✅ 正確
environment:
  SERVICE_URL_BACKEND_8000:

# ❌ 錯誤
environment:
  - SERVICE_URL_BACKEND_8000:                              # list 語法
  SERVICE_URL_BACKEND_8000: http://...                     # 自行填值
  SERVICE_URL_BACKEND_8000: ${SERVICE_URL_BACKEND_8000}    # ${} 引用
```

---

## 僅適用 HTTP / HTTPS 服務

| 協定 | SERVICE_URL | 連線方式 |
|------|-------------|---------|
| HTTP / HTTPS（frontend、backend、adminer、seq…） | ✅ | `SERVICE_URL_*:`（冒號後留空） |
| TCP（PostgreSQL、Redis、MySQL、MongoDB…） | ❌ | 用專屬變數（`DATABASE_URL` / `REDIS_URL`）；compose 內走 service 名互連（如 `postgres:5432`） |

對 TCP 服務寫 `SERVICE_URL_*` 不會有效，也不需要。

---

## 常見問題

| 問題 | 原因 | 解法 |
|------|------|------|
| SERVICE_URL 取到空字串 | 冒號後填了內容或用 `${}` 引用 | 冒號後保持空白 |
| DB / Redis 的 SERVICE_URL 沒作用 | TCP 服務不支援 | 不要填，用 `DATABASE_URL` / service 名互連 |
| 變數名對不上 service | `{SERVICE_NAME}` 拼錯或 port 不符 `expose` | 對齊 compose 實際 service 名與 expose port |
