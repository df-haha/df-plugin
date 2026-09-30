# session-resume-inventory

重開機或交接前，盤點目前開著的 Claude Code、Codex、tmux 與 Orca 工作階段（session）：對應到 repo／worktree 與分支、說明即時狀態（idle／busy／shell／open），並產出精確的 `claude --resume <id>`／`codex resume <id>` 指令。

盤點全程唯讀：不關閉、不續接、不改名任何 session。

另有關機前存快照、開機後把沒在跑的 session 開回 Orca 的復原流程（預設只預覽，確認後才開分頁）。

## 安裝

```bash
claude plugin install session-resume-inventory@df-haha-plugins
```

## 使用

自然語言觸發即可：「盤點 session」「重開機前清單」「有哪些 claude/codex 在跑」。

也可直接跑腳本：

```bash
python3 plugins/session-resume-inventory/scripts/inventory.py
```

## 關機前存快照、開機後復原

```bash
python3 plugins/session-resume-inventory/scripts/inventory.py --save   # 存到 ~/.claude/session-snapshots/
python3 plugins/session-resume-inventory/scripts/restore.py            # 預覽要開回哪些
python3 plugins/session-resume-inventory/scripts/restore.py --apply    # 確認後在 Orca 開分頁
python3 plugins/session-resume-inventory/scripts/restore.py --open-orca  # Orca 沒開時先啟動
```

- 已在跑的 session 不會重複開。
- 快照只有本人可讀（檔案 600、資料夾 700），因為會記錄啟動參數。
- 依原啟動參數復原（白名單旗標，如 `--dangerously-skip-permissions`、`--model`）；其他參數列出不帶。
- 只開回原本在 Orca、且路徑已登記在 Orca 的 session；其餘只列出指令。Windows Terminal 復原尚未支援。

## 測試

```bash
cd plugins/session-resume-inventory && python3 -m unittest discover -s tests
```

## 需求

- Linux 或 WSL（讀 `/proc`；原生 Windows／macOS 不支援）
- Python 3.9+
- tmux、Orca（`orca-ide`）為選配，沒裝時該區自動略過

## 資料來源

`/proc`、`~/.claude/sessions/<pid>.json`、`~/.claude/projects/**/<sessionId>.jsonl`、`~/.codex/sessions/**/rollout-*.jsonl`、`~/.codex/session_index.jsonl`、git metadata、`tmux list-panes`、`orca-ide terminal list --json`。
