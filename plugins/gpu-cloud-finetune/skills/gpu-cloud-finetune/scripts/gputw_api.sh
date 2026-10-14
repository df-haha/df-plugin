#!/usr/bin/env bash
# gputw.ai API 小包裝（從實戰腳本簡化，本份未重跑）
# 用法：
#   export GPUTW_TOKEN=...            # 不要寫進檔案
#   ./gputw_api.sh GET  /instances
#   ./gputw_api.sh GET  /nodes/available
#   ./gputw_api.sh GET  /auth/me
#   ./gputw_api.sh POST /instances/<id>/restart '{"mode":"same"}'
#   ./gputw_api.sh POST /instances/stop '{"instanceId":"<id>"}'
#   ./gputw_api.sh GET  '/vault/list?path=<資料夾>'        # 金鑰要有 vault:read 權限
set -euo pipefail
: "${GPUTW_TOKEN:?請先 export GPUTW_TOKEN}"
BASE="${GPUTW_API_BASE:-https://gputw.ai/api}"
METHOD="${1:?METHOD}"; PATH_="${2:?PATH}"; BODY="${3:-}"

# 金鑰不放在指令參數裡（別人用 ps 看得到）：寫進只有自己能讀的暫存檔，curl 用 -H @檔案 讀
HDR="$(umask 077 && mktemp)"; trap 'rm -f "$HDR"' EXIT
printf 'Authorization: Bearer %s\n' "$GPUTW_TOKEN" > "$HDR"

for i in $(seq 1 5); do   # GET 偶爾回截斷 JSON，重試
  if [ -n "$BODY" ]; then
    out=$(curl -sS -X "$METHOD" "$BASE$PATH_" -H "@$HDR" \
          -H 'Content-Type: application/json' -d "$BODY") || out=""
  else
    out=$(curl -sS -X "$METHOD" "$BASE$PATH_" -H "@$HDR") || out=""
  fi
  if echo "$out" | python3 -c 'import json,sys; json.load(sys.stdin)' 2>/dev/null; then
    echo "$out"; exit 0
  fi
  [ "$METHOD" = GET ] || { echo "$out"; exit 1; }
  sleep 3
done
echo "API 回應不是合法 JSON：$out" >&2; exit 1
