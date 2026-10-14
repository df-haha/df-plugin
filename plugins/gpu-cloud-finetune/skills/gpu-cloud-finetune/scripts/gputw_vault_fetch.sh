#!/usr/bin/env bash
# gputw /vault 免開機下載（從實戰腳本改寫；本份用假 API 測過，實戰原檔 2026-10-07 在真機上跑過）
# 機器停了也能用：直接打 API 把 /vault 裡的資料夾整個抓回本機。
# 做法：遞迴列目錄 → 逐檔下載 → 每檔比對大小 → 有 required_artifacts.sha256 就驗指紋。
#
# 用法：
#   export GPUTW_TOKEN=...      # 這把金鑰要有 vault:read 權限，否則回 "missing required scope: vault:read"
#   ./gputw_vault_fetch.sh <vault 裡的相對路徑> <本機目的資料夾>
#   ./gputw_vault_fetch.sh my_run/adapter ./adapter_from_vault
set -uo pipefail
: "${GPUTW_TOKEN:?請先 export GPUTW_TOKEN}"
SRC="${1:?vault 路徑}"; DST="${2:?本機目的資料夾}"
SRC="${SRC%/}"; DST="${DST%/}"      # 去掉結尾的 /，不然暫存資料夾會建到目的資料夾裡面
BASE="${GPUTW_API_BASE:-https://gputw.ai/api}"
TMP="$DST.tmp.$$"
mkdir -p "$TMP"
# 金鑰不放進指令參數：寫 0600 暫存檔，curl 用 -H @檔案 讀；結束時刪掉
HDR="$(umask 077 && mktemp)"; trap 'rm -f "$HDR"' EXIT
printf 'Authorization: Bearer %s\n' "$GPUTW_TOKEN" > "$HDR"
log() { printf '[%s] %s\n' "$(date '+%F %T')" "$*"; }
urlenc() { python3 -c 'import urllib.parse,sys;print(urllib.parse.quote(sys.argv[1]))' "$1"; }

LISTFAIL="$TMP/.list_failed"
list_rec() {  # 印出「大小<TAB>相對路徑」；任何一層列目錄失敗都留下 $LISTFAIL
  local p="$1" out
  out=$(curl -sS -H "@$HDR" "$BASE/vault/list?path=$(urlenc "$p")" | python3 -I -c '
import json, sys, os
d = json.load(sys.stdin)
if not d.get("success"):
    sys.exit("list 失敗: " + str(d.get("error")))
for f in d["data"]["files"]:
    name = f["name"]
    # 路徑穿越防線：拒絕空、.、..、含 / 或 \ 或 NUL 或換行或 tab 的名字
    if not isinstance(name, str) or not name or name in (".", ".."):
        sys.exit("FATAL: 不合法的檔名: " + repr(name))
    for bad in ("/", "\\", "\x00", "\n", "\t"):
        if bad in name:
            sys.exit("FATAL: 檔名含不允許的字元: " + repr(name))
    ftype = f.get("type", "")
    sz = f.get("size") or 0
    if ftype != "directory" and (not isinstance(sz, int) or sz < 0):
        sys.exit("FATAL: 不合法的 size: " + repr(sz))
    print(("D" if ftype == "directory" else "F") + "\t" + str(sz) + "\t" + name)') \
    || { echo "列目錄失敗：$p" >&2; touch "$LISTFAIL"; return 1; }
  while IFS=$'\t' read -r t sz name; do
    [ -n "$t" ] || continue
    if [ "$t" = "D" ]; then list_rec "$p/$name" || return 1
    else printf '%s\t%s\n' "$sz" "${p#"$SRC"}/$name" | sed 's#\t/#\t#'; fi
  done <<< "$out"
}

log "列出 $SRC ..."
MANIFEST="$TMP/.vault_list.tsv"
list_rec "$SRC" > "$MANIFEST"
[ -e "$LISTFAIL" ] && { log "FATAL: 有資料夾列不出來，不下載（避免只抓到一部分卻回報成功）"; exit 1; }
N=$(wc -l < "$MANIFEST"); TOTAL=$(awk -F'\t' '{s+=$1} END{print s+0}' "$MANIFEST")
log "共 $N 檔 $((TOTAL/1048576)) MB"
[ "$N" -gt 0 ] || { log "FATAL: 0 檔"; exit 1; }

REAL_TMP="$(cd "$TMP" && pwd -P)"
FAIL=0
while IFS=$'\t' read -r sz rel; do
  out="$TMP/$rel"; mkdir -p "$(dirname "$out")"
  # 路徑穿越防線（第二道）：確認解析後的路徑落在 TMP 內
  real_out="$(python3 -I -c "
import os, sys
real = os.path.realpath(sys.argv[1])
base = sys.argv[2]
if not (real == base or real.startswith(base + os.sep)):
    sys.exit('FATAL: 路徑穿越: ' + real + ' 不在 ' + base + ' 內')
print(real)" "$out" "$REAL_TMP")" || { log "FATAL: 路徑穿越偵測到 rel=$rel"; exit 1; }
  ok=0
  for try in 1 2 3; do
    rm -f "$out"
    curl -s -f --retry 3 --max-time 1800 -H "@$HDR" -o "$out" \
      "$BASE/vault/download?filename=$(urlenc "$SRC/$rel")"
    rc=$?
    got=$(stat -c %s "$out" 2>/dev/null || echo -1)
    if [ $rc -eq 0 ] && [ "$got" = "$sz" ]; then ok=1; break; fi
    log "WARN: $rel 第 $try 次 rc=$rc 大小 $got≠$sz"
    sleep 3
  done
  if [ $ok -eq 1 ]; then log "OK   $rel ($sz)"; else log "FAIL $rel"; FAIL=$((FAIL+1)); fi
done < "$MANIFEST"

if [ $FAIL -ne 0 ]; then log "FATAL: $FAIL 檔失敗，暫存留在 $TMP"; exit 2; fi
if [ -f "$TMP/required_artifacts.sha256" ]; then
  # 驗證 sha256 檔內的路徑不會讀到 TMP 之外（防路徑穿越）
  python3 -I -c "
import sys, os
manifest_paths = set()
with open(sys.argv[2]) as mf:
    for line in mf:
        parts = line.strip().split('\t', 1)
        if len(parts) == 2:
            manifest_paths.add(parts[1])
base = os.path.realpath(sys.argv[3])
with open(sys.argv[1]) as f:
    for lineno, line in enumerate(f, 1):
        line = line.rstrip('\n')
        if not line:
            continue
        if line.startswith('\\\\'):
            sys.exit('FATAL: sha256 檔第 %d 行用了跳脫模式' % lineno)
        # sha256sum 格式：hash  path 或 hash *path
        for sep in ('  ', ' *'):
            idx = line.find(sep)
            if idx > 0:
                p = line[idx+len(sep):]
                break
        else:
            sys.exit('FATAL: sha256 檔第 %d 行格式不對' % lineno)
        if os.path.isabs(p) or '..' in p.split('/'):
            sys.exit('FATAL: sha256 檔第 %d 行路徑穿越: %s' % (lineno, p))
        real = os.path.realpath(os.path.join(base, p))
        if not (real == base or real.startswith(base + os.sep)):
            sys.exit('FATAL: sha256 檔第 %d 行路徑落在 TMP 外: %s' % (lineno, p))
" "$TMP/required_artifacts.sha256" "$MANIFEST" "$REAL_TMP" \
    || { log "FATAL: sha256 檔路徑驗證失敗，暫存留在 $TMP"; exit 3; }
  if (cd "$TMP" && sha256sum -c required_artifacts.sha256 --quiet); then log "sha256 驗證 PASS"
  else log "FATAL: sha256 驗證失敗，暫存留在 $TMP"; exit 3; fi
else
  log "WARN: 沒有 required_artifacts.sha256，只比對了大小"
fi
[ -e "$DST" ] && { log "FATAL: 目的資料夾已存在 $DST，不覆蓋"; exit 4; }
mv "$TMP" "$DST" && log "完成 → $DST"
