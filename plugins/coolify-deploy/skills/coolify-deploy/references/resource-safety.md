# resource-safety.md — Coolify 部署資源安全

> **何時讀**:部署與 production runtime / DB 共用一台 host、主機 ≤ 8 GiB RAM、build 卡住、server 失聯、或 log 出現 OOM / `no space left on device` / `context canceled` 時。

## 目錄

1. 部署前 read-only preflight 與 Stop gate
2. Coolify server 設定
3. Swap safety net
4. Runtime container limits
5. 部署中監控與中止門檻
6. 部署後驗證
7. 何時升級或分離 build

## 核心判斷

- `mem_limit` / `mem_reservation` 只限制啟動後的 container,**不限制 `docker build`**。
- swap 是防止 host 瞬間 OOM 的安全網,**不是可長期當 RAM 用的容量**。
- build、Coolify controller、production app 與 PostgreSQL 共用一台小主機時,預設只允許 **1 個 concurrent build**。
- 不要用未量測的 hard memory limit 「保護」 PostgreSQL;過低上限會讓 Docker 直接殺掉資料庫。

## 1. 部署前 read-only preflight

在 host 上只讀執行:

```bash
free -h
swapon --show
df -h / /var/lib/docker
docker system df
docker ps --format 'table {{.Names}}\t{{.Status}}'
docker stats --no-stream
journalctl -k --since '-24 hours' --no-pager \
  | grep -Ei 'oom|out of memory|killed process|no space|i/o error' \
  || true
```

再用 Coolify CLI / API 確認:

- 沒有另一個 build / deploy 在跑。
- 沒有 backup、大型 migration 或匯入在跑。
- 部署佇列沒有重複 webhook 產生的同 commit 任務。

### Stop gate

任一成立就先停止新部署,處理根因:

- 最近 24 小時有 OOM,且尚未增加容量或降低並行度。
- 共用 host ≤ 8 GiB RAM 且沒有 swap safety net。
- `MemAvailable` 低於總 RAM 的 25%,或低於 2 GiB（取較嚴格者）,而 build peak 尚未量測。
- root / Docker 剩餘空間低於 15%,或低於 10 GiB（取較嚴格者）。
- swap 已使用過半且持續上升。
- 另一個 deploy / build / backup / migration 正在同 host 執行。

這些是不知 build peak 時的保守基準,不是容量保證。有歷史 peak 時,預留最近成功 build peak 的 1.25–1.5 倍,並保留 production runtime headroom。

## 2. Coolify server 設定

在 `Servers > <server> > Configuration > Advanced`:

- **Number of concurrent builds**:共用 host ≤ 8 GiB 設 `1`;只在有專用 build server 與實測容量後才提高。
- **Deployment timeout**:保留足以完成正常 build 的時間;加長 timeout 不會解決 OOM。
- **Deployment queue limit**:設置有限佇列,避免 webhook 連發把主機長時間壓滿。

這是 server 層設定,不是單一 application 的 runtime limit。優先用 Coolify API / CLI;若當前 API 不支援該欄位,提供人工 UI 路徑,不得未授權改用 GUI automation。

## 3. Swap safety net

小型 Linux host 可建立有限 swap 吸收 build 瞬間峰值。建議先讀取現況,再取得使用者對當次主機變更的明確授權。

一般起點:

- 8 GiB RAM 共用 host:4 GiB swap。
- `vm.swappiness=10`:優先使用 RAM,只在壓力下使用 swap。
- 必須寫入 `/etc/fstab`,並在重開後驗證 `swapon --show`。

不要:

- 把 8 GiB RAM + 8 GiB swap 描述成 16 GiB RAM。
- 在 swap 持續大量使用時照常部署;DB latency 可能因磁碟 I/O 上升。
- 未檢查既有 `/swapfile`、fstab 與 sysctl 檔就盲目重建。

## 4. Runtime container limits

Coolify 的 Docker Compose deployment 以 compose 為 SSOT。對 app / worker / 非關鍵工具,依實測 p95 加 30–50% headroom 設:

```yaml
services:
  web:
    mem_reservation: "<measured-soft-limit>"
    mem_limit: "<measured-hard-limit>"
    cpus: "<measured-cpu-limit>"
```

規則:

- 先從 `docker stats --no-stream` 與成功 deploy peak 取得數據,不要猜一個絕對值。
- soft reservation 用來表達壓力下優先權;hard limit 過低會導致 `OOMKilled=true`。
- PostgreSQL / Redis 需要額外容量處理 startup、backup、compaction 與 query burst;先負載驗證,再設 hard limit。
- 這些欄位不限制 image build。Build 峰值依靠降低並行度、改善 Dockerfile cache、升級 RAM,或使用專用 build server。

## 5. 部署中監控與中止門檻

另開一個 host terminal 監控:

```bash
watch -n 5 'free -h; swapon --show; df -h / /var/lib/docker'
```

另一個 terminal 看 kernel 事件:

```bash
journalctl -kf | grep -Ei 'oom|out of memory|killed process|no space|i/o error'
```

立即取消新 deployment,不要繼續重試,如果:

- 出現任何新 OOM killer 事件。
- `MemAvailable` 連續兩次低於 512 MiB。
- swap 快速增加,且 production latency / healthcheck 開始惡化。
- Docker daemon 大量出現 `closed fifo`、`broken pipe`、`context canceled`。
- disk 剩餘空間逼近 preflight stop gate。

中止後先保留日誌與現場。不要立即 reboot,也不要盲目執行 `docker system prune`。

## 6. 部署後驗證

```bash
free -h
swapon --show
docker stats --no-stream
docker ps -q | xargs -r docker inspect \
  --format '{{.Name}} OOMKilled={{.State.OOMKilled}} RestartCount={{.RestartCount}}'
journalctl -k --since '-30 minutes' --no-pager \
  | grep -Ei 'oom|out of memory|killed process|no space|i/o error' \
  || true
```

記錄:部署時間、commit、build 時長、RAM / swap peak、disk 前後差、OOM / restart 結果。這些數據會成為下次 preflight 的容量基準。

## 7. 何時升級或分離 build

任一成立就不應只靠加 swap:

- 再次發生 host-level OOM。
- 正常時段 `MemAvailable` 長期低於 25%。
- 部署後 swap 使用超過 1 GiB 且持續 15 分鐘。
- production workload 與 build 必須常態並行。
- PostgreSQL latency / healthcheck 在 build 期間顯著惡化。

優先順序:小主機先升至至少 16 GiB RAM;再視負載將 build 或 DB 移到獨立主機。

## 禁止捷徑

- 禁在不知容器用途與 rollback 需求時執行 `docker system prune`。
- 禁為了保住 DB 直接設 `oom_score_adj=-1000`;這可能改為殺掉 `dockerd`、`systemd` 或其他關鍵程序。
- 禁把 timeout 調長當作 OOM 修復。
- 禁在另一個 deploy 尚未結束時重複按 Deploy。
- 禁在未量測前用過低 hard limit 強行限制 Next.js / JVM / PostgreSQL build/runtime。

## 官方依據

- Coolify next docs — server build/deployment limits（設定欄位仍需對照當前 instance 版本）:https://next.coolify.io/docs/core/infrastructure/servers/manage-server
- Coolify stable docs — application resource limits:https://coolify.io/docs/applications/index
- Coolify stable docs — Sentinel / Metrics 限制:https://coolify.io/docs/knowledge-base/server/sentinel
- Coolify stable docs — monitoring 範圍:https://coolify.io/docs/knowledge-base/monitoring
- Coolify stable docs — installation hardware note:https://coolify.io/docs/get-started/installation
