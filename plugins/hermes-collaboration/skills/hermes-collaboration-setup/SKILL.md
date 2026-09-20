---
name: hermes-collaboration-setup
description: Install, upgrade, configure, validate, roll back, or troubleshoot the Hermes Collaboration user plugin for one Hermes profile. Use when setting up two Representative peers, changing peer trust or executor registrations, or migrating from Hermes Exchange to durable HERMES_CASE_V1 collaboration.
---

# Hermes Collaboration Setup

Install one reviewed runtime into one explicitly selected Hermes profile. Keep installation, configuration, enablement, gateway restart, live traffic, and predecessor cutover as separate authority boundaries.

Resolve the directory containing this `SKILL.md` as `<skill-dir>`. All bundled paths below are relative to that directory.

## Preconditions

1. Resolve the active profile through Hermes `get_hermes_home()`. If Hermes is unavailable, require an explicit profile path. Never guess between `~/.hermes`, a named profile, or another user's profile.
2. Confirm that the host supports `pre_gateway_dispatch`, `pre_tool_call`, and `post_tool_call` hooks.
3. Confirm Python has `httpx>=0.27`, `jsonschema>=4.18`, and `PyYAML>=6.0` in the Hermes runtime environment.
4. Identify the local `representative_id`, Owner Telegram chat ID, Owner IDs, peer alias, exact peer `@telegram_username`, peer numeric bot ID, peer trust revision, executor registrations, and authorized private repositories.
5. Keep `TELEGRAM_BOT_TOKEN`, GitHub webhook secrets, and credentials out of YAML, prompts, logs, and case records.

## Draft and validate configuration

Start from `<skill-dir>/../../assets/hermes_collaboration/config.example.yaml`.

- `representative_id` identifies this local Representative.
- `owner.telegram_chat_id` is the local Owner inbox.
- `owner.owner_ids` contains the only Telegram users allowed to record Owner decisions.
- Every peer requires one stable alias, exact username, numeric bot ID, positive `revision`, and explicit active state.
- `executor_registrations` binds approved executor IDs to positive revisions.
- `authorized_repositories` uses exact private `owner/repo` identifiers. Do not use wildcards.
- `intake_agents` lists only pre-approved intake identities.
- Transport bounds must stay within the reviewed limits.

Show the complete non-secret draft before writing it. Verify Bot-to-Bot Mode in BotFather for both Telegram bots. One bot's setting does not prove the other's setting.

## Install or upgrade

The installer validates the runtime and all 11 schemas before swapping files. It does not independently enable the plugin, restart Hermes, send live traffic, change BotFather settings, configure secrets, or disable `hermes-exchange`.

First install, disabled by default:

```text
python3 <skill-dir>/scripts/install_hermes_collaboration_user_plugin.py --hermes-home <profile> --config-source <approved-config>
```

Install and enable only when the user authorized enablement in the same task:

```text
python3 <skill-dir>/scripts/install_hermes_collaboration_user_plugin.py --hermes-home <profile> --config-source <approved-config> --enable
```

For an existing installation, explain that `--replace` moves the old runtime to `<profile>/backups/hermes-collaboration/<transaction>/plugin` and retains the previous configuration for rollback. Obtain explicit replacement authorization, then add `--replace`.

After installation, validate the exact installed path with the bundled runtime validator. Report `status: PASS`, `schemas: 11`, the schema hash, and protocol `HERMES_CASE_V1`. Do not claim the gateway is using the new runtime before a separately authorized restart and post-restart check.

## Rollback

List the exact backup directories and ask the user to select one when more than one exists. Restore only an exact backup path:

```text
python3 <skill-dir>/scripts/install_hermes_collaboration_user_plugin.py --hermes-home <profile> --rollback-backup <profile>/backups/hermes-collaboration/<transaction>/plugin
```

Rollback restores the matching runtime, profile plugin state, and retained collaboration configuration when present. A gateway restart is still a separate authorization.

## Activation and migration gates

Require separate authorization for each external or live-state action:

1. enable or disable a plugin;
2. restart the Hermes gateway;
3. send a harmless live Case handoff;
4. change BotFather Bot-to-Bot Mode;
5. disable the predecessor `hermes-exchange` plugin.

Do not delete predecessor plugin files or SQLite state during cutover. Disable the predecessor only after both Representatives pass the fake flow, installed-profile validation, and an explicitly authorized live smoke test.

## Report

State the exact profile, runtime version, install transaction, configuration path, enabled or disabled state, validation result, whether both bots were verified, whether the gateway was restarted, whether live traffic ran, and whether predecessor cutover remains pending.
