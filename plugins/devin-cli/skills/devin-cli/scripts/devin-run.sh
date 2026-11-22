#!/usr/bin/env bash
# devin-run.sh — 以非互動模式呼叫 devin CLI，固定帶安全旗標並檢查「靜默失敗」。
# 用法：devin-run.sh <ask|review|edit|code> <prompt 檔> [-C 工作目錄] [-o 輸出資料夾] [-m 模型] [-r session_id] [-t 逾時秒數]
# 結束碼：0 成功｜2 用法錯誤｜3 有工具呼叫被拒（可能只做一半，讀 answer.md 判斷）｜4 沒有回覆內容｜5 唯讀模式卻改動了工作目錄｜124 逾時｜其他＝devin 自己的結束碼
set -euo pipefail

# 模式 → 權限（auto 只讀；accept-edits 可改檔不可跑指令；smart 可改檔＋跑快速模型判定安全的指令）
# review 用 smart：GPT 系模型沒有讀檔工具、靠 shell 讀檔，auto 會拒（2026-11-22 實測）。唯讀靠下方的改動檢查把關。
declare -A PERMISSION=( [ask]=auto [review]=smart [edit]=accept-edits [code]=smart )
declare -A READONLY=( [ask]=1 [review]=1 [edit]=0 [code]=0 )
# 模式 → 預設模型（改預設只改這張表；清單見 references/models.md）
declare -A DEFAULT_MODEL=( [ask]=swe-2-medium [review]=gpt-6-2-sol-xhigh [edit]=swe-2-high [code]=swe-2-high )

usage() { sed -n '2,4p' "$0" | sed 's/^# //'; exit 2; }

[ $# -ge 2 ] || usage
MODE=$1; PROMPT_FILE=$2; shift 2
[ -n "${PERMISSION[$MODE]:-}" ] || { echo "未知模式：$MODE" >&2; usage; }
[ -r "$PROMPT_FILE" ] || { echo "讀不到 prompt 檔：$PROMPT_FILE" >&2; exit 2; }
PROMPT_FILE=$(realpath "$PROMPT_FILE")

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
       --respect-workspace-trust false
       --export "$OUTDIR/export.json" )
[ -z "$RESUME" ] || ARGS+=( -r "$RESUME" )

cd "$WORKDIR"
# 工作目錄指紋：git 狀態＋未提交 diff。唯讀模式結束後比對。
fingerprint() { { git status --porcelain=v1 2>/dev/null; git diff 2>/dev/null; } | sha256sum | cut -d" " -f1; }
IS_GIT=0; git rev-parse --is-inside-work-tree >/dev/null 2>&1 && IS_GIT=1
BEFORE=""; [ "$IS_GIT" -eq 0 ] || BEFORE=$(fingerprint)
RC=0
timeout "$TIMEOUT" devin "${ARGS[@]}" > "$OUTDIR/answer.md.tmp" 2> "$OUTDIR/stderr.log" || RC=$?
mv "$OUTDIR/answer.md.tmp" "$OUTDIR/answer.md"

REJECTED=$(command grep -c 'rejected a tool call' "$OUTDIR/stderr.log" || true)
if [ "$RC" -eq 0 ] && [ "$REJECTED" -gt 0 ]; then RC=3; fi
if [ "$RC" -eq 0 ] && [ ! -s "$OUTDIR/answer.md" ]; then RC=4; fi
CHANGED=na
if [ "$IS_GIT" -eq 1 ]; then
  CHANGED=0; [ "$BEFORE" = "$(fingerprint)" ] || CHANGED=1
  if [ "${READONLY[$MODE]}" -eq 1 ] && [ "$CHANGED" -eq 1 ]; then RC=5; fi
fi

echo "devin-run: mode=$MODE model=$MODEL permission=${PERMISSION[$MODE]} workdir=$WORKDIR"
echo "devin-run: answer=$OUTDIR/answer.md stderr=$OUTDIR/stderr.log export=$OUTDIR/export.json"
echo "devin-run: rejected_tool_calls=$REJECTED workdir_changed=$CHANGED exit=$RC"
exit "$RC"
