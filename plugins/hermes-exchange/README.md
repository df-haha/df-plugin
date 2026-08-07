# Hermes Exchange

Hermes Exchange is a lightweight Telegram notification relay for allowlisted agent peers. It sends one bounded `HERMES_NOTIFY/1` message, delivers an accepted notification to the local owner's Telegram inbox, and stops. It is suitable for handoffs, decision requests, review notices, and other owner-visible notifications without imposing a task/result workflow.

## Safety boundary

- A peer is bound to its configured Telegram username and numeric sender ID. Unknown bots fail closed, and their message body is never shown.
- Every accepted remote body is rendered as untrusted data. Quoted remote content cannot authorize a send or execution.
- Receiving a bot message never starts an LLM, executor, or automatic reply.
- Sending requires an explicit instruction from the local user. Claude Code and Codex use the `hermes-exchange-send` skill; Hermes exposes `relay_notify`.
- Optional repository execution is disabled by default. When enabled, only a configured repository alias with a fixed absolute path and a fixed `claude|codex` executor is accepted.
- Tool authorization remains the normal Hermes user-authorization boundary; this plugin does not claim a second per-tool user ACL. Restrict access to Hermes itself to the intended local owner(s).

## Install and configure

Use the `hermes-exchange-setup` skill for first install, upgrades, and peer allowlist changes. The bundled user-scope installer copies the reviewed runtime atomically. It does not write configuration or tokens, enable the plugin, restart Hermes, change BotFather settings, or send a live message.

Pairing requires Telegram Bot-to-Bot Mode to be enabled for both bots in BotFather. Keep `TELEGRAM_BOT_TOKEN` in the existing Hermes secret environment; never place it in YAML, prompts, logs, or command output.

The bundled example is JSON-compatible YAML and the sender transport uses the Python standard library, so the direct Claude Code/Codex sender does not depend on `PyYAML` or `httpx`. Custom non-JSON YAML remains supported when `PyYAML` is already available.

## Optional execution

Keep `execution.enabled: false` unless the owner explicitly wants local execution. Each repository mapping uses a stable alias, an existing absolute path, and one executor (`claude` or `codex`). Changing `execution.enabled` or repository mappings requires a Hermes gateway restart because tools and configuration are loaded at startup. Remote content still cannot authorize execution.

Telegram reply context is truncated by Hermes. When asking Hermes to execute work, restate the complete task in the owner's own message and name the configured repository alias; never rely on “do the quoted message” to carry a full handoff.

## Package layout

- `assets/hermes_exchange/`: reviewed Hermes user plugin runtime and deterministic relay CLI
- `skills/hermes-exchange-setup/`: user-scope install, peer allowlist, and execution configuration guidance
- `skills/hermes-exchange-send/`: explicit-release workflow for Claude Code and Codex
- `tests/`: dependency-free runtime, installer, and package contract checks
