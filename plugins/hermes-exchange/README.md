# Hermes Exchange

Hermes Exchange packages an open human-gated agent exchange protocol and one user-scope Hermes reference adapter. Telegram bot-to-bot is the first transport; engineering handoff, decision requests, reviews, research, document drafting, and operational checks are all represented by an open `kind` plus JSON payload.

## Does the peer need this same plugin?

No. The peer needs a compatible endpoint, not an identical package.

- Hermes users can install this package's bundled adapter.
- Another framework can implement [`HERMES_EXCHANGE/1`](skills/hermes-exchange-setup/references/protocol-v1.md) and interoperate.
- A generic Telegram agent can read the JSON as text, but is not automatically workflow-safe unless it implements identity binding, replay protection, correlation, limits, and approval gates.

Use the `hermes-exchange-setup` skill to install the reference adapter or design a compatible adapter.

## Safety boundary

The reference workflow requires local owner approval before sending a request, executing received work, and returning the result. It never writes Telegram tokens into plugin configuration and does not automatically enable the plugin, restart Hermes, or run a live probe.

## Package layout

- `assets/hermes_exchange/`: reviewed Hermes user plugin runtime
- `skills/hermes-exchange-setup/`: installation and interoperability workflow
- `skills/hermes-exchange-setup/references/protocol-v1.md`: framework-neutral wire contract
- `tests/`: 15 standard-library installer, protocol, and runtime-contract checks plus the repository package gate
