"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const runner = require("./ai-review-runner.js");

test("expands both into Codex and Antigravity reviewers", () => {
  assert.deepEqual(runner.expandReviewers("both"), ["codex", "agy"]);
});

test("keeps codex as a real provider and maps gemini to agy", () => {
  assert.equal(runner.normalizeReviewer("codex"), "codex");
  assert.equal(runner.normalizeReviewer("gemini"), "agy");
  assert.equal(runner.normalizeReviewer(""), "codex");
});

test("builds codex review invocation for code mode without model or effort by default", () => {
  assert.deepEqual(runner.buildInvocation("codex", "", { mode: "code" }), {
    command: "codex",
    args: ["review", "--uncommitted"],
    label: "Codex",
    omitPrompt: true,
  });
});

test("builds codex exec invocation with model and reasoning effort overrides", () => {
  assert.deepEqual(runner.buildInvocation("codex", "gpt-5.4-mini", { mode: "debate", effort: "high" }), {
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
  assert.deepEqual(runner.buildInvocation("codex", "gpt-5.3-codex-spark", { mode: "code" }), {
    command: "codex",
    args: ["review", "--uncommitted", "-c", 'model="gpt-5.3-codex-spark"'],
    label: "Codex",
    omitPrompt: true,
  });
});

test("injects reasoning effort into codex review for code mode", () => {
  assert.deepEqual(runner.buildInvocation("codex", "", { mode: "code", effort: "ultra" }), {
    command: "codex",
    args: ["review", "--uncommitted", "-c", 'model_reasoning_effort="ultra"'],
    label: "Codex",
    omitPrompt: true,
  });
});

test("builds Claude Code print-mode invocation with plan permissions", () => {
  assert.deepEqual(runner.buildInvocation("claude", "opus-test"), {
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

test("uses Claude Opus 4.6 1m with max effort by default", () => {
  assert.deepEqual(runner.buildInvocation("claude"), {
    command: "claude",
    args: [
      "-p",
      "--output-format",
      "text",
      "--permission-mode",
      "plan",
      "--model",
      "claude-opus-4-6[1m]",
      "--effort",
      "max",
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
      "claude-opus-4-6[1m]",
      "--effort",
      "max",
    ],
    label: "Claude Code",
  });
});

test("uses Antigravity 3.5 Flash by default with the default 600s print timeout", () => {
  assert.deepEqual(runner.buildInvocation("agy"), {
    command: "agy",
    args: ["--print-timeout", "600s", "--model", "3.5-flash", "--print"],
    label: "Antigravity",
  });
});

test("derives agy print timeout from the shared timeout option", () => {
  assert.deepEqual(runner.buildInvocation("agy", "", { timeoutMs: 120_000 }).args.slice(0, 2), [
    "--print-timeout",
    "120s",
  ]);
});

test("reads timeout from AI_REVIEW_TIMEOUT_MS with a 600s fallback on garbage", () => {
  const saved = process.env.AI_REVIEW_TIMEOUT_MS;
  try {
    delete process.env.AI_REVIEW_TIMEOUT_MS;
    assert.equal(runner.reviewTimeoutMs(), 600_000);
    process.env.AI_REVIEW_TIMEOUT_MS = "120000";
    assert.equal(runner.reviewTimeoutMs(), 120_000);
    process.env.AI_REVIEW_TIMEOUT_MS = "not-a-number";
    assert.equal(runner.reviewTimeoutMs(), 600_000);
    process.env.AI_REVIEW_TIMEOUT_MS = "-5";
    assert.equal(runner.reviewTimeoutMs(), 600_000);
  } finally {
    if (saved === undefined) delete process.env.AI_REVIEW_TIMEOUT_MS;
    else process.env.AI_REVIEW_TIMEOUT_MS = saved;
  }
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

test("builds discuss prompt that asks Claude to inspect the current repository without editing it", () => {
  const prompt = runner.buildPrompt({
    mode: "discuss",
    question: "這個模組的邊界是否合理？",
  });

  assert.match(prompt, /目前 Git repository/);
  assert.match(prompt, /先讀取/);
  assert.match(prompt, /不要修改/);
  assert.match(prompt, /這個模組的邊界是否合理？/);
});

test("requires a question for discuss mode", () => {
  assert.throws(
    () => runner.buildPrompt({ mode: "discuss" }),
    /discuss 模式需要/,
  );
});

test("builds plan review prompt from a plan file", () => {
  const dir = fs.mkdtempSync(path.join(os.tmpdir(), "ai-review-plan-"));
  const planPath = path.join(dir, "plan.md");
  fs.writeFileSync(planPath, "Step 1: add Codex manifest\n", "utf8");

  const prompt = runner.buildPrompt({ mode: "plan", planPath });

  assert.match(prompt, /審查以下實作計畫/);
  assert.match(prompt, /Step 1: add Codex manifest/);
});
