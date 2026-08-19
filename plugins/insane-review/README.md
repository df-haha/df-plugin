English | [한국어](README.ko.md) | [中文](README.zh.md) | [日本語](README.ja.md) | [Español](README.es.md)

# insane-review

<div align="center">
  <img src="assets/hero.png" width="860" alt="insane-review cinematic hero">
</div>

> **GPT Pro (the Pro reasoning tier of the current flagship — currently GPT-5.6 Sol) has no API. This plugin uses it from inside Claude Code anyway.**

GPT Pro lives in the ChatGPT web app (subscription), not behind a public API. The original review workflow drives a **logged-in ChatGPT web session over CDP**. This fork lets Codex CLI and Claude Code share a dedicated CDP Deep Research workflow; ChatGPT desktop Codex can optionally use the official Chrome Extension bridge. Both paths run on your existing ChatGPT plan with no API billing.

[Quick Start](#quick-start) • [Why insane-review?](#why-insane-review) • [How it works](#how-it-works) • [Features](#features) • [Tuning & timeouts](#tuning--timeouts) • [Requirements](#requirements)

---

## Quick Start

### 1. Add the marketplace (once)

```
/plugin marketplace add https://github.com/fivetaku/gptaku_plugins.git
```

### 2. Install

```
/plugin install insane-review
```

### 3. Restart Claude Code

Required for the plugin to load.

### 4. Prepare the browser bridge (once per machine)

Pro is web-only, so insane-review needs a real, logged-in browser on a debug port:

```bash
# launch Comet (or Chrome) with the CDP port, then log into chatgpt.com and pick the Pro reasoning tier
open -a Comet --args --remote-debugging-port=9222

# verify everything is wired up (node/repomix, playwright, pyperclip, CDP browser)
python3 bin/pack_and_ask.py --check-env
```

### 5. Run it

```
/insane-review review the auth flow in src/auth
```

Or just say "have Pro review this" / "ask GPT Pro about this design" — Claude figures out the target and packs it.

---

## Why insane-review?

- **Use subscription ChatGPT without API billing** — the original review path and Codex CLI／Claude Code Deep Research use isolated logged-in CDP sessions; ChatGPT desktop Codex can optionally use the official Chrome extension.
- **Claude picks the complete relevant set** — you don't have to hand-list files. For reviews it sends **full code** (no `--compress`, which strips function bodies and produces false "looks fine" verdicts) and audits the packed file list so nothing is silently missing.
- **fail-closed by design** — wrong model, unverified login, truncated prompt, an empty pack, or a previous turn's answer are all refused rather than silently sent or saved. Hardened across four rounds of Pro's own self-review (6 → 0 P0s).
- **Two roles, one engine** — a standalone reviewer when you ask for a fix/review, or a web-only member of [agent-council](references/council-setup.md) so Pro can debate alongside Codex/Gemini/others.
- **Citations you can follow** — line numbers are packed in, so Pro's findings come back as `file:line` you can jump to.

---

## How it works

```
"have Pro review this"  /  council member call
  ↓
Claude selects the COMPLETE relevant file set (full code — no --compress for reviews)
  ↓
repomix pack  (line numbers · secretlint · packed-file-list audit · token count)
  ↓
CDP-attach the logged-in ChatGPT session
Select Pro effort (flagship auto-follows; currently GPT-5.6 Sol)  → re-open menu and VERIFY (mismatch = abort, fail-closed)
  ↓
Attach pack + prompt  → confirm the prompt actually landed in the composer  → send
  ↓
Wait for THIS turn to complete (turn-scoped: new assistant node + new copy button)
Optionally cut long reasoning early with --force-answer-after
  ↓
Harvest the answer → save to  ./.insane-review/response_*.md  (atomic write)
```

Output lands in the **current project's** `.insane-review/` folder (like kkirikkiri's `.kkirikkiri/`), never inside the plugin:

```
.insane-review/
├── pack_<target>_<ts>.md        # what was sent (chmod 600)
└── response_<target>_<ts>.md    # Pro's answer + verified model header
```

---

## Features

### Commands

| Command | What it does |
|---------|-------------|
| `/insane-review [target/question]` | Pack the relevant code and send it to GPT Pro for review |
| `/insane-research [topic]` | Start or resume a subscription ChatGPT Deep Research run (experimental) |
| natural language | "have Pro review this", "ask GPT Pro about X" — same flow |

### Subscription Deep Research (experimental)

The fork preserves the original review command and adds a host-neutral persistent CLI that both Claude Code and Codex can call:

```bash
python3 bin/insane_research.py start --prompt-file prompt.md --json
python3 bin/insane_research.py status .insane-research/<run_id> --refresh --json
python3 bin/insane_research.py fetch .insane-research/<run_id> --json
```

Each run stores its prompt, bound ChatGPT conversation URL, state, response, sources, and final report in an isolated directory. `fetch` refuses non-completed runs. New runs require a positively verified **GPT-5.6 Sol / Extra High / Deep Research** selection before submission. The browser adapter remains experimental. On 2026-08-19, the shared Codex CLI／Claude Code CDP path passed a no-send live model/effort/mode gate and completed a Deep Research submit → bound conversation → nested report-frame harvest → fetch run (8,428 report characters and 8 source links). The optional ChatGPT desktop Chrome-extension bridge remains separately unverified. Selector changes fail closed instead of returning a partial report.

Codex CLI and Claude Code use the dedicated CDP adapter on port `9333`, leaving the original review browser on `9222`. OpenAI documents that Browser and Chrome Extension are unavailable in Codex CLI and the IDE extension, so they are not CLI prerequisites. In a ChatGPT desktop Codex chat, `start --browser-driver agent` plus the internal `record` bridge can validate official Chrome Extension observations before advancing persisted state. On WSL without `DISPLAY`/`WAYLAND_DISPLAY`, live CDP calls re-execute with Windows Python; local agent-driver preparation, `record`, `status`, and `fetch` stay in WSL.

### Two modes

1. **Standalone reviewer** — you ask for a fix/review → Claude scopes the target → repomix pack → Pro analysis → applied back.
2. **agent-council member** — register Pro as a web-only council member so it debates with other models. See [`references/council-setup.md`](references/council-setup.md).

### Key flags

| Flag | Purpose |
|------|---------|
| `--target <dir>` | Folder to pack (omit for a prompt-only opinion) |
| `--include <glob>` / `--ignore <glob>` | Narrow the packed set |
| `--model pro` | Select the reasoning effort (e.g. Pro) |
| `--require-model "GPT-5.6"` | Verify the active model name — abort send on mismatch (fail-closed) |
| `--prompt "..."` / `--prompt-file` | The question |
| `--pack-only` | Just pack (inspect token count), don't send |
| `--council` | Council mode — response on stdout, logs on stderr |
| `--compress` | tree-sitter skeleton only — **don't use for reviews** (drops function bodies) |
| `--check-env` / `--install` | Diagnose / install the local toolchain |

---

## Tuning & timeouts

Response waiting and pack timeouts are adjustable from both the CLI and the environment — useful because Pro's full reasoning can take 10–15 minutes.

| Control | Default | What it does |
|---------|---------|-------------|
| `--max-wait <sec>` | `1200` (20 min) | Max time to wait for Pro's response before giving up (fail-closed, no partial save) |
| `INSANE_REVIEW_MAX_WAIT` | `1200` | Same as `--max-wait`, via environment |
| `--force-answer-after <sec>` | off | Soft cut: if Pro is still reasoning after N sec, click **"Get answer now"** so it answers **from the reasoning it has done so far** — a complete, saved answer (see below) |
| `INSANE_REVIEW_REPOMIX_TIMEOUT` | `300` | Max seconds for the repomix packing step |
| `--retries <n>` | `1` | Re-attempts if a send/harvest fails |

**Two different "timeouts" — don't confuse them:**

- **`--force-answer-after N` (soft cut, recommended for bounding cost).** Pro reasons for a long time; this clicks ChatGPT's *"Get answer now"* at N seconds, so Pro stops reasoning and replies based on **what it has reasoned up to that point**. That reply is a normal, complete turn — it's harvested and saved like any other. Use it to cap a council member at, say, 120s instead of waiting 10+ minutes.
- **`--max-wait N` (hard ceiling, fail-closed).** If the turn never completes within N seconds *and* no answer was forced, insane-review gives up **without saving** the half-streamed text — an incomplete answer is treated as a failure, not a result. This is intentional: it never hands you a truncated review pretending to be done.

Other environment overrides:

| Variable | Default | What it does |
|----------|---------|-------------|
| `INSANE_REVIEW_CDP_PORT` | `9222` | Browser remote-debugging port |
| `INSANE_REVIEW_COMET` / `INSANE_REVIEW_CHROME` | app default path | Browser executable path |
| `INSANE_REVIEW_REPOMIX_VERSION` | `1.15.0` | Pinned repomix version (reproducibility) |
| `INSANE_REVIEW_OUT` | `./.insane-review` | Output directory (also `--out-dir`) |

```bash
# example: give Pro up to 25 minutes, but cut reasoning at 5 minutes if it's still thinking
INSANE_REVIEW_MAX_WAIT=1500 python3 bin/pack_and_ask.py \
  --target . --include "src/**" --model pro --require-model "GPT-5.6" \
  --force-answer-after 300 --prompt "Where are the concurrency bugs?"
```

---

## Requirements

### Required

- [Claude Code](https://docs.anthropic.com/claude-code)
- For Codex CLI or Claude Code Deep Research: Windows Python with Playwright and the dedicated logged-in CDP browser profile
- Python 3.11+ with `playwright` and `pyperclip`
- Node.js / `npx`
- **A subscription ChatGPT account with GPT Pro**, logged in inside Comet/Chrome launched on the debug port (`--remote-debugging-port=9222`)

### What's auto-handled vs. what you do

| Dependency | First-run behavior |
|------------|-------------------|
| **repomix** | **Fully automatic** — pulled on demand via `npx -y repomix@<pinned>`, never needs manual install |
| **playwright / pyperclip** | Checked on first use by `--check-env`; install them with `--install` (runs `pip install`). A normal run without them stops with a clear instruction (fail-closed) rather than failing mid-way |
| **Browser login + GPT Pro** | **Manual** — can't be automated; you log into `chatgpt.com` and select Pro once |

```bash
# one shot: checks node/repomix, playwright, pyperclip, CDP browser — and installs the pip deps if missing
python3 bin/pack_and_ask.py --check-env --install
```

### Note

Codex CLI and Claude Code use the direct CDP path because OpenAI's Browser／Chrome Extension is desktop-app-only. ChatGPT desktop Codex may use the official Chrome Extension bridge. Both browser paths rely on live UI details and may need maintenance when the UI changes. Review applicable account and workspace policies before using browser automation.

---

## License

MIT

---

<div align="center">

**No API. Still Pro.**

</div>
