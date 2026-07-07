"use strict";

const assert = require("node:assert/strict");
const fs = require("node:fs");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const runner = require("./ai-review-runner.js");

test("expands both into Claude Code and Antigravity reviewers", () => {
  assert.deepEqual(runner.expandReviewers("both"), ["claude", "agy"]);
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

test("uses Antigravity 3.5 Flash by default", () => {
  assert.deepEqual(runner.buildInvocation("agy"), {
    command: "agy",
    args: ["--print-timeout", "5m0s", "--model", "3.5-flash", "--print"],
    label: "Antigravity",
  });
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
