---
description: 使用已登入的 ChatGPT 訂閱網頁啟動、檢查並取回 Deep Research 報告
---

# /insane-research

Use `${CLAUDE_PLUGIN_ROOT}/skills/insane-research/SKILL.md` as the operating procedure.

The user's request is `$ARGUMENTS`. Build a complete research prompt from it. Require the final report to begin with the exact standalone line `Deep research completed.` and include at least two direct source links. The CLI must positively verify GPT-5.6 Sol, Extra High reasoning, and Deep Research mode before sending. Before any live `start` or `status --refresh`, explicitly ask for authorization to operate the dedicated ChatGPT browser for this task. Do not reuse authorization from another task.

Run the shared CLI at:

```bash
python3 "${CLAUDE_PLUGIN_ROOT}/bin/insane_research.py" <start|status|fetch> ...
```

Keep the returned absolute `run_dir` pinned for the whole task. Never identify a run by choosing the newest directory.
