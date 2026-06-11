# adminer.md — Adminer（DB 管理 UI，選配）

> **何時讀**：要在 compose 加入 Adminer 管 DB 用、或 Adminer 登入失敗 debug 時。

Adminer 是輕量 DB 管理 Web UI（單檔 PHP），透過 Coolify 分配的對外網址用瀏覽器連線。

⚠️ Adminer 本身**無任何內建存取控制**：知道 URL 即可嘗試登入，Adminer 登入密碼等於 DB 密碼，洩漏即整個 DB 被拿。配置紀律見最後一段。

---

## compose 段落

```yaml
adminer:
  image: adminer:4.8.1-standalone        # 第三方固定版工具，允許 pinned literal；禁 latest
  environment:
    SERVICE_URL_ADMINER_8080:            # Coolify magic env：冒號後留空
    ADMINER_DEFAULT_SERVER: db           # 對應 compose 內 DB service 名（不是 localhost）
    ADMINER_DEFAULT_DB_DRIVER: pgsql
    TZ: Asia/Taipei
  expose:
    - "8080"
  depends_on:
    db:
      condition: service_healthy
  restart: unless-stopped
```

---

## 環境變數對照

| 變數 | 必填 | 用途 |
|------|------|------|
| `SERVICE_URL_ADMINER_8080:` | ✅ | Coolify 分配對外網址（冒號後留空，見 coolify-deploy `references/service-url.md`） |
| `ADMINER_DEFAULT_SERVER` | 建議 | 登入畫面預設 server hostname，填 compose DB service 名（如 `db` / `postgres`） |
| `ADMINER_DEFAULT_DB_DRIVER` | 建議 | `pgsql` / `server`(MySQL/MariaDB) / `sqlite` / `mssql` / `oracle` / `mongo` |
| `ADMINER_DESIGN` | 選配 | UI 主題，如 `pepa-linha` / `hydra` |
| `ADMINER_PLUGINS` | 選配 | 啟用額外 plugins，如 `tables-filter dump-json` |

---

## 登入欄位對照

| Adminer 欄位 | 填什麼 |
|--------------|--------|
| **System** | PostgreSQL（或對應 DB 類型） |
| **Server** | `db`（compose service 名，**不是** localhost / 127.0.0.1） |
| **Username** | DB 使用者（writer role：`${WRITER_USER}`；admin：`${POSTGRES_USER}`） |
| **Password** | 對應密碼 |
| **Database** | DB 名（`${POSTGRES_DB}`），可留空登入後再選 |

登入哪個 role 看用途：
- 日常查 / 改業務資料 → writer role
- 改 schema / 建 user / 重設密碼 → admin role（`POSTGRES_USER`）
- 只看不改 → reader role（會自動拒絕 INSERT/UPDATE，驗證權限隔離正確）

---

## 在無 terminal 環境跑 SQL（典型用途）

Coolify 預設不提供 web terminal 進 container（也沒有 `coolify exec` CLI 指令，見姊妹 skill **coolify-logs** 的 cli-commands.md）。當你**只有 Adminer + Coolify UI** 還要做：

- 補建一個 admin / superadmin 帳號（業務帳號系統，不是 DB role）
- 跑 ad-hoc UPDATE 修壞掉的資料
- 跑 backup query 抓欄位看

→ Adminer 的「SQL command」分頁是唯一 web 端 channel。**所有改寫操作**前：

1. 在 SQL command 跑對應的 `SELECT` 先看要改的 row
2. 寫 `BEGIN; <UPDATE/INSERT>; SELECT * FROM ... -- 確認 -- ROLLBACK;` 模式，先 ROLLBACK 確認受影響範圍
3. 確認後改 `COMMIT`

⚠️ Adminer SQL command 沒有「執行單條 highlighted SQL」按鈕，整個 textarea 一次跑光 —— 別把 `COMMIT` 跟 `ROLLBACK` 寫在同一段 textarea 裡誤觸。

---

## 安全紀律（⚠️ core 規則，非 trivia）

1. **`POSTGRES_PASSWORD` / `WRITER_PASSWORD` 絕對不能為空** —— Adminer 接受空密碼登入，這是設定錯誤而不是 feature。
2. **正式環境未加 Basic Auth / IP allowlist 就不要生成 adminer**（或部署後在 Coolify 把 service stop 平時停用，要用時 start）。Basic Auth 走 Coolify 的 application-level proxy 設定或前置反代。
3. **Image 綁版**，禁 `adminer:latest` 進正式環境（pinned literal `4.8.1-standalone` 即可）。
4. **SERVICE_URL 不得貼進文件 / README / 聊天工具 / commit message** —— 等同洩漏 DB 後門。團隊內共享走有 access control 的內部 wiki / 1Password。
5. **Adminer URL 進 deny list**：把 Adminer 的對外網址加進公司 DLP / 出口監控的白名單管制清單，異常存取要被注意到。

---

## 替代工具（功能換更強）

| 工具 | Image | 適用 |
|------|-------|------|
| **Adminer**（預設） | `adminer:4.8.1-standalone` | 輕量、單檔、快查 |
| pgAdmin | `dpage/pgadmin4` | PostgreSQL 重度、ER 圖 / explain plan |
| DBGate | `dbgate/dbgate` | 多類型 DB、SQL history |
| CloudBeaver | `dbeaver/cloudbeaver` | 企業級多人協作、帳號權限 |

走 pgAdmin / CloudBeaver 時，安全紀律一樣：**未加 Basic Auth / IP allowlist 不部署**。Adminer 一直是輕量首選，安全護欄上得起來就用它。
