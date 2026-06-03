# om-daily-cockpit

營運主管的**每日駕駛艙** — 一條指令 `/hi` 整合郵件分流、團隊日報追蹤、async coaching directive loop，與（可選）情報/標案/社群雷達。

**department-agnostic、config 驅動、零 tenant hard-code**：所有部門特定值（成員、Outlook 帳號、追蹤檔、情報來源）都在你自己的 `config.md`，技術碼裡沒有任何寫死的姓名/公司/密鑰。

---

## 它做什麼

| 區塊 | 說明 | MVP 預設 |
|------|------|----------|
| 郵件分流 | Outlook 收件匣 P0-P3 分類 + 回覆建議（`email-triage`，走 outlook_local MCP） | ✅ 開 |
| 團隊日報追蹤 | 抓屬下日報附件、格式檢查、AI 用量、對齊度、主管疑問閉環（`team-daily-fetcher`） | ✅ 開 |
| Coaching directive loop | 產澄清問題卡 → compose/reply 寄屬下 → 屬下 directive-first 偵測回覆（依賴 `om-daily-work-log`） | ✅ 開 |
| 情報 / 標案 / 社群雷達 | 產業特化爬取 + First-Principles 分析（`intel`/`tender`/`fb`） | ⛔ 停用（框架在） |

## 安裝

1. 安裝相依 plugin：`om-daily-work-log`（≥1.2.0，已自給自足，內建日誌/寄信功能）。
2. 安裝核心 Python 相依：`pip install PyYAML`。
3. 跑 onboarding：對 Claude 說「cockpit setup」（或執行 `cockpit-onboarding` skill），它會引導你：
   填 config → 設 secrets env → 接 Outlook MCP → 驗證 → 跑 `/hi --quick`。

## 設定（config.md）

從 `templates/config.md` 複製一份填寫，驗證：

```bash
python3 scripts/oc_core/config.py --validate <你的>/config.md
```

把 config 路徑設進環境變數，之後免帶 `--config`：

```bash
export OM_DAILY_COCKPIT_CONFIG=<你的>/config.md
```

### 🔴 安全紅線

- **config.md 內零密鑰**：`services.*_env` 只填「環境變數名稱」，真值放本機 env。
  loader 會掃描 config，發現疑似密鑰（API key / JWT / 連線字串）直接拒絕。
- secret 一律走 env：`OM_COCKPIT_DATABASE_URL` / `OM_COCKPIT_GEMINI_API_KEY` / `OM_COCKPIT_N8N_*` / `OM_COCKPIT_TELEGRAM_*`。

## 用法

```bash
/hi                  # 完整模式：核心 + 所有 config 啟用的可選模組
/hi --quick          # 輕量：只跑核心（郵件分流 + 團隊日報 + directive loop）
/hi --config <path>  # 指定 config（或用 OM_DAILY_COCKPIT_CONFIG）
```

## Directive 契約（coaching loop 的核心）

主管端 `send_coaching_cards.py` 寄催辦信時一律帶：
- **主旨前綴**（config `directive.subject_prefix`，預設 `【每日追蹤】`）
- **HTML marker**：`<!-- OM_DIRECTIVE directive_id=… target_date=… employee_id=… source=compose|reply -->`

屬下端（`om-daily-work-log` SKILL Phase 0）用 Outlook MCP 依**前綴 + marker** 搜當日催辦信——
**同時涵蓋 compose（新信）與 reply（接日報）兩種來源**，繞過舊 reply-chain 抓不到 compose 的限制。

寄送採**嚴格 email 比對**：多屬下同主旨時不會串錯人；reply 找不到原日報會自動轉 compose 開新信。

## 已知限制

- **日報主旨慣例**：coaching loop 的 reply 路徑（`om-daily-work-log`）目前**假設屬下日報主旨為中文
  「每日工作報告 YYYY/MM/DD」**。非中文團隊的 reply-match 需把 `report_subject_pattern` 覆寫成自己的
  慣例（compose fallback 仍可運作，只是 reply 接信會 degrade）。這是語言慣例假設，非 tenant 識別洩漏。
- **產業 crawler 自備**：intel/tender/fb 啟用需自備對應 `scripts/<module>_crawler.py`（見下方 MVP 邊界）。

## 可選模組 storage 後端（Phase 4.5）

intel/tender/fb 啟用前須選 `storage`：

| 後端 | 說明 |
|------|------|
| `quick_only`（預設）| 不落任何 DB；最簡，每日即抓即用 |
| `sqlite` | 本機 SQLite 檔（`.om-cockpit/<tenant>.sqlite3`） |
| `postgres` | 你自己的 Postgres（`OM_COCKPIT_DATABASE_URL`），通用資料表（非寫死 schema） |

> **MVP 邊界**：本 plugin 打包了情報「分析框架」（`intel-scan` skill）與 storage adapter，
> 但**產業特定 crawler runtime 需自備**（換成你產業的 RSS/關鍵字來源）。

## 測試

```bash
python3 -m pytest tests/ -q
```

涵蓋：config schema / loader、no-hardcode gate、config→args 映射、storage adapter。
