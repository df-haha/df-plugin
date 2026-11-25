#!/usr/bin/env bash
# devin-run.sh 回歸測試：唯讀檢查抓不抓得到改動、腳本自己跑的 git 會不會執行代理埋的指令。
# 只在本機跑（CI 只跑 *.test.js）：bash plugins/devin-cli/tests/devin-run.test.sh
# 對別的版本跑（例如確認舊版會失敗）：DEVIN_RUN=<devin-run.sh 路徑> bash plugins/devin-cli/tests/devin-run.test.sh
# 不會呼叫真的 devin：PATH 最前面放假的 devin，每次呼叫前都核對。
set -u

HERE=$(cd "$(dirname "$0")" && pwd)
SCRIPT=${DEVIN_RUN:-$HERE/../skills/devin-cli/scripts/devin-run.sh}
# 暫存資料夾建不起來就立刻停：後面的 HOME、PATH、假 devin 全都放在 ROOT 底下，ROOT 空的會寫到真實路徑
ROOT=$(mktemp -d "${TMPDIR:-/tmp}/devin-run-test.XXXXXX") || { echo "ABORT: mktemp 失敗" >&2; exit 2; }
case "$ROOT" in /?*) ;; *) echo "ABORT: ROOT 不合理：$ROOT" >&2; exit 2 ;; esac
[ -d "$ROOT" ] || { echo "ABORT: ROOT 不是資料夾" >&2; exit 2; }
trap 'rm -rf "$ROOT"' EXIT

# 與本機 git 設定隔離（本機全域設定有 filter.lfs 等，會影響結果）
export HOME="$ROOT/home" GIT_CONFIG_GLOBAL="$ROOT/home/.gitconfig" GIT_CONFIG_NOSYSTEM=1
export GIT_AUTHOR_NAME=t GIT_AUTHOR_EMAIL=t@example.invalid GIT_COMMITTER_NAME=t GIT_COMMITTER_EMAIL=t@example.invalid
unset GIT_DIR GIT_WORK_TREE GIT_INDEX_FILE GIT_EXTERNAL_DIFF GIT_PAGER
mkdir -p "$HOME" "$ROOT/bin"; : > "$GIT_CONFIG_GLOBAL"
git config --global init.defaultBranch main

# 假的 devin：依 FAKE_ACTION 在目前資料夾做事，印一行回覆，結束碼 0
cat > "$ROOT/bin/devin" <<'STUB'
#!/usr/bin/env bash
set -e
[ -z "${FAKE_ARGV:-}" ] || printf '%s\n' "$@" > "$FAKE_ARGV"
# 從 --export 的路徑找出輸出資料夾
out=""; prev=""; for a in "$@"; do [ "$prev" != "--export" ] || out=$(dirname "$a"); prev=$a; done
case "${FAKE_ACTION:-none}" in
  none) ;;
  edit) echo changed >> f.txt ;;
  edit-commit) echo changed >> f.txt; git add f.txt; git commit -qm agent ;;
  stash-reapply) git stash -q; git stash apply -q ;;
  branch) git branch agent-branch ;;
  staged-swap) echo B > f.txt; git add f.txt ;;
  untracked-edit) echo changed >> u.txt ;;
  edit-src) echo changed >> src/a.txt ;;
  skip-worktree) git update-index --skip-worktree f.txt; echo changed >> f.txt ;;
  poison-fsmonitor) printf '[core]\n\tfsmonitor = %s\n' "$FAKE_PAYLOAD" >> "$(git rev-parse --git-common-dir)/config" ;;
  poison-diffext) echo changed >> f.txt; printf '[diff]\n\texternal = %s\n' "$FAKE_PAYLOAD" >> .git/config ;;
  poison-include) printf '[core]\n\tfsmonitor = %s\n' "$FAKE_PAYLOAD" >> inc.cfg ;;
  plant-gitfile) printf 'gitdir: %s\n' "$FAKE_PAYLOAD" > .git ;;
  plant-hook) printf '#!/bin/sh\ntouch /nonexistent\n' > .git/hooks/pre-commit ;;
  reject) echo "warning: rejected a tool call that requires confirmation" >&2 ;;
  untrusted) echo "Error: Refusing to run in an untrusted workspace" >&2; exit 1 ;;
  plant-hookspath) printf '#!/bin/sh\ntouch /nonexistent\n' > "$FAKE_PAYLOAD/pre-commit"; chmod +x "$FAKE_PAYLOAD/pre-commit" ;;
  plant-outdir) printf '#!/bin/sh\necho pwned\n' > "$out/planted.sh" ;;
  outdir-export-dir) mkdir "$out/export.json"; printf '#!/bin/sh\necho pwned\n' > "$out/export.json/planted.sh" ;;
  outdir-tmp-dir) rm -f "$out/answer.md.tmp"; mkdir "$out/answer.md.tmp" ;;
  chmod-x) chmod +x "$FAKE_PAYLOAD" ;;
  edit-hook-target) echo "echo changed" >> scripts/hook.sh ;;
  poison-exit124) printf '[core]\n\tfsmonitor = %s\n' "$FAKE_PAYLOAD" >> .git/config; echo partial; exit 124 ;;
  *) echo "unknown FAKE_ACTION" >&2; exit 99 ;;
esac
echo "fake answer"
STUB
chmod +x "$ROOT/bin/devin"
export PATH="$ROOT/bin:$PATH"
echo "review this" > "$ROOT/prompt.md"

PASS=0; FAILS=(); OUTN=0
check() { # check <名稱> <條件說明> <0=通過>
  if [ "$3" -eq 0 ]; then PASS=$((PASS+1)); echo "ok   $1: $2"; else FAILS+=("$1"); echo "FAIL $1: $2"; fi
}
expect_rc()      { local r=0; [ "$RC" -eq "$2" ] || r=1; check "$1" "exit=$RC (want $2)" "$r"; }
expect_absent()  { if [ -e "$2" ]; then check "$1" "marker present (want absent)" 1; else check "$1" "marker absent" 0; fi; }
expect_not_run() { local r=0; [ ! -e "$2" ] || r=1; check "$1" "fake devin 沒被呼叫" "$r"; }
expect_line()    { local r=0; printf '%s\n' "$OUT" | grep -qF -- "$2" || r=1; check "$1" "output has '$2'" "$r"; }

# 跑包裝腳本：run <模式> <工作目錄> [其他參數]；其他參數放在 -C 與 -o 之間；結果在 RC、OUT
run() {
  local mode=$1 dir=$2; shift 2
  OUTN=$((OUTN + 1))
  if [ "$(command -v devin)" != "$ROOT/bin/devin" ]; then echo "ABORT: devin 不是假的那支" >&2; exit 2; fi
  OUT=$(cd "$dir" && timeout 120 bash "$SCRIPT" "$mode" "$ROOT/prompt.md" -C "$dir" "$@" -o "$ROOT/out-$OUTN" 2>&1); RC=$?
}
new_repo() { # 建一個有一筆 commit 的 repo，印路徑
  local d="$ROOT/repo-$1"
  git init -q "$d"; echo a > "$d/f.txt"; git -C "$d" add f.txt; git -C "$d" commit -qm init
  echo "$d"
}
# shellcheck disable=SC2016  # ${1...} 要留給產生的腳本展開
payload() { # 會 touch 記號檔的腳本，印腳本路徑
  printf '#!/bin/sh\ntouch %s\ncat "${1:-/dev/null}" 2>/dev/null\nexit 0\n' "$ROOT/marker-$1" > "$ROOT/payload-$1.sh"
  chmod +x "$ROOT/payload-$1.sh"; echo "$ROOT/payload-$1.sh"
}

# 1. ask，什麼都沒做
R=$(new_repo c1); FAKE_ACTION=none run ask "$R"
expect_rc c1-ask-noop 0

# 2. review，改了追蹤中的檔
R=$(new_repo c2); FAKE_ACTION=edit run review "$R"
expect_rc c2-review-edit 5

# 3. review，改檔後 commit（status 又變乾淨）
R=$(new_repo c3); FAKE_ACTION=edit-commit run review "$R"
expect_rc c3-review-commit 5

# 4a. review，stash 後再套回（工作目錄看起來一樣，多一筆 stash）
R=$(new_repo c4a); echo pre >> "$R/f.txt"; FAKE_ACTION=stash-reapply run review "$R"
expect_rc c4a-review-stash 5
# 4b. review，開新分支
R=$(new_repo c4b); FAKE_ACTION=branch run review "$R"
expect_rc c4b-review-branch 5

# 5. edit，在 .git/config 寫入 core.fsmonitor
R=$(new_repo c5); P=$(payload c5); FAKE_ACTION=poison-fsmonitor FAKE_PAYLOAD=$P run edit "$R"
expect_absent c5-edit-fsmonitor-marker "$ROOT/marker-c5"; expect_rc c5-edit-fsmonitor-rc 6
expect_line   c5-edit-fsmonitor-warn "devin-run: git_config_changed=1"

# 6. edit，改檔並設 diff.external
R=$(new_repo c6); P=$(payload c6); FAKE_ACTION=poison-diffext FAKE_PAYLOAD=$P run edit "$R"
expect_absent c6-edit-diffext-marker "$ROOT/marker-c6"; expect_rc c6-edit-diffext-rc 6
expect_line   c6-edit-diffext-warn "devin-run: git_config_changed=1"

# 7. 有工具呼叫被拒
R=$(new_repo c7); FAKE_ACTION=reject run ask "$R"
expect_rc c7-rejected 3

# 8. 執行前設定就已被下毒（上一輪留下的）：腳本自己的 git 呼叫不得執行
R=$(new_repo c8a); P=$(payload c8a); git -C "$R" config core.fsmonitor "$P"
FAKE_ACTION=none run ask "$R"
expect_absent c8a-prepoisoned-fsmonitor "$ROOT/marker-c8a"; expect_rc c8a-prepoisoned-fsmonitor-rc 0
R=$(new_repo c8b); P=$(payload c8b); git -C "$R" config diff.external "$P"; echo pre >> "$R/f.txt"
FAKE_ACTION=none run ask "$R"
expect_absent c8b-prepoisoned-diffext "$ROOT/marker-c8b"; expect_rc c8b-prepoisoned-diffext-rc 0
R=$(new_repo c8c); P=$(payload c8c); git -C "$R" config diff.tc.textconv "$P"
echo 'f.txt diff=tc' > "$R/.git/info/attributes"; echo pre >> "$R/f.txt"
FAKE_ACTION=none run ask "$R"
expect_absent c8c-prepoisoned-textconv "$ROOT/marker-c8c"; expect_rc c8c-prepoisoned-textconv-rc 0
R=$(new_repo c8d); P=$(payload c8d); git -C "$R" config filter.ev.clean "$P"; git -C "$R" config filter.ev.required true
echo 'f.txt filter=ev' > "$R/.git/info/attributes"; echo pre >> "$R/f.txt"
FAKE_ACTION=none run ask "$R"
expect_absent c8d-prepoisoned-filter "$ROOT/marker-c8d"; expect_rc c8d-prepoisoned-filter-rc 0

# 9. .git/config include 工作目錄裡的檔，代理改那個檔（.git/config 本身沒變）
R=$(new_repo c9); P=$(payload c9); : > "$R/inc.cfg"; git -C "$R" config include.path ../inc.cfg
FAKE_ACTION=poison-include FAKE_PAYLOAD=$P run edit "$R"
expect_absent c9-include-marker "$ROOT/marker-c9"; expect_rc c9-include-rc 6
expect_line   c9-include-warn "devin-run: git_config_changed=1"

# 10. 工作目錄是子資料夾，代理在裡面放 .git 檔指到別的 repo（該 repo 設了 fsmonitor）
R=$(new_repo c10); mkdir "$R/sub"; echo s > "$R/sub/s.txt"; git -C "$R" add sub; git -C "$R" commit -qm sub
P=$(payload c10); git init -q "$ROOT/evil-c10"; git -C "$ROOT/evil-c10" config core.fsmonitor "$P"
FAKE_ACTION=plant-gitfile FAKE_PAYLOAD="$ROOT/evil-c10/.git" run edit "$R/sub"
expect_absent c10-subdir-gitfile-marker "$ROOT/marker-c10"

# 11. 連結 worktree：commit 要抓到；改共用設定要停
R=$(new_repo c11); git -C "$R" worktree add -q "$ROOT/wt-c11" -b wt 2>/dev/null
FAKE_ACTION=edit-commit run review "$ROOT/wt-c11"
expect_rc c11-worktree-commit 5
P=$(payload c11); FAKE_ACTION=poison-fsmonitor FAKE_PAYLOAD=$P run edit "$ROOT/wt-c11"
expect_absent c11-worktree-fsmonitor-marker "$ROOT/marker-c11"; expect_rc c11-worktree-fsmonitor-rc 6
expect_line   c11-worktree-fsmonitor-warn "devin-run: git_config_changed=1"

# 12. 只改暫存區（前後 status 都是「M 」、工作目錄與暫存區之間都沒差異）
R=$(new_repo c12); echo A > "$R/f.txt"; git -C "$R" add f.txt
FAKE_ACTION=staged-swap run review "$R"
expect_rc c12-review-staged-only 5

# 13. 改了原本就未追蹤的檔
R=$(new_repo c13); echo u > "$R/u.txt"; FAKE_ACTION=untracked-edit run review "$R"
expect_rc c13-review-untracked-content 5

# 14. 放 hooks
R=$(new_repo c14); FAKE_ACTION=plant-hook run review "$R"
expect_rc c14-review-hook 6; expect_line c14-review-hook-warn "devin-run: git_config_changed=1"

# 15. 不該誤報：還沒有 commit 的 repo、HEAD 不在分支上、輸出資料夾在 repo 裡、未追蹤的 FIFO
R="$ROOT/repo-c15a"; git init -q "$R"; echo x > "$R/x.txt"; FAKE_ACTION=none run ask "$R"
expect_rc c15a-unborn 0
R=$(new_repo c15b); git -C "$R" checkout -q --detach; FAKE_ACTION=none run ask "$R"
expect_rc c15b-detached 0
R=$(new_repo c15c); if [ "$(command -v devin)" = "$ROOT/bin/devin" ]; then
  OUT=$(cd "$R" && FAKE_ACTION=none timeout 120 bash "$SCRIPT" ask "$ROOT/prompt.md" -C "$R" -o "$R/devin-out" 2>&1); RC=$?
else echo "ABORT" >&2; exit 2; fi
expect_rc c15c-outdir-inside-repo 0
R=$(new_repo c15d); mkfifo "$R/pipe"; FAKE_ACTION=none run ask "$R"
expect_rc c15d-untracked-fifo 0

# 15e. 輸出資料夾是有追蹤檔的資料夾（-o src）：裡面的改動照樣要抓到
R=$(new_repo c15e); mkdir "$R/src"; echo a > "$R/src/a.txt"; git -C "$R" add src; git -C "$R" commit -qm src
if [ "$(command -v devin)" = "$ROOT/bin/devin" ]; then
  OUT=$(cd "$R" && FAKE_ACTION=edit-src timeout 120 bash "$SCRIPT" review "$ROOT/prompt.md" -C "$R" -o "$R/src" 2>&1); RC=$?
else echo "ABORT" >&2; exit 2; fi
expect_rc c15e-outdir-is-tracked-dir 5

# 17. 先標 skip-worktree 再改檔（status 看不到）
R=$(new_repo c17); FAKE_ACTION=skip-worktree run review "$R"
expect_rc c17-review-skip-worktree 5

# 18. 工作區信任改成手動開啟
R=$(new_repo c18a); FAKE_ARGV="$ROOT/argv-c18a" FAKE_ACTION=none run ask "$R"
r=0; { [ -s "$ROOT/argv-c18a" ] && ! grep -qx -- '--respect-workspace-trust' "$ROOT/argv-c18a"; } || r=1
check c18a-no-trust-flag "沒帶 --trust 時 devin 沒收到 --respect-workspace-trust" "$r"
expect_line c18a-summary-trust0 "trust=0 "
R=$(new_repo c18b); FAKE_ARGV="$ROOT/argv-c18b" FAKE_ACTION=none run ask "$R" --trust
r=1; grep -A1 -x -- '--respect-workspace-trust' "$ROOT/argv-c18b" | tail -1 | grep -qx false && r=0
check c18b-trust-flag "帶 --trust（放在 -C 與 -o 之間）時 devin 收到 --respect-workspace-trust false" "$r"
expect_rc c18b-trust-rc 0; expect_line c18b-summary-trust1 "trust=1 "
R=$(new_repo c18c)
if [ "$(command -v devin)" = "$ROOT/bin/devin" ]; then
  OUT=$(cd "$R" && FAKE_ARGV="$ROOT/argv-c18c" FAKE_ACTION=none timeout 120 bash "$SCRIPT" ask "$ROOT/prompt.md" -C "$R" -m some-model --trust 2>&1); RC=$?
else echo "ABORT" >&2; exit 2; fi
r=1; grep -qx -- '--respect-workspace-trust' "$ROOT/argv-c18c" && grep -qx some-model "$ROOT/argv-c18c" && r=0
check c18c-trust-flag-last "--trust 放最後也生效，-m 不受影響" "$r"
R=$(new_repo c18d); FAKE_ACTION=untrusted run review "$R"
expect_rc c18d-untrusted-rc 1; expect_line c18d-untrusted-line "devin-run: untrusted_workspace=1"

# 19. .git 設定被改＋devin 自己回非零（像逾時的 124）：仍回 6
R=$(new_repo c19); P=$(payload c19); FAKE_ACTION=poison-exit124 FAKE_PAYLOAD=$P run code "$R"
expect_rc c19-config-changed-overrides-124 6; expect_absent c19-marker "$ROOT/marker-c19"

# 20. core.hooksPath 指到 .git 以外（在 HOME 底下），代理在那裡放 hook
R=$(new_repo c20a); mkdir "$HOME/hooks-c20a"
# shellcheck disable=SC2088  # 故意寫字面的 ~，測 git 自己的展開
git -C "$R" config core.hooksPath '~/hooks-c20a'
FAKE_ACTION=plant-hookspath FAKE_PAYLOAD="$HOME/hooks-c20a" run edit "$R"
expect_rc c20a-hookspath-outside-git 6; expect_line c20a-hookspath-warn "devin-run: git_config_changed=1"
# 20b. hooksPath 指到的是符號連結資料夾
R=$(new_repo c20b); mkdir "$HOME/real-hooks-c20b"; ln -s "$HOME/real-hooks-c20b" "$HOME/hooks-c20b"
git -C "$R" config core.hooksPath "$HOME/hooks-c20b"
FAKE_ACTION=plant-hookspath FAKE_PAYLOAD="$HOME/real-hooks-c20b" run review "$R"
expect_rc c20b-hookspath-symlink 6; expect_line c20b-hookspath-symlink-warn "devin-run: git_config_changed=1"
# 20c. hooksPath 有設定、沒被動過：不誤報
R=$(new_repo c20c); mkdir "$HOME/hooks-c20c"
# shellcheck disable=SC2088
git -C "$R" config core.hooksPath '~/hooks-c20c'
FAKE_ACTION=none run review "$R"
expect_rc c20c-hookspath-untouched 0
# 20d. hooksPath 有設定但展開不了（不存在的使用者）：git 每個指令都拒跑，不能當成「不是 repo」放行
R=$(new_repo c20d)
# shellcheck disable=SC2088
git -C "$R" config core.hooksPath '~no-such-user-c20d/hooks'
FAKE_ARGV="$ROOT/argv-c20d" FAKE_ACTION=none run edit "$R"
expect_rc c20d-hookspath-unresolvable 7; expect_line c20d-hookspath-unresolvable-line "devin-run: git_unresolved=1"
expect_not_run c20d-devin-not-invoked "$ROOT/argv-c20d"
# 20e. 真的不是 git repo：照舊不檢查
mkdir "$ROOT/plain-c20e"; FAKE_ACTION=none run ask "$ROOT/plain-c20e"
expect_rc c20e-not-a-repo 0; expect_line c20e-not-a-repo-na "workdir_changed=na"

# 21. 輸出資料夾在 repo 裡，代理在裡面多放一個檔（c15c 是只有腳本自己的檔，要回 0）
R=$(new_repo c21)
if [ "$(command -v devin)" = "$ROOT/bin/devin" ]; then
  OUT=$(cd "$R" && FAKE_ACTION=plant-outdir timeout 120 bash "$SCRIPT" review "$ROOT/prompt.md" -C "$R" -o "$R/devin-out" 2>&1); RC=$?
else echo "ABORT" >&2; exit 2; fi
expect_rc c21-outdir-planted-file 5
r=1; [ -f "$R/devin-out/planted.sh" ] && r=0; check c21-planted-file-exists "假 devin 真的放了 planted.sh" "$r"

# 22a. 輸出資料夾在 repo 裡，代理把 export.json 換成資料夾並在裡面藏檔
R=$(new_repo c22a)
if [ "$(command -v devin)" = "$ROOT/bin/devin" ]; then
  OUT=$(cd "$R" && FAKE_ACTION=outdir-export-dir timeout 120 bash "$SCRIPT" review "$ROOT/prompt.md" -C "$R" -o "$R/devin-out" 2>&1); RC=$?
else echo "ABORT" >&2; exit 2; fi
expect_rc c22a-export-json-dir 5; expect_line c22a-summary-printed "workdir_changed=1 exit=5"
# 22b. 代理把 answer.md.tmp 換成資料夾：腳本的 mv 失敗也要印完結果行、回非零
R=$(new_repo c22b); FAKE_ACTION=outdir-tmp-dir run review "$R"
expect_rc c22b-tmp-dir 5; expect_line c22b-summary-printed "exit=5"; expect_line c22b-tampered-line "devin-run: output_tampered=1"
# 22c. .git 以外的 hooks 資料夾裡原本沒有執行權限的 hook，被加上執行權限
R=$(new_repo c22c); mkdir "$HOME/hooks-c22c"; printf '#!/bin/sh\nexit 0\n' > "$HOME/hooks-c22c/pre-commit"
git -C "$R" config core.hooksPath "$HOME/hooks-c22c"
FAKE_ACTION=chmod-x FAKE_PAYLOAD="$HOME/hooks-c22c/pre-commit" run review "$R"
expect_rc c22c-hookspath-chmod 6
# 22d. 同上，在 .git/hooks 裡
R=$(new_repo c22d); printf '#!/bin/sh\nexit 0\n' > "$R/.git/hooks/pre-commit"; chmod -x "$R/.git/hooks/pre-commit"
FAKE_ACTION=chmod-x FAKE_PAYLOAD="$R/.git/hooks/pre-commit" run edit "$R"
expect_rc c22d-githooks-chmod 6

# 23. filter 名稱含 =（-c 會在第一個 = 切開，覆寫失效）
R=$(new_repo c23); P=$(payload c23); git -C "$R" config 'filter.a=b.clean' "$P"
echo 'f.txt filter=a=b' > "$R/.git/info/attributes"; echo pre >> "$R/f.txt"
FAKE_ACTION=none run ask "$R"
expect_absent c23-filter-name-with-equals "$ROOT/marker-c23"; expect_rc c23-filter-name-with-equals-rc 0

# 24. .git/hooks 裡的符號連結指到工作目錄的腳本，代理改那個腳本
R=$(new_repo c24); mkdir "$R/scripts"; printf '#!/bin/sh\nexit 0\n' > "$R/scripts/hook.sh"; chmod +x "$R/scripts/hook.sh"
git -C "$R" add scripts; git -C "$R" commit -qm hook; ln -s ../../scripts/hook.sh "$R/.git/hooks/pre-commit"
FAKE_ACTION=edit-hook-target run edit "$R"
expect_rc c24-hook-symlink-target-edit 6

# 25. -o 裡已經有輸出檔：拒跑、不覆寫
R=$(new_repo c25a); mkdir "$ROOT/out-c25a"; echo old > "$ROOT/out-c25a/answer.md"
if [ "$(command -v devin)" = "$ROOT/bin/devin" ]; then
  OUT=$(cd "$R" && FAKE_ARGV="$ROOT/argv-c25a" FAKE_ACTION=none timeout 120 bash "$SCRIPT" ask "$ROOT/prompt.md" -C "$R" -o "$ROOT/out-c25a" 2>&1); RC=$?
else echo "ABORT" >&2; exit 2; fi
expect_rc c25a-existing-answer 2; expect_not_run c25a-devin-not-invoked "$ROOT/argv-c25a"
r=1; [ "$(cat "$ROOT/out-c25a/answer.md")" = old ] && r=0; check c25a-answer-unchanged "answer.md 沒被覆寫" "$r"
R=$(new_repo c25b); mkdir "$ROOT/out-c25b"; echo keep > "$ROOT/target-c25b"; ln -s "$ROOT/target-c25b" "$ROOT/out-c25b/stderr.log"
if [ "$(command -v devin)" = "$ROOT/bin/devin" ]; then
  OUT=$(cd "$R" && FAKE_ARGV="$ROOT/argv-c25b" FAKE_ACTION=none timeout 120 bash "$SCRIPT" ask "$ROOT/prompt.md" -C "$R" -o "$ROOT/out-c25b" 2>&1); RC=$?
else echo "ABORT" >&2; exit 2; fi
expect_rc c25b-existing-stderr-symlink 2; expect_not_run c25b-devin-not-invoked "$ROOT/argv-c25b"
r=1; [ "$(cat "$ROOT/target-c25b")" = keep ] && r=0; check c25b-symlink-target-unchanged "符號連結指到的檔沒被動" "$r"

# 26. 參數：-- 之後的 --trust 不算、多出來的參數、-t 不是正整數
R=$(new_repo c26)
FAKE_ARGV="$ROOT/argv-c26a" FAKE_ACTION=none run ask "$R" -- --trust
expect_rc c26a-dashdash-trust 2; expect_not_run c26a-devin-not-invoked "$ROOT/argv-c26a"
FAKE_ARGV="$ROOT/argv-c26b" FAKE_ACTION=none run ask "$R" -t --help
expect_rc c26b-timeout-help 2; expect_not_run c26b-devin-not-invoked "$ROOT/argv-c26b"
FAKE_ARGV="$ROOT/argv-c26c" FAKE_ACTION=none run ask "$R" -t 0
expect_rc c26c-timeout-zero 2; expect_not_run c26c-devin-not-invoked "$ROOT/argv-c26c"

# 27. 測試本身：暫存資料夾建不起來時立刻停，不往外寫任何東西
mkdir "$ROOT/f-cwd" "$ROOT/f-home"
(cd "$ROOT/f-cwd" && HOME="$ROOT/f-home" TMPDIR=/nonexistent bash "$HERE/$(basename "$0")" >/dev/null 2>&1); r=$?
check c27-harness-mktemp-fail-rc "TMPDIR 不存在時測試以非零結束（rc=$r）" "$([ "$r" -ne 0 ] && echo 0 || echo 1)"
r=0; [ -z "$(ls -A "$ROOT/f-cwd")" ] && [ -z "$(ls -A "$ROOT/f-home")" ] || r=1
check c27-harness-wrote-nothing "目前資料夾與 HOME 都沒被寫入" "$r"

# 16. 未追蹤檔太多：只比對檔名並註明
R=$(new_repo c16); mkdir "$R/many"; (cd "$R/many" && touch f{0001..2001}); FAKE_ACTION=none run ask "$R"
expect_rc c16-many-untracked 0; expect_line c16-many-untracked-note "devin-run: untracked_hash_skipped=2001"

echo
echo "script: $SCRIPT"
echo "passed: $PASS  failed: ${#FAILS[@]}${FAILS[*]:+ (${FAILS[*]})}"
[ "${#FAILS[@]}" -eq 0 ]
