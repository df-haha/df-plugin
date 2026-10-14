#!/usr/bin/env bash
# RunPod REST API 小包裝（從實戰腳本簡化，本份未重跑）
# 用法：
#   export RUNPOD_API_KEY=...          # 不要寫進檔案
#   ./runpod_api.sh GET    /pods
#   ./runpod_api.sh POST   /pods "$(cat pod.json)"
#   ./runpod_api.sh GET    "/pods/<id>?includeMachine=true"
#   ./runpod_api.sh POST   /pods/<id>/stop
#   ./runpod_api.sh DELETE /pods/<id>
#   ./runpod_api.sh GQL    '{ myself { clientBalance } }'     # 查餘額
set -euo pipefail
: "${RUNPOD_API_KEY:?請先 export RUNPOD_API_KEY}"
METHOD="${1:?METHOD}"; ARG="${2:?PATH or QUERY}"; BODY="${3:-}"
# 金鑰不放在指令參數裡（別人用 ps 看得到）：寫進只有自己能讀的暫存檔，curl 用 -H @檔案 讀
HDR="$(umask 077 && mktemp)"; trap 'rm -f "$HDR"' EXIT
printf 'Authorization: Bearer %s\n' "$RUNPOD_API_KEY" > "$HDR"
AUTH=(-H "@$HDR")

if [ "$METHOD" = GQL ]; then
  payload=$(python3 -c 'import json,sys; print(json.dumps({"query": sys.argv[1]}))' "$ARG")
  curl -sS https://api.runpod.io/graphql "${AUTH[@]}" -H 'Content-Type: application/json' -d "$payload"
  exit 0
fi
if [ -n "$BODY" ]; then
  curl -sS -X "$METHOD" "https://rest.runpod.io/v1$ARG" "${AUTH[@]}" \
       -H 'Content-Type: application/json' -d "$BODY"
else
  curl -sS -X "$METHOD" "https://rest.runpod.io/v1$ARG" "${AUTH[@]}"
fi
