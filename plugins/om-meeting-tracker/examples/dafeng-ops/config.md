# 大豐營運部 Meeting Tracker Config（seed 範例）

```mt-config
schema_version: 1
tenant_id: dafeng-ops
timezone: Asia/Taipei
week_start: monday
meeting_day: wednesday
business_days: [mon, tue, wed, thu, fri]
paths:
  tracking_file: tracking.md
  draft_dir: drafts/
  context_dir: context/
  state_file: state/meeting_tracker_state.json
  run_log_dir: run-log/
send:
  adapter: n8n_webhook
owners:
  - owner_id: haha
    name: Haha
    email: haha@example.com
    alias_allowlist: []
  - owner_id: hsin-ping
    name: 蕭欣萍
    email: hsin-ping@example.com
    alias_allowlist: []
  - owner_id: mei-hsing
    name: 林梅杏
    email: mei-hsing@example.com
    alias_allowlist: []
  - owner_id: bin-rong
    name: 王彬褣
    email: bin-rong@example.com
    alias_allowlist: []
metrics:
  - metric_id: g1-tender
    owner_id: haha
    title: 政府標案 G1
    deadline: "2026-05-31"
    cadence: daily
    meeting_id: cost-reduction-weekly
  - metric_id: recycle-recon
    owner_id: hsin-ping
    title: 回收站對帳
    deadline: "2026-06-30"
    cadence: daily
    meeting_id: cost-reduction-weekly
  - metric_id: steel-buy
    owner_id: mei-hsing
    title: 靠行/鋼廠買貨
    deadline: "2026-06-30"
    cadence: business_days
    meeting_id: cost-reduction-weekly
  - metric_id: caotun-med
    owner_id: bin-rong
    title: 草屯門市/外載醫療
    deadline: "2026-06-30"
    cadence: daily
    meeting_id: cost-reduction-weekly
```
