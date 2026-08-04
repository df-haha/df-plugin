# Pilotfish Parallel for Codex

Codex-only plugin for running two or three genuinely independent repository jobs concurrently in isolated Git worktrees, integrating their snapshots all-or-nothing, and asking a fresh read-only Verifier for the final decision.

## Requirements

- Linux or WSL2 (`/proc` and POSIX process-group behavior are required)
- Codex CLI compatible with `0.144.1`
- Python 3.11+
- Git 2.43+
- Python package `jsonschema` 4.x
- Access to `gpt-5.6-luna`, `gpt-5.6-sol`, and `gpt-5.6-terra`

Check the Python dependency before first use:

```bash
python3 -c 'import jsonschema; print(jsonschema.__version__)'
```

On Ubuntu/WSL2, install it with the distribution package when missing:

```bash
sudo apt-get update
sudo apt-get install python3-jsonschema
```

## Install from df-plugin

```bash
codex plugin marketplace add df-haha/df-plugin --ref main
codex plugin add pilotfish-parallel@df-haha-plugins
```

Start a new Codex thread after installation so the plugin Skill is discovered.

## Role policy

| Role | Model | Effort | Sandbox |
|---|---|---|---|
| Scout | `gpt-5.6-luna` | `medium` | `read-only` |
| Executor | `gpt-5.6-sol` | `medium` | `workspace-write` |
| Verifier | `gpt-5.6-terra` | `xhigh` | `read-only` |

The runner supports at most three concurrent jobs. Multiple writers must have non-overlapping literal path prefixes and run in separate worktrees. Any worker, integration, or final-verification failure fails the batch closed.

This plugin does not modify `~/.codex/config.toml`, create `~/.codex/agents/`, or write any `~/.claude/` path.
