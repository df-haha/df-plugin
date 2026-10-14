#!/usr/bin/env bash
# gputw「純時限」停機保險（從實戰腳本改寫；本份用假 API 測過，實戰原檔 2026-10-06 在真機上掛過）
# 第三條停機保險：不用 ssh、不靠機器上任何程式。拿到 instance id 就發射，
# 睡滿 N 秒後，只要機器還沒停，就打 API 停機，每 60 秒重送，直到回查看到 STOPPED。
#
# 用法（開機拿到 id 的同一個動作就發射）：
#   export GPUTW_TOKEN=...
#   setsid nohup ./gputw_api_backstop.sh <instance-id> <秒數> </dev/null >/dev/null 2>&1 &
#
# 同一台機器重新發射時，新的一輪會改寫 pid 檔；舊的那輪到期看到 pid 檔不是自己，就不動手。
set -uo pipefail
IID="${1:?需要 instance id}"
SECS="${2:?需要秒數}"
HERE="$(cd "$(dirname "$0")" && pwd)"
API="${GPUTW_API_SH:-$HERE/gputw_api.sh}"     # 背景執行時工作目錄不一定對，用絕對路徑
LOG_DIR="${LOG_DIR:-$HERE/logs}"
mkdir -p "$LOG_DIR"
LOG="$LOG_DIR/backstop_${IID}.log"
PID_FILE="$LOG_DIR/backstop_${IID}.pid"
log() { echo "[$(date '+%F %T')] $*" >> "$LOG"; }
status_of() { "$API" GET /instances 2>/dev/null | python3 -c "
import json,sys
try:
    d=json.load(sys.stdin); print(next((str(i['status']).upper() for i in d['data'] if str(i['id'])=='$IID'),'GONE'))
except Exception: print('')"; }
mine() { [ "$(cat "$PID_FILE" 2>/dev/null)" = "$$" ]; }
[[ "$SECS" =~ ^[0-9]+$ ]] || { echo "FATAL: 秒數 $SECS 不是整數" >&2; exit 1; }
st0=$(status_of)
case "$st0" in
  "")   echo "FATAL: 查不到 API（金鑰？網路？），保險沒有發射" >&2; exit 1 ;;
  GONE) echo "FATAL: API 裡沒有 instance $IID（id 打錯？），保險沒有發射" >&2; exit 1 ;;
esac

echo $$ > "$PID_FILE"
log "啟動：instance=$IID，${SECS} 秒後到期（pid $$）"
sleep "$SECS"
mine || { log "pid 檔已被新一輪取代，不動手"; exit 0; }
for i in $(seq 1 180); do
  st=$(status_of)
  log "到期回查 #$i: status=${st:-unknown}"
  case "$st" in STOPPED|GONE|TERMINATED) log "已停（$st），收工"; exit 0;; esac
  mine || { log "pid 檔已被新一輪取代，停止重送"; exit 0; }   # 每次送出前都重新確認
  "$API" POST /instances/stop "{\"instanceId\":\"$IID\"}" >> "$LOG" 2>&1 || true
  sleep "${BACKSTOP_RETRY_SEC:-60}"
done
log "ALERT: 重送 180 次仍沒停，要人工處理"
touch "$LOG_DIR/ALERT_BACKSTOP_${IID}"
exit 2
