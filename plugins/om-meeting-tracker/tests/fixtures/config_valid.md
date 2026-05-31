# Meeting Tracker Config（測試 fixture，去識別化）

```mt-config
schema_version: 1
tenant_id: acme-ops
timezone: Asia/Taipei
week_start: monday
meeting_day: wednesday
business_days: [mon, tue, wed, thu, fri]
paths:
  tracking_file: tracking/weekly.md
  draft_dir: drafts/
  context_dir: context/
  state_file: state/meeting_tracker_state.json
  run_log_dir: run-log/
send:
  adapter: n8n_webhook
owners:
  - owner_id: alice
    name: Alice
    email: alice@example.com
    alias_allowlist: [alice.work@example.com]
    tier: 2
  - owner_id: bob
    name: Bob
    email: bob@example.com
    alias_allowlist: []
metrics:
  - metric_id: cost-q2
    owner_id: alice
    title: Q2 降本
    deadline: "2026-06-30"
    cadence: daily
    meeting_id: weekly-ops
  - metric_id: revenue-q2
    owner_id: bob
    title: Q2 增效
    deadline: "2026-06-30"
    cadence: business_days
    meeting_id: weekly-ops
```
