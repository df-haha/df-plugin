#!/usr/bin/env bash
# 停機保險（dead-man）骨架（從實戰腳本簡化；本份用假 API／假 ssh 測過，沒在真機器上跑過）
# 開機拿到 instance id 的同一個動作就發射：
#   INSTANCE_ID=<id> SSH_DEST=<user>@<host> SSH_PORT=2222 HARD_TIMEOUT_H=8 \
#   setsid nohup ./deadman_template.sh > deadman.log 2>&1 < /dev/null &
#
# 預設是 gputw。換平台只要改下面「平台相關」的 api_stop／instance_status 兩個函式。
# 開跑時任何前提不對（沒設 id、時數不是整數、API 找不到這台機器），立刻報錯結束，
# 不要等到該停機那一刻才發現停不了。
set -uo pipefail

HERE="$(cd "$(dirname "$0")" && pwd)"            # 背景執行時工作目錄不一定對，一律用絕對路徑
API="${GPUTW_API_SH:-$HERE/gputw_api.sh}"
POLL_SEC="${POLL_SEC:-120}"                      # 多久檢查一次
STARTUP_GRACE_MIN="${STARTUP_GRACE_MIN:-90}"     # 開機後最多等幾分鐘讓訓練程式出現（裝環境、下載模型）
IDLE_STOP_MIN="${IDLE_STOP_MIN:-20}"             # 訓練程式出現過之後，消失幾分鐘就收工
HARD_TIMEOUT_H="${HARD_TIMEOUT_H:-8}"            # 硬上限（小時，只吃整數）
SSH_FAIL_MAX="${SSH_FAIL_MAX:-6}"                # 連續幾次 ssh 連不上就當失聯，直接停機
HARVEST_MAX_SEC="${HARVEST_MAX_SEC:-900}"        # 收工時拉檔最多花幾秒，時間到不管拉完沒有都停機
LOCAL_DIR="${LOCAL_DIR:-./remote_snapshots}"
REMOTE_DIR="${REMOTE_DIR:-/workspace/run}"
PROC_PATTERN="${PROC_PATTERN:-[t]rain.py}"       # 中括號寫法避免 pgrep 找到自己
SSH_PORT="${SSH_PORT:-22}"                       # gputw 是 2222
GONE_MAX="${GONE_MAX:-3}"                        # 看過機器在跑之後，連續幾次查到「已停」才算被外部停掉
: "${INSTANCE_ID:?需要 INSTANCE_ID}" "${SSH_DEST:?需要 SSH_DEST}"
for v in POLL_SEC STARTUP_GRACE_MIN IDLE_STOP_MIN HARD_TIMEOUT_H SSH_FAIL_MAX HARVEST_MAX_SEC SSH_PORT GONE_MAX; do
  [[ "${!v}" =~ ^[0-9]+$ ]] || { echo "FATAL: $v=${!v} 不是整數" >&2; exit 1; }
done
[ -x "$API" ] || { echo "FATAL: 找不到可執行的 API 腳本 $API" >&2; exit 1; }
mkdir -p "$LOCAL_DIR" || { echo "FATAL: 建不了 $LOCAL_DIR" >&2; exit 1; }

# ===== 平台相關：照你的平台改 =====
api_stop() { timeout 60 "$API" POST /instances/stop "{\"instanceId\":\"$INSTANCE_ID\"}" >/dev/null 2>&1 || true; }
instance_status() {   # 印這台機器的狀態（大寫）；API 查不到這台印 GONE；API 本身失敗印空字串
  timeout 60 "$API" GET /instances 2>/dev/null | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin); items=d.get('data', d) if isinstance(d, dict) else d
    print(next((str(i.get('status','')).upper() for i in items if str(i.get('id'))=='$INSTANCE_ID'), 'GONE'))
except Exception: print('')"; }
# ===================================

SSH_OPTS=(-p "$SSH_PORT" -o BatchMode=yes -o ConnectTimeout=20
          -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o LogLevel=ERROR)
SCP_OPTS=(-P "$SSH_PORT" -o BatchMode=yes -o ConnectTimeout=20
          -o UserKnownHostsFile=/dev/null -o StrictHostKeyChecking=no -o LogLevel=ERROR)
rsh()  { timeout 120 ssh -n "${SSH_OPTS[@]}" "$SSH_DEST" "$@"; }        # 連線失敗回 255（timeout 到期回 124）
pull() { timeout 300 scp -q "${SCP_OPTS[@]}" "$SSH_DEST:$1" "$LOCAL_DIR/" 2>/dev/null || true; }
log()  { echo "[$(date '+%F %T')] $*"; }

st0="$(instance_status)"
case "$st0" in
  "")   echo "FATAL: 查不到 API（金鑰？網路？），保險沒有發射" >&2; exit 1 ;;
  GONE) echo "FATAL: API 裡沒有 instance $INSTANCE_ID（id 打錯？），保險沒有發射" >&2; exit 1 ;;
esac

snapshot() { for f in train.log progress.json RESULT_SUMMARY.json; do pull "$REMOTE_DIR/$f"; done; }
harvest()  { rsh "cd $REMOTE_DIR && tar -czf /tmp/results.tgz results *.log 2>/dev/null || true"; pull /tmp/results.tgz; }

stop_and_verify() {
  api_stop
  for i in $(seq 1 20); do
    sleep 30
    case "$(instance_status)" in STOPPED|GONE|TERMINATED) log "已確認停機（第 $i 次回查）"; return 0;; esac
    if [ "$i" = 3 ] || [ "$i" = 10 ]; then api_stop; fi
  done
  log "20 次回查都沒看到停機！"
  touch "$LOCAL_DIR/ALERT_STOP_UNVERIFIED"
  command -v notify-send >/dev/null && notify-send "GPU 停機未確認" "$INSTANCE_ID"
  return 1
}

finish() {
  log "收工原因：$1"
  # 拉檔放背景、限時；時間到先送停機，再殺掉還沒拉完的子程序。拉檔卡住不能擋住停機。
  ( snapshot; harvest ) & local hp=$!
  local waited=0
  while kill -0 "$hp" 2>/dev/null && [ "$waited" -lt "$HARVEST_MAX_SEC" ]; do sleep 5; waited=$((waited + 5)); done
  if kill -0 "$hp" 2>/dev/null; then
    api_stop
    pkill -P "$hp" 2>/dev/null; kill "$hp" 2>/dev/null
    log "拉檔超過 ${HARVEST_MAX_SEC} 秒，已先送停機並放棄拉檔"
  fi
  if stop_and_verify; then exit 0; else exit 2; fi
}

start=$(date +%s); seen_once=0; idle_since=""; ssh_fail=0; seen_running=0; gone_n=0
log "停機保險啟動：instance=$INSTANCE_ID 狀態=$st0 poll=${POLL_SEC}s grace=${STARTUP_GRACE_MIN}m idle=${IDLE_STOP_MIN}m hard=${HARD_TIMEOUT_H}h"
while true; do
  now=$(date +%s)
  (( now - start >= HARD_TIMEOUT_H * 3600 )) && finish "硬上限 ${HARD_TIMEOUT_H} 小時"
  st="$(instance_status)"
  if [ "$st" = RUNNING ]; then
    seen_running=1; gone_n=0
  elif [ "$seen_running" -eq 1 ] && [[ "$st" =~ ^(STOPPED|GONE|TERMINATED)$ ]]; then
    # 只有「看過它在跑」之後的「已停」才算被外部停掉；剛開機還沒起來時看到 STOPPED 不算
    gone_n=$((gone_n + 1)); log "查到狀態 $st（連續 $gone_n 次）"
    [ "$gone_n" -ge "$GONE_MAX" ] && { log "機器已被外部停掉，收工"; exit 0; }
  fi
  if [ "$seen_running" -eq 0 ]; then                  # 還沒開好：不連 ssh，只看開機寬限期
    (( now - start >= STARTUP_GRACE_MIN * 60 )) && finish "開機 ${STARTUP_GRACE_MIN} 分鐘機器都沒進入 RUNNING"
    sleep "$POLL_SEC"; continue
  fi

  snapshot
  rsh "test -f $REMOTE_DIR/ALL_DONE" && finish "完成標記"
  rsh "test -f $REMOTE_DIR/FAILED"   && finish "失敗標記"

  rsh "pgrep -f '$PROC_PATTERN' >/dev/null"; rc=$?
  if [ "$rc" -eq 255 ] || [ "$rc" -eq 124 ]; then     # ssh 連不上，不能當成「訓練程式消失」
    ssh_fail=$((ssh_fail + 1)); log "ssh 失敗（連續 $ssh_fail 次）"
    [ "$ssh_fail" -ge "$SSH_FAIL_MAX" ] && finish "ssh 連續 $ssh_fail 次失敗，當成失聯"
  else
    ssh_fail=0
    if [ "$rc" -eq 0 ]; then
      seen_once=1; idle_since=""
    elif [ "$seen_once" -eq 0 ]; then                 # 還在裝環境：只看開機寬限期
      (( now - start >= STARTUP_GRACE_MIN * 60 )) && finish "開機 ${STARTUP_GRACE_MIN} 分鐘訓練程式都沒出現"
    else
      [ -z "$idle_since" ] && idle_since=$now
      (( now - idle_since >= IDLE_STOP_MIN * 60 )) && finish "訓練程式消失 ${IDLE_STOP_MIN} 分鐘"
    fi
  fi
  sleep "$POLL_SEC"
done
