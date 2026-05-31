# Meeting Tracker Config（主管自編）

> 程式只讀下方 `mt-config` 區塊（schema validation 強制）。敘事說明放區塊外。
> ⚠️ 不要把任何密碼 / app password / webhook URL 寫進此檔——secret 一律放 Routine/本機 env。

```mt-config
schema_version: 1
tenant_id: <你的部門 slug，如 acme-ops>
timezone: Asia/Taipei
week_start: monday
meeting_day: wednesday
business_days: [mon, tue, wed, thu, fri]
paths:
  tracking_file: <追蹤檔相對路徑>
  draft_dir: <draft 輸出目錄，如 drafts/>
  context_dir: <context base 目錄，如 context/>
  state_file: state/meeting_tracker_state.json
  run_log_dir: run-log/
send:
  adapter: n8n_webhook   # n8n_webhook | gmail_smtp
owners:
  - owner_id: <stable slug>
    name: <顯示名>
    email: <可達 email>
    alias_allowlist: []   # 回信可能用的其他位址
    tier: 1               # 1=人工屬下 / 2=AI 屬下（om-daily-work-log，v1.5）；不確定先填 1
metrics:
  - metric_id: <stable slug>
    owner_id: <對應 owner_id>
    title: <指標名>
    deadline: "<YYYY-MM-DD>"
    cadence: daily        # daily | business_days | overdue_only | snooze:<YYYY-MM-DD>
    meeting_id: <會議 slug>
```
