# pilotfish-roles

pilotfish 多模型協調層的 **installer plugin**（Claude Code 專用）。
跑一次 `pilotfish-setup` skill，把「Fable/Opus 當 orchestrator 做判斷、便宜模型量產執行、
verifier 對抗式把關」的分層委派架構裝進本機。

## 與 pilotfish-parallel 的分工

| plugin | 宿主 | 用途 |
|---|---|---|
| **pilotfish-roles**（本 plugin） | Claude Code | 安裝角色 agents ＋ 委派政策 ＋ 模型別名釘選 |
| pilotfish-parallel | Codex only | 隔離 worktree 並行 job 編排 runner |

## 安裝內容（三層）

1. **`~/.claude/agents/`** — 6 個角色 agent（scout / Explore / mech-executor / executor / verifier / security-executor）＋ 2 個可選版本變體（executor-opus47 / executor-opus45）
2. **`~/.claude/rules/agents.md`** — 委派政策：角色路由表、完整規格委派、便宜優先升級、verifier 回報前把關、模型版本管理紀律
3. **`~/.claude/settings.json` env** — `ANTHROPIC_DEFAULT_{OPUS,SONNET,HAIKU}_MODEL` 三鍵釘選 tier 別名解析（預設 opus→4-6[1m]、sonnet→4-6、haiku→4-5，setup 時可改）

## 為什麼是 installer 而不是 plugin 原生 agents？

- plugin 原生 agent 名稱會帶命名空間前綴（`pilotfish-roles:executor`），路由表與使用習慣全要改；installer 複製到 `~/.claude/agents/` 保持素名。
- 政策（rules 自動載入）與 env 釘選（settings.json）本來就不是 plugin manifest 能承載的，只能由 setup skill 引導完成（rules 與 settings 寫入皆 draft-first / 備份先行，人審後才落盤）。

## 使用

```
/pilotfish-roles:pilotfish-setup
```

或說「裝 pilotfish」「pilotfish setup」。已安裝者重跑即為升級模式（diff 模板 vs 本機，逐項決定）。
回滾方式見 SKILL.md「升級 / 回滾」節。

## 授權與 attribution

角色定義與政策模板衍生自 [Nanako0129/pilotfish](https://github.com/Nanako0129/pilotfish) v1.1.2（MIT License，
Copyright (c) 2026 Nanako0129）。依 MIT 條款，完整著作權與許可聲明保留於 `LICENSE.pilotfish`。
本 plugin 的在地修改（版本變體 agent、政策整併稿、installer skill）由 df-haha 維護。
