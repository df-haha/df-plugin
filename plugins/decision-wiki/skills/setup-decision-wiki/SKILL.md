---
name: setup-decision-wiki
description: Use when a Git repository（程式碼儲存庫） needs a decision Wiki（決策知識庫） installed, repaired, or upgraded, especially when existing ADRs（架構決策紀錄）, agent rules（代理規則）, or decision files must be preserved for human review.
---

# Setup Decision Wiki

## Overview

Install or upgrade one Git-first（以 Git 為主）decision Wiki without creating a second authority. Work from a reviewable temporary patch（暫存修補內容）, preserve accepted history, and require human approval for every agent-rule change.

Resolve this skill's directory as `<skill-dir>` before running bundled assets. Do not assume the skill directory is the repository root.

## Workflow

Copy this checklist and keep it current:

```text
Decision Wiki setup:
- [ ] Locate the repository and inspect its dirty state（未提交狀態）
- [ ] Read repository rules, decision indexes, and existing ADRs（架構決策紀錄）
- [ ] Verify Node.js 18（JavaScript 執行環境） or newer
- [ ] Choose install（安裝） or upgrade（升級） without overwriting history
- [ ] Build and validate a temporary candidate tree（候選檔案樹）
- [ ] Show the complete patch and a separate rules draft
- [ ] Apply only approved files
- [ ] Run asset, decision, and repository gates
```

### 1. Locate and read

1. Run `git rev-parse --show-toplevel`; stop if the target is not a Git repo.
2. Record `git status --short --branch`. Preserve every unrelated tracked（已追蹤）, untracked（未追蹤）, ignored（忽略）, and symlink（符號連結）entry.
3. Read, when present, `AGENTS.md`, `CLAUDE.md`, `docs/INDEX.md`, `docs/decisions/README.md`, `docs/decisions/INDEX.md`, all formal decision cards, and existing `docs/**/decisions/ADR-*.md` records.
4. Read build and test configuration before choosing repository gates. Repository-local instructions override this skill.

### 2. Gate the runtime

Run:

```bash
node -e "const major=Number(process.versions.node.split('.')[0]); process.exit(major >= 18 ? 0 : 1)"
```

If Node.js is missing or older than 18, stop and ask the user to choose a supported validation runtime（驗證執行環境）. Do not install documentation without an executable gate.

### 3. Choose a branch

Inventory the whole footprint before choosing. Check for the `docs/decisions` path even when it is empty; all existing `DEC-*.md`, `_draft/`, `INDEX.md`, decision README, and ADR files under `docs/`; and every existing or repository-configured decision validator and test, including but not limited to the portable target paths.

| Observable state | Required branch |
|---|---|
| The entire footprint is absent | Install from the bundled assets. |
| Any footprint path or ADR exists | Upgrade/inventory in place; retain all existing relevant files before proposing changes. |

README absence alone never proves a fresh install. A partial decision directory, card, draft, index, validator, test, or ADR always takes the upgrade branch.

For an install, propose these repo-relative asset mappings:

| Asset | Target |
|---|---|
| `assets/docs/decisions/README.md` | `docs/decisions/README.md` |
| `assets/docs/decisions/INDEX.md` | `docs/decisions/INDEX.md` |
| `assets/docs/decisions/_draft/README.md` | `docs/decisions/_draft/README.md` |
| `assets/scripts/validate-decisions.mjs` | `scripts/validate-decisions.mjs` |
| `assets/tests/validate-decisions.test.mjs` | `tests/validate-decisions.test.mjs` |

For an upgrade:

- Treat the existing cards and ADR（架構決策紀錄）files as immutable history.
- Keep an existing validator only when its native tests pass and it enforces the repository's current `docs/decisions/README.md` contract. Otherwise propose the portable validator and test as an explicit replacement patch. Record the selected contract and validator: repo-native when retaining the existing contract, portable for a fresh install or an explicitly reviewed portable-contract adoption.
- Keep each existing ADR at its current path. Add an ADR index row only when the record is already explicitly accepted.
- Treat decisions found only in specs, plans, memories, or database records as candidates. After deduplication, place any proposed conversion under `_draft/`; never accept or delete it automatically.
- Do not add a database, MCP（模型情境協定）service, hook（事件掛鉤）, semantic index（語意索引）, or session scanner.

### 4. Build a review patch

1. Create a temporary candidate root outside the repo. For installs, copy the bundled decision documents into their repo-relative paths. For upgrades, seed the candidate with every relevant path found during inventory: the complete current decision tree, every decision card and ADR at its existing repo-relative path, plus every existing decision validator, test, and the configuration that invokes them. Keep unrelated files out of the candidate.
2. Only after seeding, overlay proposed documents, validator, and test. For upgrades, start each candidate file from its current contents and make the smallest contract-preserving change. Never replace a populated `INDEX.md` with the empty template or silently move an existing card or ADR.
3. Validate the candidate tree with the selected contract-aware validator before touching the repo:

   - For a fresh install or reviewed portable-contract adoption, run:

     ```bash
     node --test "<skill-dir>/assets/tests/validate-decisions.test.mjs"
     node "<skill-dir>/assets/scripts/validate-decisions.mjs" "<candidate-root>"
     ```

   - When retaining a valid repo-native contract and validator, run that validator's native tests and validate `<candidate-root>` according to repository instructions. Do not use the bundled validator's fixed schema to judge that target corpus.

4. Do not call the candidate review-ready（可送審） until every selected validator test and validation command exits `0`. On any failure, show the exact output and stop without applying files.
5. Show the complete file list and full unified diff（統一差異）, including every new file in full. A summary is not a preview.
6. Wait for approval of the Wiki patch before applying it.

If shared instructions should mention the Wiki, draft `AGENTS.md` and `CLAUDE.md` changes separately under the temporary directory. Show the full rule text or diff and wait for separate, explicit human approval. Never bundle unreviewed rule changes into the Wiki patch.

### 5. Apply and verify

Apply only the approved candidate files. Do not stage, commit, push（推送）, or modify unrelated work.

Run all applicable gates in this order:

1. Run the selected contract-aware validator gate:

   - For the portable contract, run its bundled tests and validate the repository:

     ```bash
     node --test "<skill-dir>/assets/tests/validate-decisions.test.mjs"
     node "<skill-dir>/assets/scripts/validate-decisions.mjs" "<repo-root>"
     ```

   - For a retained repo-native contract, run its native tests and validator against `<repo-root>`. Do not run the bundled validator against the target corpus unless the portable contract was explicitly adopted.

2. Repository-local typecheck（型別檢查）, lint（靜態檢查）, test, and build gates required by repo instructions.
3. `git diff --check`, a final `git status --short`, and a complete diff review proving unrelated work stayed untouched.

If any gate fails, stop, keep the patch uncommitted, and report the exact failure. Only commit after a separate explicit request.

## Safety boundaries

- Formal `DEC-*.md` cards are the decision SSOT（單一真相來源）; `INDEX.md` is navigation only.
- A draft remains `status: proposed` until a human accepts its content, sources, and persistent relationships.
- `supersedes`, `depends_on`, `conflicts_with`, and `related_to` require human confirmation. Similarity is never persisted as a relationship.
- Supersession creates a new card and changes lifecycle metadata on the old card; it never rewrites accepted rationale or history.
- Source excerpts must be redacted, short, visible, and hash-verified. Never store a full transcript or secret.
- Do not auto-commit under any install, upgrade, repair, or validation outcome.
