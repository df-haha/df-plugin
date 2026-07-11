# Decision Wiki Dual-Host Plugin Design

**Status:** APPROVED
**Date:** 2026-07-11
**Repositories:** `df-plugin` (distribution canonical), `CC-memory` (current source to retire)

## Goal

Publish the existing `setup-decision-wiki` and `save-decision` skills as one `decision-wiki` plugin that Claude Code and Codex can both discover from `df-plugin`. After both hosts pass fresh-process validation, retire the duplicate direct-install sources without changing any repository's accepted decision history.

## Decisions

- Create one independent `decision-wiki` plugin at version `1.0.0`; do not merge it into `devlog`.
- Make `df-plugin/plugins/decision-wiki/` the sole canonical skill source after migration.
- Share one byte-identical `skills/` tree across hosts. Host-specific metadata lives only in the two plugin manifests.
- Keep skill frontmatter cross-client: only `name` and `description`. Retain `agents/openai.yaml` for Codex; Claude Code may ignore it.
- Add no database, MCP server, hook, automatic session capture, or semantic index.
- Push the `df-plugin` release. Commit the later `CC-memory` cleanup locally but do not push it: that branch is already ahead of its remote for unrelated work.

## Plugin Structure

```text
plugins/decision-wiki/
├── .claude-plugin/plugin.json
├── .codex-plugin/plugin.json
├── skills/
│   ├── setup-decision-wiki/
│   │   ├── SKILL.md
│   │   ├── agents/openai.yaml
│   │   └── assets/...
│   └── save-decision/
│       ├── SKILL.md
│       └── agents/openai.yaml
└── tests/package.test.js
```

The plugin is registered in both `.claude-plugin/marketplace.json` and `.agents/plugins/marketplace.json`, and documented in the root `README.md`. The Claude manifest follows existing marketplace conventions. The Codex manifest declares `skills: "./skills/"` plus interface metadata and no unsupported components.

## Discovery and Invocation

Both hosts load the same two skill bodies and their natural-language triggers. Plugin component names may be host-namespaced, so the release must not assume that bare `/save-decision` or `$save-decision` is the final explicit spelling. Acceptance records the exact explicit invocation shown by the installed Claude Code and Codex versions; natural-language triggers such as `存決策` and `setup decision wiki` must work regardless of namespace presentation.

## Test-First Packaging

Before the plugin exists, add `plugins/decision-wiki/tests/package.test.js` and run it to observe the expected RED failure. The test contract requires:

- both manifests exist, agree on name/version, and expose only real components;
- both marketplaces contain exactly one correctly shaped `decision-wiki` entry;
- both skill trees and required assets exist;
- each `SKILL.md` frontmatter contains only `name` and `description`;
- forbidden single-host dependencies and local absolute paths are absent;
- root documentation names both hosts and both skills.

After implementation, run:

1. `node --test 'plugins/**/*.test.js'`;
2. the portable validator's bundled `node:test` suite;
3. `quick_validate.py` for both skills;
4. Codex `validate_plugin.py` against the plugin;
5. `claude plugin validate` against the plugin and Claude marketplace;
6. JSON parsing, `git diff --check`, and a complete diff review.

## Release Flow

1. Work on `feat/decision-wiki-plugin` in an isolated worktree.
2. Copy the already-tested skill trees from `CC-memory` without behavior edits, then add manifests, marketplace entries, README text, and packaging tests.
3. Use `gpt-5.6-terra` through Codex CLI for a bounded implementation or review task. The main agent retains responsibility for skill-instruction interpretation and final acceptance.
4. Merge the verified branch into `df-plugin/main` and push `origin/main`.
5. Refresh Claude's GitHub marketplace and install `decision-wiki@df-haha-plugins`.
6. Install the same plugin from Codex's existing local `df-haha-plugins` marketplace.
7. Verify both plugin inventories and cached tree hashes match the `df-plugin` canonical tree.

## Safe Cutover

Directly installed skills can mask plugin-loaded skills. After inventory/hash verification, move—do not delete—the four direct global directories into a timestamped migration backup:

```text
~/.claude/skills/{setup-decision-wiki,save-decision}
~/.codex/skills/{setup-decision-wiki,save-decision}
```

Then start fresh Claude Code and Codex processes. In a disposable Git fixture, trigger `save-decision` with an explicitly unsettled decision and require the exact `NOT_SETTLED` result, no file changes, and no commit. Inspect the resolved component/cache path to prove the plugin, rather than a stale direct copy, supplied the skill.

Only after both hosts pass:

- back up and remove the compatibility-port copies under `CC_project/doc/claude-codex-port/skills/`;
- remove `CC-memory/skills/{setup-decision-wiki,save-decision}`;
- remove `CC-memory/scripts/install-decision-skills.sh` and its installer test;
- keep `CC-memory` decision documents, native validator/tests, design records, and Git history;
- create a scoped local `CC-memory` cleanup commit without pushing it.

## Failure and Rollback

- Any pre-release validation failure blocks merge and installation.
- Any install, inventory, hash, or fresh-process failure blocks source retirement.
- If a global-directory cutover probe fails, move the timestamped backups back to their original paths and leave `CC-memory` unchanged.
- If one host passes and the other fails, keep the plugin installed for diagnosis but restore both direct global sources so behavior remains symmetric.
- Do not uninstall, rewrite, migrate, or modify `devlog` in this change.

## Acceptance Criteria

- `decision-wiki` appears installed and enabled in both host plugin inventories.
- Both hosts discover both skills from plugin-managed paths.
- The fresh-process unsettled-decision probe passes on both hosts with a clean fixture.
- Plugin cache/source tree hashes match the `df-plugin` canonical tree.
- The four direct global copies and two compatibility-port copies are absent only after successful probes and recoverable from backup.
- `CC-memory` retains its decision Wiki corpus and validator while no longer publishing duplicate skill sources.
- `df-plugin` CI and all focused validators pass; no Critical or Important review finding remains.
