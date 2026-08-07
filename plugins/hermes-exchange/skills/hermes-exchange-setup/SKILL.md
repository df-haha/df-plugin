---
name: hermes-exchange-setup
description: Install, upgrade, configure, or troubleshoot Hermes Exchange at user scope; pair two Telegram agent endpoints; explain whether another agent needs the same plugin; or guide a non-Hermes runtime to implement the open HERMES_EXCHANGE/1 wire protocol. Trigger on Hermes Exchange, agent-to-agent handoff, Telegram bot-to-bot agent messaging, exchange peer setup, and protocol compatibility questions.
---

# Hermes Exchange Setup

Treat Hermes Exchange as a protocol plus adapters, not as a requirement that both owners install the same marketplace package.

Resolve the directory containing this `SKILL.md` as `<skill-dir>`. All bundled paths below are relative to that directory, not to the user's current repository.

## Select the endpoint path

1. For a Hermes endpoint, install the bundled reference adapter with `<skill-dir>/scripts/install_hermes_exchange_user_plugin.py`.
2. For another runtime, read `<skill-dir>/references/protocol-v1.md` and `<skill-dir>/references/adapter-guide.md`; implement and test that protocol instead of copying Hermes hooks.
3. For an ordinary bot with no compatible receiver, describe the message as readable-only. Do not promise authenticated, deduplicated, human-gated automation.

## Install the Hermes adapter

Before writing, resolve the active Hermes profile with `get_hermes_home()`. If the Hermes environment is unavailable, require an explicit `--hermes-home`; never guess a profile path.

Run:

```bash
python3 <skill-dir>/scripts/install_hermes_exchange_user_plugin.py --hermes-home <resolved-profile>
```

The installer refuses to overwrite an existing runtime. Use `--replace` only after showing the retained-backup behavior and receiving approval.

After installation:

1. Draft `<skill-dir>/../../assets/hermes_exchange/config.example.yaml` into the profile's `state/hermes-exchange/config.yaml` with the owner.
2. Keep `TELEGRAM_BOT_TOKEN` in the existing secret environment; never put it in YAML, prompts, logs, or command output.
3. Record each peer's explicit name, `@username`, and authenticated numeric sender ID. Never infer or auto-update identity.
4. Confirm both bots enabled Telegram Bot-to-Bot Communication Mode in BotFather.
5. Ask before changing `plugins.enabled`, restarting a gateway, or running a live probe.

## Pair unlike implementations

Require protocol compatibility, not package equality:

- encode/decode the exact canonical V1 envelope;
- bind the transport-authenticated sender ID to the configured peer;
- reject unknown versions, recipients, expired messages, oversize messages, and replay conflicts;
- preserve request/result correlation with `exchange_id` and `in_reply_to`;
- enforce bounded hops, rate limits, deduplication, and a terminal loop condition;
- keep send, execute, and return behind local owner decisions for workflow-safe conformance.

V1 has no automatic capability negotiation. Pairing must confirm the protocol version and conformance level out of band.

## Report accurately

State which level was verified:

- readable-only: a person or general agent can inspect the text, with no workflow guarantees;
- wire-compatible: the endpoint passes V1 codec fixtures;
- workflow-safe: it also enforces identity, replay, limits, and human gates;
- Hermes reference: it runs the bundled user-scope adapter.

Never collapse these levels into a generic claim that two agents are connected.
