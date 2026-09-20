# Hermes Collaboration

Hermes Collaboration turns cross-person engineering work into durable `HERMES_CASE_V1` cases shared by two equal Hermes Representatives. Each Representative keeps its own state, explains work to its Owner, and only crosses authority gates after the correct Owner decision.

## What is included

- `assets/hermes_collaboration/`: reviewed Hermes user-plugin runtime and 11 JSON schemas
- `skills/hermes-collaboration-setup/`: atomic install, configuration, upgrade, rollback, and validation guidance
- `skills/hermes-collaboration-operations/`: case lifecycle, Owner decisions, work routing, and incident diagnosis
- `tests/`: package, installer, configuration, and runtime registration checks

The bundled runtime is copied byte-for-byte from `df-haha/hermes` commit `a8aa7e96776e70cf5399711233ce03d469a864b4`; the source subtree is Git tree `7c95cd3c555b2ed963e5ed01afc2b9f2ad8f25d7`.

## Safety boundary

- Telegram transports bounded case events; it is not the source of case truth.
- Peer identity requires the configured Telegram username and numeric bot ID.
- A Discussion Mandate does not authorize repository execution.
- Repository mutation requires an explicit Execution Grant.
- A draft pull request does not authorize merge, release, or deploy.
- Claude Code and Codex are bounded Executors. They do not represent an Owner and cannot enlarge approved scope.
- Secrets stay outside collaboration YAML and case records.

## Install

Install this marketplace plugin, then invoke `hermes-collaboration-setup`. The bundled installer can stage the runtime and validated non-secret configuration into one Hermes profile. Enabling the plugin, restarting the gateway, sending live traffic, and disabling `hermes-exchange` remain separate Owner-authorized actions.

Use `hermes-collaboration-operations` after installation to inspect case state or diagnose a blocked lifecycle without bypassing an authority gate.

The `case_*` tools are registered by the runtime inside Hermes. Claude Code and Codex do not gain those tools merely by installing this marketplace package. Outside a Hermes host, the operations skill prepares safe inspection steps or an Owner-facing request for Hermes instead of claiming direct case access.
