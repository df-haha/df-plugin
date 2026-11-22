#!/usr/bin/env bash
# devin-run.sh — 以非互動模式呼叫 devin CLI，固定帶安全旗標並檢查「靜默失敗」。
# 用法：devin-run.sh <ask|review|edit|code> <prompt 檔> [--trust] [-C 工作目錄] [-o 輸出資料夾] [-m 模型] [-r session_id] [-t 逾時秒數]
# --trust：略過 devin 的工作區信任檢查（帶 --respect-workspace-trust false）。只用在使用者自己的 repo；不帶時未受信任的資料夾會被 devin 拒跑。
# 結束碼：0 成功｜2 用法錯誤｜3 有工具呼叫被拒（可能只做一半，讀 answer.md 判斷）｜4 沒有回覆內容｜5 唯讀模式卻改動了工作目錄｜6 .git 的設定或 hooks 被改（任何模式，蓋掉其他結束碼；先別跑 git）｜124 逾時｜其他＝devin 自己的結束碼
set -euo pipefail

# 模式 → 權限（auto 只讀；accept-edits 可改檔不可跑指令；smart 可改檔＋跑快速模型判定安全的指令）
# review 用 smart：GPT 系模型沒有讀檔工具、靠 shell 讀檔，auto 會拒（2026-11-22 實測）。唯讀靠下方的改動檢查把關。
declare -A PERMISSION=( [ask]=auto [review]=smart [edit]=accept-edits [code]=smart )
declare -A READONLY=( [ask]=1 [review]=1 [edit]=0 [code]=0 )
# 模式 → 預設模型（改預設只改這張表；清單見 references/models.md）
declare -A DEFAULT_MODEL=( [ask]=swe-2-medium [review]=gpt-6-2-sol-xhigh [edit]=swe-2-high [code]=swe-2-high )

usage() { sed -n '2,5p' "$0" | sed 's/^# //'; exit 2; }

[ $# -ge 2 ] || usage
MODE=$1; PROMPT_FILE=$2; shift 2
[ -n "${PERMISSION[$MODE]:-}" ] || { echo "未知模式：$MODE" >&2; usage; }
[ -r "$PROMPT_FILE" ] || { echo "讀不到 prompt 檔：$PROMPT_FILE" >&2; exit 2; }
PROMPT_FILE=$(realpath "$PROMPT_FILE")

# --trust 可放在其他選項之間任何位置；先抽出來，其餘交給 getopts（選項的值剛好是 --trust 時不抽）
TRUST=0; REST=()
while [ $# -gt 0 ]; do
  case $1 in
    --trust) TRUST=1 ;;
    -[Comrt]) REST+=( "$1" ); if [ $# -ge 2 ]; then REST+=( "$2" ); shift; fi ;;
    *) REST+=( "$1" ) ;;
  esac
  shift
done
set -- ${REST[@]+"${REST[@]}"}

WORKDIR=$PWD; OUTDIR=""; MODEL=${DEFAULT_MODEL[$MODE]}; RESUME=""; TIMEOUT=1800
while getopts "C:o:m:r:t:" opt; do
  case $opt in
    C) WORKDIR=$OPTARG ;; o) OUTDIR=$OPTARG ;; m) MODEL=$OPTARG ;;
    r) RESUME=$OPTARG ;; t) TIMEOUT=$OPTARG ;; *) usage ;;
  esac
done
[ -d "$WORKDIR" ] || { echo "工作目錄不存在：$WORKDIR" >&2; exit 2; }
[ -n "$OUTDIR" ] || OUTDIR=$(mktemp -d "${TMPDIR:-/tmp}/devin-run.XXXXXX")
mkdir -p "$OUTDIR"; OUTDIR=$(realpath "$OUTDIR")

ARGS=( -p --prompt-file "$PROMPT_FILE"
       --model "$MODEL"
       --permission-mode "${PERMISSION[$MODE]}"
       --process-model in-process
       --export "$OUTDIR/export.json" )
[ "$TRUST" -eq 0 ] || ARGS+=( --respect-workspace-trust false )
[ -z "$RESUME" ] || ARGS+=( -r "$RESUME" )

cd "$WORKDIR"
SCRATCH=$(mktemp -d "${TMPDIR:-/tmp}/devin-run-fp.XXXXXX"); trap 'rm -rf "$SCRATCH"' EXIT

# ── 工作目錄改動檢查（唯讀模式結束後比對）──────────────────────────────
# 代理改得到 .git/config、hooks、.gitattributes。腳本自己跑的 git 若照單全收，等於替代理執行任意指令，
# 所以：(1) 每個 git 呼叫都關掉已知的執行點（fsmonitor、外部 diff、textconv、filter、pager、hooks、簽章驗證）；
# (2) repo 位置在執行前解析一次，之後用 GIT_DIR／GIT_WORK_TREE 釘住，不讓代理放的 .git 檔改指別的 repo；
# (3) 執行後先不用 git 比對設定檔與 hooks，有變就不再跑任何 git，直接當成有改動。
MAX_UNTRACKED_FILES=2000        # 未追蹤檔超過這個數量就只比對檔名
MAX_HASH_BYTES=$((50*1024*1024)) # 單檔超過這個大小只比對大小與修改時間
GIT_SAFE=( --no-pager -c core.fsmonitor=false -c core.hooksPath=/dev/null -c core.pager=cat
           -c diff.external= -c log.showSignature=false -c status.submoduleSummary=false )
FILTER_OFF=(); PIN_GIT_DIR=""; PIN_WORK_TREE=""; COMMON_DIR=""; PATHSPEC=(); HOOKS_DIR=""; HOOKS_UNRESOLVED=0
g() {
  local -a pin=()
  [ -z "$PIN_GIT_DIR" ] || pin=( "GIT_DIR=$PIN_GIT_DIR" "GIT_WORK_TREE=$PIN_WORK_TREE" )
  env ${pin[@]+"${pin[@]}"} GIT_OPTIONAL_LOCKS=0 GIT_TERMINAL_PROMPT=0 \
    git "${GIT_SAFE[@]}" ${FILTER_OFF[@]+"${FILTER_OFF[@]}"} "$@"
}

# .git 裡會影響 git 行為、但 git status 看不到的檔：設定檔、hooks、info/（exclude、attributes）、
# 連結 worktree 的指向檔、子模組的設定與 hooks。只用 find／sha256sum，不呼叫 git。
hash_path() {
  if [ ! -e "$1" ] && [ ! -L "$1" ]; then echo "absent $1"; return 0; fi
  find "$1" -name objects -prune -o -type f -exec sha256sum {} + -o -printf '%y %p %l\n' 2>&1 || echo "find-failed $1"
}
# core.hooksPath 指到的資料夾：跟著符號連結讀（find 會偵測迴圈），深度有上限。另記資料夾本身實際指到哪裡。
hash_path_follow() {
  if [ ! -e "$1" ] && [ ! -L "$1" ]; then echo "absent $1"; return 0; fi
  printf 'target %s -> %s\n' "$1" "$(realpath -m -- "$1")"
  find -L "$1" -maxdepth 4 -type f -exec sha256sum {} + -o -printf '%y %p\n' 2>&1 || echo "find-failed $1"
}
meta_fingerprint() {
  {
    local p
    [ -z "$HOOKS_DIR" ] || hash_path_follow "$HOOKS_DIR"
    for p in "$COMMON_DIR/config" "$COMMON_DIR/config.worktree" "$COMMON_DIR/hooks" \
             "$PIN_GIT_DIR/config.worktree" "$PIN_GIT_DIR/commondir"; do hash_path "$p"; done
    # info/ 只看會影響 git 判讀的檔（info/refs 會被 gc 自動改寫，不看）
    for p in "$COMMON_DIR" "$PIN_GIT_DIR"; do
      for f in attributes exclude sparse-checkout grafts; do hash_path "$p/info/$f"; done
    done
    if [ -f "$PIN_WORK_TREE/.git" ] || [ -L "$PIN_WORK_TREE/.git" ]; then hash_path "$PIN_WORK_TREE/.git"; fi
    if [ -d "$COMMON_DIR/modules" ]; then
      find "$COMMON_DIR/modules" -name objects -prune -o \( -name config -o -name commondir -o -path '*/hooks/*' -o -path '*/info/attributes' -o -path '*/info/exclude' \) \
        \( -type f -exec sha256sum {} + -o -printf '%y %p %l\n' \) 2>&1 || echo "find-failed modules"
    fi
  } | LC_ALL=C sort | sha256sum | cut -d" " -f1
}
# 生效中的全部設定（含 include 進來的檔、全域設定）。git config 只讀設定、不執行任何東西。
config_fingerprint() { g config --list --show-origin --show-scope -z | sha256sum | cut -d" " -f1; }

# 指紋：status、未暫存與已暫存 diff、HEAD、目前分支、所有 ref、stash、未追蹤檔內容。
# HEAD 尚無 commit、HEAD 不在分支上是正常狀態，只記結束碼；其他 git 指令失敗就讓整個指紋失敗（失效時從嚴）。
fingerprint() {
  (
    cd "$PIN_WORK_TREE" || exit 1
    fail=0
    echo "## status";   g status --porcelain=v1 --untracked-files=all --ignore-submodules=dirty -- ${PATHSPEC[@]+"${PATHSPEC[@]}"} || fail=1
    echo "## diff";     g diff --no-ext-diff --no-textconv --binary --submodule=short --ignore-submodules=dirty -- ${PATHSPEC[@]+"${PATHSPEC[@]}"} || fail=1
    echo "## cached";   g diff --cached --no-ext-diff --no-textconv --binary --submodule=short --ignore-submodules=dirty -- ${PATHSPEC[@]+"${PATHSPEC[@]}"} || fail=1
    echo "## head";     g rev-parse --verify -q HEAD || echo "rc=$?"
    echo "## branch";   g symbolic-ref -q HEAD || echo "rc=$?"
    echo "## refs";     g for-each-ref --format='%(objectname) %(refname)' || fail=1
    echo "## stash";    g stash list || fail=1
    echo "## flags";    g ls-files -v | { grep -v '^H ' || true; } || fail=1   # skip-worktree／assume-unchanged 會讓 status 看不到改動
    echo "## untracked"
    if g ls-files -z -o --exclude-standard -- ${PATHSPEC[@]+"${PATHSPEC[@]}"} > "$SCRATCH/untracked"; then
      n=$(tr -cd '\0' < "$SCRATCH/untracked" | wc -c)
      if [ "$n" -gt "$MAX_UNTRACKED_FILES" ]; then
        echo "$n" > "$SCRATCH/untracked_skipped"; cat "$SCRATCH/untracked"
      else
        # 只讀一般檔；符號連結記指向、其他類型（FIFO 等）只記檔名，避免讀到卡住
        while IFS= read -r -d '' p; do
          if [ -L "$p" ]; then printf 'l %s -> %s\n' "$p" "$(readlink -- "$p")"
          elif [ -f "$p" ]; then
            size=$(stat -c %s -- "$p")
            if [ "$size" -gt "$MAX_HASH_BYTES" ]; then printf 'b %s %s %s\n' "$p" "$size" "$(stat -c %Y -- "$p")"
            else printf 'f %s ' "$p"; sha256sum < "$p"; fi
          else printf 'o %s\n' "$p"; fi
        done < "$SCRATCH/untracked"
      fi
    else fail=1; fi
    exit "$fail"
  ) | sha256sum | cut -d" " -f1
}

# 是 git 工作目錄卻解析不到 .git 位置（例如很舊的 git），或 git 因設定錯誤、擁有者檢查等原因整個拒跑
# （訊息不是「not a git repository」）時，一律當成有改動（失效時從嚴）
IS_GIT=0; GIT_UNRESOLVED=0
INSIDE=$(LC_ALL=C g rev-parse --is-inside-work-tree 2>"$SCRATCH/rev-parse.err") || INSIDE=error
if [ "$INSIDE" = error ] && ! command grep -q 'not a git repository' "$SCRATCH/rev-parse.err"; then GIT_UNRESOLVED=1
elif [ "$INSIDE" = true ]; then
  if GIT_INFO=$(g rev-parse --git-dir --git-common-dir --show-toplevel 2>/dev/null); then
    mapfile -t GIT_LINES <<< "$GIT_INFO"
    if [ "${#GIT_LINES[@]}" -eq 3 ] && PIN_GIT_DIR=$(realpath -e -- "${GIT_LINES[0]}") \
       && COMMON_DIR=$(realpath -e -- "${GIT_LINES[1]}") && PIN_WORK_TREE=$(realpath -e -- "${GIT_LINES[2]}") \
       && [ -d "$PIN_GIT_DIR" ] && [ -d "$COMMON_DIR" ] && [ -d "$PIN_WORK_TREE" ]; then IS_GIT=1; fi
  fi
  [ "$IS_GIT" -eq 1 ] || { GIT_UNRESOLVED=1; PIN_GIT_DIR=""; COMMON_DIR=""; PIN_WORK_TREE=""; }
fi
if [ "$IS_GIT" -eq 1 ]; then
  # 輸出資料夾在 repo 裡時，只排除腳本自己寫的四個檔；資料夾裡出現其他檔照樣算改動
  case "$OUTDIR/" in "$PIN_WORK_TREE"/*)
    OUT_REL=${OUTDIR#"$PIN_WORK_TREE"}; OUT_REL=${OUT_REL#/}
    PATHSPEC=( ":(top)" )
    for f in answer.md answer.md.tmp stderr.log export.json; do
      PATHSPEC+=( ":(top,exclude,literal)${OUT_REL:+$OUT_REL/}$f" )
    done ;;
  esac
  # core.hooksPath 若指到 .git 以外，代理可在那裡埋 hooks，使用者之後 commit 時才執行。
  # 腳本自己的 git 已用 -c 蓋掉它，所以讀設定時排除指令列層（command）的值；相對路徑以工作目錄根為準（git 的規則）。
  # 有設定卻解析不出路徑時，執行後一律當成設定被改（失效時從嚴）。
  HOOKS_RC=0; HOOKS_LIST=$(g config --type=path --show-scope --get-all core.hooksPath 2>/dev/null) || HOOKS_RC=$?
  if [ "$HOOKS_RC" -eq 0 ]; then
    HOOKS_RAW=$(printf '%s\n' "$HOOKS_LIST" | awk -F'\t' '$1 != "command" { v = substr($0, index($0, "\t") + 1) } END { print v }')
    if [ -n "$HOOKS_RAW" ]; then
      case "$HOOKS_RAW" in /*) ;; *) HOOKS_RAW="$PIN_WORK_TREE/$HOOKS_RAW" ;; esac
      HOOKS_DIR=$(realpath -m -s -- "$HOOKS_RAW") || { HOOKS_DIR=""; HOOKS_UNRESOLVED=1; }
    fi
  elif [ "$HOOKS_RC" -ne 1 ]; then HOOKS_UNRESOLVED=1   # 1＝沒設定；其他＝讀取或展開失敗
  fi
  # 設定裡定義的每個 filter 都清成空指令：代理可在 .gitattributes 把任一 filter 套到檔案上
  declare -A SEEN_FILTER=()
  while IFS= read -r key; do
    name=${key#filter.}; name=${name%.*}
    [ -z "${SEEN_FILTER[$name]:-}" ] || continue
    SEEN_FILTER[$name]=1
    FILTER_OFF+=( -c "filter.$name.clean=" -c "filter.$name.smudge=" -c "filter.$name.process=" -c "filter.$name.required=false" )
  done < <(g config --name-only --get-regexp '^filter\..*\.(clean|smudge|process)$' 2>/dev/null || true)
  META_BEFORE=$(meta_fingerprint) || META_BEFORE=error-before
  CFG_BEFORE=$(config_fingerprint) || CFG_BEFORE=error-before
  BEFORE=$(fingerprint) || BEFORE=error-before
fi
RC=0
timeout "$TIMEOUT" devin "${ARGS[@]}" > "$OUTDIR/answer.md.tmp" 2> "$OUTDIR/stderr.log" || RC=$?
mv "$OUTDIR/answer.md.tmp" "$OUTDIR/answer.md"

REJECTED=$(command grep -c 'rejected a tool call' "$OUTDIR/stderr.log" || true)
if [ "$RC" -eq 0 ] && [ "$REJECTED" -gt 0 ]; then RC=3; fi
if [ "$RC" -eq 0 ] && [ ! -s "$OUTDIR/answer.md" ]; then RC=4; fi
# 沒帶 --trust、資料夾又沒被信任過：devin 自己回非零，保留它的結束碼，只加提示
UNTRUSTED=0; ! command grep -q 'Refusing to run in an untrusted workspace' "$OUTDIR/stderr.log" || UNTRUSTED=1
CHANGED=na; GIT_CONFIG_CHANGED=0
if [ "$GIT_UNRESOLVED" -eq 1 ]; then
  CHANGED=1
  if [ "${READONLY[$MODE]}" -eq 1 ]; then RC=5; fi
elif [ "$IS_GIT" -eq 1 ]; then
  CHANGED=0
  if [ "$HOOKS_UNRESOLVED" -eq 1 ] || [ "$META_BEFORE" != "$(meta_fingerprint)" ]; then GIT_CONFIG_CHANGED=1
  else
    CFG_AFTER=$(config_fingerprint) || CFG_AFTER=error-after
    [ "$CFG_BEFORE" = "$CFG_AFTER" ] || GIT_CONFIG_CHANGED=1
  fi
  if [ "$GIT_CONFIG_CHANGED" -eq 1 ]; then CHANGED=1   # 設定可能已被下毒：不再跑任何 git
  else
    AFTER=$(fingerprint) || AFTER=error-after
    [ "$BEFORE" = "$AFTER" ] || CHANGED=1
  fi
  if [ "${READONLY[$MODE]}" -eq 1 ] && [ "$CHANGED" -eq 1 ]; then RC=5; fi
  # .git 設定或 hooks 被改：任何模式都回 6，蓋掉其他結束碼（之後跑 git 可能執行被埋的程式）
  if [ "$GIT_CONFIG_CHANGED" -eq 1 ]; then RC=6; fi
fi

echo "devin-run: mode=$MODE model=$MODEL permission=${PERMISSION[$MODE]} trust=$TRUST workdir=$WORKDIR"
echo "devin-run: answer=$OUTDIR/answer.md stderr=$OUTDIR/stderr.log export=$OUTDIR/export.json"
[ "$GIT_CONFIG_CHANGED" -eq 0 ] || echo "devin-run: git_config_changed=1"
[ "$GIT_UNRESOLVED" -eq 0 ] || echo "devin-run: git_unresolved=1"
[ "$HOOKS_UNRESOLVED" -eq 0 ] || echo "devin-run: hooks_path_unresolved=1"
if [ "$UNTRUSTED" -eq 1 ]; then
  echo "devin-run: untrusted_workspace=1"
  echo "devin-run: 提示：devin 拒跑未受信任的工作目錄。是使用者自己的 repo、或使用者當輪同意，才加 --trust 重跑；外部來源的程式碼不要直接加。"
fi
[ ! -s "$SCRATCH/untracked_skipped" ] || echo "devin-run: untracked_hash_skipped=$(cat "$SCRATCH/untracked_skipped")"
echo "devin-run: rejected_tool_calls=$REJECTED workdir_changed=$CHANGED exit=$RC"
exit "$RC"
