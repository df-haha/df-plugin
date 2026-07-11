---
name: pilotfish-parallel
description: Use when a Codex task contains two or three genuinely independent repository jobs, including multiple disjoint write jobs that should run concurrently in isolated Git worktrees and be integrated all-or-nothing with final adversarial verification. Trigger on phrases such as "並行處理", "平行處理", "多個寫入任務", "parallel workers", "parallel executors", or "pilotfish parallel". Do not use for dirty/non-Git repositories, dependent tasks, overlapping write paths, architecture decisions, external-state tests, or tasks requiring more than three workers.
---

# Pilotfish Parallel

1. Announce that you are using `pilotfish-parallel`, restate the two or three independent jobs, and identify which jobs may write.
2. Read the repository `AGENTS.md` and the relevant repository files before decomposing the task. The repository remains the source of truth.
3. Refuse to start unless the target is a Git repository top-level on a normal attached branch with a clean tracked and untracked tree, no sparse checkout, and no active Git operation.
4. Create one JSON manifest under `/tmp`. Preserve the user's original `task_requirement`, state an explicit `completion_claim`, and define overall acceptance criteria independently of the split. Record the exact base branch and SHA, disjoint literal path prefixes, argv-based checks, and explicit timeouts. Read every check before setting `effect_scope` to `repo-local`. Never put network, database, Docker, socket, or external-service commands in the manifest; report `NEEDS_WRITABLE_VERIFICATION` so the main loop can handle them.
5. Show the manifest summary to the user only when decomposition changes user-visible scope. Ordinary in-scope decomposition may proceed without another approval round.
6. Resolve the manifest and the linked [runner](scripts/runner.py) to absolute paths from this `SKILL.md` directory; never assume a global `$CODEX_HOME/skills/...` location because plugin installs live in a versioned cache. Run `python3 <absolute-runner-path> validate --manifest <absolute-manifest-path>`. The format is defined by [the job schema](schemas/job.schema.json), and role routing is fixed by [the role policy](config/roles.toml).
7. Only after validation succeeds, run the same absolute [runner](scripts/runner.py) path with `run --manifest <absolute-manifest-path>`.
8. Treat every state other than `APPLIED` as failure. Never manually apply a quarantined patch, partially accept workers, retry automatically, or resolve integration conflicts automatically.
9. Report worker roles; configured and strictly attested model, effort, and sandbox evidence; behavioral sandbox probes; changed files; integration checks; the fresh Verifier result; usage; artifacts; and any cleanup command. Do not call model, effort, or sandbox server-echoed runtime metadata unless the observed Codex JSONL actually contains those fields.
10. Never touch Claude or native MultiAgent configuration. Do not modify `~/.codex/config.toml`, create `~/.codex/agents/`, or write any `~/.claude/` path.
11. Never weaken the `/tmp` exclusion or sibling-write denial gates. Repository test code is trusted only to the explicitly reviewed `repo-local` effect assertion; these checks are not hostile-code containment.

## Two-Executor manifest example

```json
{
  "schema_version": 1,
  "run_id": "parallel-api-docs",
  "task_requirement": "Implement the API validation and update its operator documentation in parallel.",
  "completion_claim": "The API validation and operator documentation are complete and jointly verified.",
  "overall_acceptance_criteria": [
    "The validation tests pass.",
    "The operator guide describes the final behavior."
  ],
  "repo_root": "/absolute/path/to/repository",
  "base_branch": "main",
  "base_sha": "0123456789abcdef0123456789abcdef01234567",
  "max_parallel": 2,
  "integration_verification_commands": [
    {
      "id": "all-tests",
      "argv": ["python3", "-m", "unittest", "discover", "-s", "tests"],
      "timeout_seconds": 300,
      "effect_scope": "repo-local"
    }
  ],
  "jobs": [
    {
      "id": "api-validation",
      "role": "executor",
      "goal": "Implement API input validation without changing documentation.",
      "allowed_paths": ["src/api", "tests/api"],
      "denied_paths": ["docs"],
      "acceptance_criteria": ["Invalid API input is rejected by focused tests."],
      "verification_commands": [
        {
          "id": "api-tests",
          "argv": ["python3", "-m", "unittest", "tests.test_api"],
          "timeout_seconds": 180,
          "effect_scope": "repo-local"
        }
      ],
      "timeout_seconds": 1200
    },
    {
      "id": "operator-docs",
      "role": "executor",
      "goal": "Update only the operator documentation for the specified validation behavior.",
      "allowed_paths": ["docs/operator"],
      "denied_paths": ["src", "tests"],
      "acceptance_criteria": ["The guide documents accepted and rejected input."],
      "verification_commands": [
        {
          "id": "docs-check",
          "argv": ["python3", "tools/check_docs.py", "docs/operator"],
          "timeout_seconds": 180,
          "effect_scope": "repo-local"
        }
      ],
      "timeout_seconds": 900
    }
  ]
}
```

There is no validation bypass. If the manifest cannot pass validation, stop and report the failure.
