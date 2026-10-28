"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const runner = require("./ai-review-runner.js");

// 2026-09-08：Codex 預設模型改由 $CODEX_HOME/claude-review.config.toml 驅動；
// 測試用 CODEX_HOME 指向暫存目錄控制「有／無 profile 檔」兩種情境。
const emptyCodexHome = fs.mkdtempSync(path.join(os.tmpdir(), "codex-home-empty-"));
const profiledCodexHome = fs.mkdtempSync(path.join(os.tmpdir(), "codex-home-profile-"));
fs.writeFileSync(
  path.join(profiledCodexHome, "claude-review.config.toml"),
  'model = "gpt-test-profile"\nmodel_reasoning_effort = "low"\n',
  "utf8"
);

function withCodexHome(home, fn) {
  const prev = process.env.CODEX_HOME;
  process.env.CODEX_HOME = home;
  try { return fn(); } finally {
    if (prev === undefined) delete process.env.CODEX_HOME; else process.env.CODEX_HOME = prev;
  }
}

// `resolved` 只是給 log 用的說明字串，比對 invocation 時剝掉
function stripResolved(invocation) {
  const { resolved, ...rest } = invocation;
  return rest;
}

test("expands both into Codex and Antigravity reviewers", () => {
  assert.deepEqual(runner.expandReviewers("both"), ["codex", "agy"]);
});

test("keeps codex as a real provider and maps gemini to agy", () => {
  assert.equal(runner.normalizeReviewer("codex"), "codex");
  assert.equal(runner.normalizeReviewer("gemini"), "agy");
  assert.equal(runner.normalizeReviewer(""), "codex");
});

test("builds codex review invocation for code mode without model or effort when no profile file", () => {
  const invocation = withCodexHome(emptyCodexHome, () => runner.buildInvocation("codex", "", { mode: "code" }));
  assert.deepEqual(stripResolved(invocation), {
    command: "codex",
    args: ["review", "--uncommitted"],
    label: "Codex",
    omitPrompt: true,
  });
  assert.match(invocation.resolved, /profile 缺檔/);
});

test("reads default codex model and effort from claude-review.config.toml", () => {
  const invocation = withCodexHome(profiledCodexHome, () => runner.buildInvocation("codex", "", { mode: "code" }));
  assert.deepEqual(invocation.args, [
    "review",
    "--uncommitted",
    "-c",
    'model="gpt-test-profile"',
    "-c",
    'model_reasoning_effort="low"',
  ]);
  assert.match(invocation.resolved, /claude-review\.config\.toml/);
});

test("explicit model and effort win over claude-review.config.toml", () => {
  const invocation = withCodexHome(profiledCodexHome, () =>
    runner.buildInvocation("codex", "gpt-explicit", { mode: "debate", effort: "high" })
  );
  assert.deepEqual(invocation.args, [
    "exec",
    "--ephemeral",
    "--skip-git-repo-check",
    "-c",
    'model="gpt-explicit"',
    "-c",
    'model_reasoning_effort="high"',
  ]);
  assert.match(invocation.resolved, /source=env/);
});

test("builds codex exec invocation with model and reasoning effort overrides", () => {
  const invocation = withCodexHome(emptyCodexHome, () =>
    runner.buildInvocation("codex", "gpt-5.4-mini", { mode: "debate", effort: "high" })
  );
  assert.deepEqual(stripResolved(invocation), {
    command: "codex",
    args: [
      "exec",
      "--ephemeral",
      "--skip-git-repo-check",
      "-c",
      'model="gpt-5.4-mini"',
      "-c",
      'model_reasoning_effort="high"',
    ],
    label: "Codex",
    omitPrompt: false,
  });
});

test("injects model via -c config override for code mode because codex review has no -m flag", () => {
  const invocation = withCodexHome(emptyCodexHome, () =>
    runner.buildInvocation("codex", "gpt-5.3-codex-spark", { mode: "code" })
  );
  assert.deepEqual(stripResolved(invocation), {
    command: "codex",
    args: ["review", "--uncommitted", "-c", 'model="gpt-5.3-codex-spark"'],
    label: "Codex",
    omitPrompt: true,
  });
});

test("injects reasoning effort into codex review for code mode", () => {
  const invocation = withCodexHome(emptyCodexHome, () =>
    runner.buildInvocation("codex", "", { mode: "code", effort: "ultra" })
  );
  assert.deepEqual(stripResolved(invocation), {
    command: "codex",
    args: ["review", "--uncommitted", "-c", 'model_reasoning_effort="ultra"'],
    label: "Codex",
    omitPrompt: true,
  });
});

test("builds Claude Code print-mode invocation with plan permissions", () => {
  assert.deepEqual(runner.buildInvocation("claude", "opus-test", { effort: "max" }), {
    command: "claude",
    args: [
      "-p",
      "--output-format",
      "text",
      "--permission-mode",
      "plan",
      "--model",
      "opus-test",
      "--effort",
      "max",
    ],
    label: "Claude Code",
  });
});

test("uses Claude Opus 4.6 with high effort by default", () => {
  assert.deepEqual(runner.buildInvocation("claude"), {
    command: "claude",
    args: [
      "-p",
      "--output-format",
      "text",
      "--permission-mode",
      "plan",
      "--model",
      "claude-opus-4-6",
      "--effort",
      "high",
    ],
    label: "Claude Code",
  });
});

test("allows Claude permission mode to opt into bypassPermissions", () => {
  assert.deepEqual(runner.buildInvocation("claude", "", {
    claudePermissionMode: "bypassPermissions",
  }), {
    command: "claude",
    args: [
      "-p",
      "--output-format",
      "text",
      "--permission-mode",
      "bypassPermissions",
      "--model",
      "claude-opus-4-6",
      "--effort",
      "high",
    ],
    label: "Claude Code",
  });
});

test("uses Antigravity Gemini 3.7 Flash High by default with the default 540s print timeout", () => {
  assert.deepEqual(runner.buildInvocation("agy"), {
    command: "agy",
    args: ["--print-timeout", "540s", "--model", "Gemini 3.7 Flash (High)", "--print"],
    label: "Antigravity",
  });
});

test("derives agy print timeout from the shared timeout option", () => {
  assert.deepEqual(runner.buildInvocation("agy", "", { timeoutMs: 120_000 }).args.slice(0, 2), [
    "--print-timeout",
    "120s",
  ]);
});

test("reads timeout from AI_REVIEW_TIMEOUT_MS with a 540s fallback on garbage", () => {
  const saved = process.env.AI_REVIEW_TIMEOUT_MS;
  try {
    delete process.env.AI_REVIEW_TIMEOUT_MS;
    assert.equal(runner.reviewTimeoutMs(), 540_000);
    process.env.AI_REVIEW_TIMEOUT_MS = "120000";
    assert.equal(runner.reviewTimeoutMs(), 120_000);
    process.env.AI_REVIEW_TIMEOUT_MS = "not-a-number";
    assert.equal(runner.reviewTimeoutMs(), 540_000);
    process.env.AI_REVIEW_TIMEOUT_MS = "-5";
    assert.equal(runner.reviewTimeoutMs(), 540_000);
  } finally {
    if (saved === undefined) delete process.env.AI_REVIEW_TIMEOUT_MS;
    else process.env.AI_REVIEW_TIMEOUT_MS = saved;
  }
});

test("retries Claude once when the upstream request times out", () => {
  let calls = 0;
  const result = runner.runInvocationWithRetry({
    reviewer: "claude",
    invocation: { command: "claude", args: [], label: "Claude Code" },
    cliArgs: ["review this plan"],
    projectDir: process.cwd(),
    timeoutMs: 540_000,
    execFileSyncFn() {
      calls += 1;
      if (calls === 1) {
        const error = new Error("request failed");
        error.stdout = "Request timed out\n";
        throw error;
      }
      return "review complete";
    },
    nowFn: () => 0,
  });

  assert.equal(calls, 2);
  assert.deepEqual(result, {
    ok: true,
    output: "review complete",
    errorText: "",
    attempts: 2,
  });
});

test("does not retry Claude for non-timeout failures", () => {
  let calls = 0;
  const result = runner.runInvocationWithRetry({
    reviewer: "claude",
    invocation: { command: "claude", args: [], label: "Claude Code" },
    cliArgs: ["review this plan"],
    projectDir: process.cwd(),
    timeoutMs: 540_000,
    execFileSyncFn() {
      calls += 1;
      const error = new Error("authentication failed");
      error.stderr = "Not authenticated\n";
      throw error;
    },
    nowFn: () => 0,
  });

  assert.equal(calls, 1);
  assert.equal(result.ok, false);
  assert.match(result.errorText, /Not authenticated/);
  assert.equal(result.attempts, 1);
});

test("keeps bracketed model aliases when explicitly requested because execFileSync does not invoke a shell", () => {
  assert.equal(runner.sanitizeModel("claude-opus-4-6[1m]"), "claude-opus-4-6[1m]");
});

test("builds code review prompt that asks the reviewer to inspect git diff", () => {
  const prompt = runner.buildPrompt({ mode: "code" });

  assert.match(prompt, /git diff/);
  assert.match(prompt, /未提交/);
  assert.match(prompt, /不要修改/);
});

test("builds plan review prompt from a plan file", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-review-plan-"));
  const planPath = path.join(dir, "plan.md");
  fs.writeFileSync(planPath, "Step 1: add Codex manifest\n", "utf8");

  const prompt = runner.buildPrompt({ mode: "plan", planPath });

  assert.match(prompt, /審查以下實作計畫/);
  assert.match(prompt, /Step 1: add Codex manifest/);
});
