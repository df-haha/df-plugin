# session-resume-inventory

重開機或交接前，盤點目前開著的 Claude Code、Codex、tmux 與 Orca 工作階段（session）：對應到 repo／worktree 與分支、說明即時狀態（idle／busy／shell／open），並產出精確的 `claude --resume <id>`／`codex resume <id>` 指令。

全程唯讀：不關閉、不續接、不改名任何 session。

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

## 需求

- Linux 或 WSL（讀 `/proc`；原生 Windows／macOS 不支援）
- Python 3.9+
- tmux、Orca（`orca-ide`／`orca`）為選配，沒裝時該區自動略過

## 資料來源

`/proc`、`~/.claude/sessions/<pid>.json`、`~/.claude/projects/**/<sessionId>.jsonl`、`~/.codex/sessions/**/rollout-*.jsonl`、`~/.codex/session_index.jsonl`、git metadata、`tmux list-panes`、`orca-ide terminal list --json`。
