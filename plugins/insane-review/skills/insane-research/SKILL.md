---
name: insane-research
description: Use a logged-in subscription ChatGPT web session to start, resume, and fetch Deep Research reports without OpenAI API billing. Trigger when the user asks for ChatGPT subscription Deep Research, insane research, or a research run through ChatGPT Web.
---

# insane-research

Use the shared `bin/insane_research.py` CLI. It preserves `insane-review`; do not route code-review requests here unless the user specifically asks for Deep Research.

Every new run targets **GPT-5.6 Sol** with **Extra High** reasoning. Model, reasoning effort, and Deep Research mode must all be positively verified before the prompt is sent. Never accept ChatGPT's defaults or infer the model from a response.

## Resolve the plugin root

Resolve this selected `SKILL.md` to an absolute path. `PLUGIN_ROOT` is two directories above its containing directory. Use the absolute path for every command; do not assume `CLAUDE_PLUGIN_ROOT` exists in Codex.

## Browser backend routing

### Codex CLI and Claude Code: dedicated CDP browser

Codex CLI and Claude Code use the default `cdp` driver. It operates the isolated ChatGPT browser on port `9333`; the original review browser remains on `9222`. This is required for Codex CLI because OpenAI's Browser and Chrome Extension are available in the ChatGPT desktop app, not in Codex CLI or the IDE extension.

### ChatGPT desktop Codex: optional Chrome extension

When this skill is used in a ChatGPT desktop Codex chat and `chrome:Chrome` is available, the official Codex Chrome Extension may use the `--browser-driver agent` plus `record` bridge described below. Do not require or claim this backend in Codex CLI.

## Desktop authorization

Any Chrome-extension browser action, CDP `start`, or CDP `status --refresh` is a live browser operation. Before the first live browser call in each user task, state the exact operation and obtain the user's explicit authorization. A previous task's authorization does not carry over. Local `start --browser-driver agent`, `record`, `status` without `--refresh`, and `fetch` do not operate the browser.

Deep Research defaults to CDP port `9333`; the original review workflow remains on `9222`. On WSL without `DISPLAY` or `WAYLAND_DISPLAY`, live browser calls automatically re-execute this CLI with Windows Python while local-only calls remain in WSL. If Windows Python or its Playwright package is unavailable, report that prerequisite instead of falling back to headless login. Login and any challenge remain manual in the dedicated Windows Chrome profile.

Never extract or print ChatGPT cookies, access tokens, refresh tokens, passwords, or one-time codes. Login remains manual in the dedicated browser profile.

## Codex CLI / Claude Code CDP commands

Create a complete research prompt in a local UTF-8 file, then start:

```bash
python3 "<PLUGIN_ROOT>/bin/insane_research.py" start \
  --prompt-file "<ABSOLUTE_PROMPT_FILE>" \
  --out-dir "<PROJECT_ROOT>/.insane-research" \
  --json
```

The returned `run_dir` is the durable run identity. Pin it in the conversation. Never select a run by newest modification time.

Read the last local status without browser access:

```bash
python3 "<PLUGIN_ROOT>/bin/insane_research.py" status "<RUN_DIR>" --json
```

Refresh from the bound ChatGPT conversation after desktop authorization:

```bash
python3 "<PLUGIN_ROOT>/bin/insane_research.py" status "<RUN_DIR>" --refresh --json
```

Fetch only after status is `COMPLETED`:

```bash
python3 "<PLUGIN_ROOT>/bin/insane_research.py" fetch "<RUN_DIR>" --json
```

## ChatGPT desktop Codex Chrome-extension workflow

Create the prompt file first. Require the final report to begin with the exact standalone line `Deep research completed.` and include at least two direct source links. Then prepare the run without sending:

```bash
python3 "<PLUGIN_ROOT>/bin/insane_research.py" start \
  --prompt-file "<ABSOLUTE_PROMPT_FILE>" \
  --out-dir "<PROJECT_ROOT>/.insane-research" \
  --browser-driver agent \
  --json
```

Pin the returned `run_dir`. Through `chrome:Chrome`, open a new ChatGPT chat and use fresh DOM state rather than guessed selectors. Before sending:

1. Select `GPT-5.6 Sol` and verify its checked/selected state.
2. Select `Extra High` and verify its checked/selected state.
3. Select Deep Research and verify the active composer pill/state.
4. Capture the pre-submit assistant count, copy-control count, and existing assistant `data-message-id` values.
5. Insert the exact `request.md` content and verify it is complete.
6. Send once. Capture the resulting `/c/<id>` conversation URL. Never resend if URL capture is uncertain.

Write a private temporary JSON observation and record it:

```json
{
  "kind": "submission",
  "conversation_url": "https://chatgpt.com/c/<id>",
  "base_assistant": 0,
  "base_copy": 0,
  "base_message_ids": [],
  "verified_model": "GPT-5.6 Sol",
  "verified_effort": "Extra High",
  "deep_research_active": true,
  "prompt_verified": true
}
```

```bash
python3 "<PLUGIN_ROOT>/bin/insane_research.py" record "<RUN_DIR>" \
  --observation-file "<PRIVATE_OBSERVATION_JSON>" --json
```

For a refresh, navigate only to the bound `conversation_url`. Collect the latest assistant turn from fresh DOM state, including its `data-message-id`, full visible report text, direct links, whether the turn has a positively verified terminal state, whether streaming is active, and any quota block. Record this shape:

```json
{
  "kind": "refresh",
  "conversation_url": "https://chatgpt.com/c/<same-id>",
  "message_id": "<data-message-id>",
  "assistant_text": "<full visible assistant text>",
  "response_text": "<full copied or visible report>",
  "links": ["https://...", "https://..."],
  "turn_complete": true,
  "terminal_signal": "deep_research_report_frame",
  "streaming": false,
  "quota": null
}
```

Set `turn_complete` to `true` only after a positive terminal UI signal for the bound Deep Research turn. Set `terminal_signal` to `deep_research_report_frame` only for the completed `internal://deep-research` report frame, or to `assistant_turn_complete` for a positively completed normal assistant turn. Absence of a spinner alone is not completion. The CLI rejects missing/unknown terminal signals, conversation mismatches, stale message IDs, wrong model/effort, missing Deep Research verification, short or source-poor reports, and ambiguous completion. Remove the temporary observation after `record` succeeds because it can contain sensitive report data.

## State handling

- `CREATED`: local run exists; no browser submission is confirmed.
- `PROMPT_SUBMITTED`: prompt was sent and `conversation_url` was captured.
- `SENT_UNKNOWN_LOCATION`: ChatGPT may have received the prompt but no conversation URL was captured. Find the conversation manually; never resubmit automatically.
- `RESEARCHING`: ChatGPT still shows an active or incomplete research turn.
- `WAITING_CLARIFICATION`: read `clarification.md`, tell the user, and ask them to answer in the dedicated ChatGPT browser. Do not invent an answer or click without new authorization.
- `COMPLETED`: a report passed the conservative completion check and is stored in `response.md`.
- `HARVESTED`: `report.md` is ready for downstream citation verification or synthesis.
- `FAILED`: report the persisted `error`; do not resubmit automatically because that can consume subscription quota twice.

The completion detector fails closed. If ChatGPT changes its UI and completion cannot be established, keep the run non-terminal and report that live selector verification is required. A Codex browser observation is evidence input, not authority: the CLI still enforces the bound URL, new message identity, terminal signal, completion marker, minimum report length, and source links.

## Output

Each run has its own directory containing `request.md`, `state.json`, and, when available, `clarification.md`, `response.md`, `sources.json`, and `report.md`. Treat all files as potentially sensitive research artifacts.
