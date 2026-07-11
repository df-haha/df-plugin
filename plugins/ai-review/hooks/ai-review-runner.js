#!/usr/bin/env node
// AI Review Runner - shared execution engine for Claude Code and Codex plugins.
// It invokes external reviewer CLIs (Codex, Antigravity, Claude Code) in print mode
// and writes each result to a temporary markdown file.
// Codex model/effort defaults live in ~/.codex/config.toml; flags are only
// injected when CODEX_MODEL / CODEX_EFFORT are explicitly set.
"use strict";

const fs = require("fs");
const path = require("path");
const { execFileSync } = require("child_process");

const COOLDOWN_MS = 60_000;
const MAX_PROMPT_CHARS = 8000;
// 預設 10 分鐘 = Claude Code Bash tool 同步等待上限；可用 AI_REVIEW_TIMEOUT_MS 覆寫。
// 更長的審查請改走 run_in_background + 事後 Read 結果檔（見 SKILL.md）。
const DEFAULT_TIMEOUT_MS = 600_000;
const DEFAULT_CLAUDE_MODEL = "claude-opus-4-6[1m]";
const DEFAULT_CLAUDE_EFFORT = "max";
const DEFAULT_CLAUDE_PERMISSION_MODE = "plan";
const DEFAULT_AGY_MODEL = "3.5-flash";
const CLAUDE_PERMISSION_MODES = new Set([
  "acceptEdits",
  "auto",
  "bypassPermissions",
  "default",
  "dontAsk",
  "plan",
]);

function cooldownFile() {
  return process.env.AI_REVIEW_COOLDOWN_FILE || path.join(
    process.env.HOME || "/tmp",
    ".ai-review",
    "cooldown.json"
  );
}

function sanitizeModel(model) {
  return (model || "").replace(/[\u0000-\u001f\u007f]/g, "").trim();
}

function sanitizeEffort(effort) {
  return (effort || "").replace(/[^a-zA-Z-]/g, "").trim();
}

function reviewTimeoutMs() {
  const raw = Number.parseInt(process.env.AI_REVIEW_TIMEOUT_MS || "", 10);
  return Number.isFinite(raw) && raw > 0 ? raw : DEFAULT_TIMEOUT_MS;
}

function normalizeClaudePermissionMode(mode) {
  const value = mode || DEFAULT_CLAUDE_PERMISSION_MODE;
  if (!CLAUDE_PERMISSION_MODES.has(value)) {
    throw new Error(
      `未知 Claude permission mode "${mode}"，支援: ${Array.from(CLAUDE_PERMISSION_MODES).join(", ")}`
    );
  }
  return value;
}

function normalizeReviewer(reviewer) {
  const value = (reviewer || "codex").toLowerCase();
  if (value === "codex") return "codex";
  if (value === "claude" || value === "claude-code") return "claude";
  if (value === "agy" || value === "antigravity" || value === "gemini") return "agy";
  if (value === "both") return "both";
  throw new Error(`未知 reviewer "${reviewer}"，支援: codex, agy, claude, both (gemini→agy)`);
}

function expandReviewers(reviewer) {
  const normalized = normalizeReviewer(reviewer);
  return normalized === "both" ? ["codex", "agy"] : [normalized];
}

function buildInvocation(reviewer, model = "", options = {}) {
  const normalized = normalizeReviewer(reviewer);

  if (normalized === "codex") {
    // code 模式走專用 review harness；plan/debate 走一般 exec
    const isCodeMode = (options.mode || "code") === "code";
    const args = isCodeMode
      ? ["review", "--uncommitted"]
      : ["exec", "--ephemeral", "--skip-git-repo-check"];
    // 注意：codex review 沒有 -m 旗標，model 一律走 -c config override（review/exec 皆支援）
    const selectedModel = sanitizeModel(model);
    if (selectedModel) args.push("-c", `model="${selectedModel}"`);
    const effort = sanitizeEffort(options.effort);
    if (effort) args.push("-c", `model_reasoning_effort="${effort}"`);
    return {
      command: "codex",
      args,
      label: "Codex",
      // codex review 的 --uncommitted 與 positional prompt 互斥，code 模式不附加 prompt
      omitPrompt: isCodeMode,
    };
  }

  const selectedModel = sanitizeModel(
    model || (normalized === "claude" ? DEFAULT_CLAUDE_MODEL : DEFAULT_AGY_MODEL)
  );

  if (normalized === "claude") {
    const permissionMode = normalizeClaudePermissionMode(options.claudePermissionMode);
    const args = ["-p", "--output-format", "text", "--permission-mode", permissionMode];
    if (selectedModel) args.push("--model", selectedModel);
    args.push("--effort", DEFAULT_CLAUDE_EFFORT);
    return {
      command: "claude",
      args,
      label: "Claude Code",
    };
  }

  if (normalized === "agy") {
    const timeoutMs = options.timeoutMs || reviewTimeoutMs();
    const args = ["--print-timeout", `${Math.ceil(timeoutMs / 1000)}s`];
    if (selectedModel) args.push("--model", selectedModel);
    args.push("--print");
    return {
      command: "agy",
      args,
      label: "Antigravity",
    };
  }

  throw new Error("both must be expanded before building an invocation");
}

function readTextFile(filePath, label) {
  try {
    return fs.readFileSync(filePath, "utf8");
  } catch {
    throw new Error(`無法讀取${label}: ${filePath}`);
  }
}

function truncatePrompt(content) {
  if (content.length <= MAX_PROMPT_CHARS) return content;
  return `${content.slice(0, MAX_PROMPT_CHARS)}\n\n[... 內容截斷 ...]`;
}

function buildPrompt({ mode, planPath = "", question = "" }) {
  switch (mode || "code") {
    case "code":
      return [
        "請審查此 Git repository 目前未提交的程式碼變更。",
        "",
        "請先檢查 git status 與 git diff，找出 bugs、行為回歸、缺漏測試、風險與可操作的修正建議。",
        "只做審查，不要修改檔案，不要 commit，不要執行破壞性命令。",
        "輸出請用繁體中文，依嚴重度排序，並附上具體檔案/行號或可驗證依據。",
      ].join("\n");

    case "plan": {
      if (!planPath) throw new Error("plan 模式需要 PLAN_PATH 環境變數或計畫檔路徑");
      const planContent = truncatePrompt(readTextFile(planPath, "計畫檔案"));
      return `請審查以下實作計畫，指出潛在問題、遺漏、風險和改進建議。請用繁體中文、依嚴重度排序：\n\n${planContent}`;
    }

    case "debate":
      if (!question) throw new Error("debate 模式需要 CODEX_QUESTION 環境變數或問題文字");
      return [
        "你是魔鬼代言人（Devil's Advocate）。",
        "針對以下技術決策或問題，請提出反對意見、潛在風險、替代方案和需要考慮的 trade-off。",
        "請用繁體中文，聚焦實質技術風險，不要泛泛而談。",
        "",
        question,
      ].join("\n");

    default:
      throw new Error(`未知模式 "${mode}"，支援: code, plan, debate`);
  }
}

function checkCooldown(provider) {
  try {
    const file = cooldownFile();
    if (!fs.existsSync(file)) return;

    const data = JSON.parse(fs.readFileSync(file, "utf8"));
    const lastRun = data[provider] || 0;
    const elapsed = Date.now() - lastRun;
    if (elapsed < COOLDOWN_MS) {
      const remaining = Math.ceil((COOLDOWN_MS - elapsed) / 1000);
      console.log(`COOLDOWN: ${provider} 距離上次執行不到 60 秒，請等待 ${remaining} 秒後再試。`);
      process.exit(0);
    }
  } catch {
    // Ignore corrupt or unreadable cooldown files.
  }
}

function updateCooldown(provider) {
  try {
    const file = cooldownFile();
    fs.mkdirSync(path.dirname(file), { recursive: true });
    let data = {};
    try {
      if (fs.existsSync(file)) data = JSON.parse(fs.readFileSync(file, "utf8"));
    } catch {
      data = {};
    }
    data[provider] = Date.now();
    fs.writeFileSync(file, JSON.stringify(data), "utf8");
  } catch {
    // Cooldown write failures should not block review.
  }
}

function makeOutputPath(provider) {
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  return `/tmp/ai-review-${provider}-${ts}.md`;
}

function formatExecError(error, label) {
  const stdout = Buffer.isBuffer(error.stdout) ? error.stdout.toString("utf8") : (error.stdout || "");
  const stderr = Buffer.isBuffer(error.stderr) ? error.stderr.toString("utf8") : (error.stderr || "");
  const body = [stdout, stderr].filter(Boolean).join("\n\n").trim();
  return body || `${label} 執行錯誤，但沒有輸出 stdout/stderr。`;
}

function runReviewer({ reviewer, mode, projectDir, planPath, question, model, effort }) {
  checkCooldown(reviewer);

  const outputFile = makeOutputPath(reviewer);
  const prompt = buildPrompt({ mode, planPath, question });
  const timeoutMs = reviewTimeoutMs();
  const invocation = buildInvocation(reviewer, model, {
    mode,
    effort,
    timeoutMs,
    claudePermissionMode: process.env.AI_REVIEW_CLAUDE_PERMISSION_MODE,
  });

  updateCooldown(reviewer);
  console.log(`REVIEWER: ${reviewer}`);
  console.log(`MODE: ${mode}${model ? ` (model: ${model})` : ""}${effort ? ` (effort: ${effort})` : ""}`);
  console.log(`OUTPUT: ${outputFile}`);
  console.log(`EXECUTING: ${invocation.label} ...`);

  const cliArgs = invocation.omitPrompt ? invocation.args : [...invocation.args, prompt];
  let failed = false;
  try {
    const output = execFileSync(invocation.command, cliArgs, {
      cwd: projectDir,
      timeout: timeoutMs,
      encoding: "utf8",
      maxBuffer: 10 * 1024 * 1024,
    });
    fs.writeFileSync(outputFile, output || "", "utf8");
  } catch (error) {
    failed = true;
    fs.writeFileSync(outputFile, formatExecError(error, invocation.label), "utf8");
  }

  if (failed) {
    console.log(`WARNING: ${invocation.label} 執行失敗（非零 exit 或逾時），錯誤訊息已寫入 ${outputFile}`);
    return "";
  }

  if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 0) {
    console.log(`SUCCESS: 結果已寫入 ${outputFile}`);
    return outputFile;
  }

  console.log(`WARNING: output 檔案為空或不存在，${reviewer} 可能沒有產出`);
  return "";
}

function main() {
  const mode = process.env.AI_REVIEW_MODE || process.env.CODEX_MODE || "code";
  const projectDir = process.env.PROJECT_DIR || process.cwd();
  const planPath = process.env.PLAN_PATH || "";
  const question = process.env.AI_REVIEW_QUESTION || process.env.CODEX_QUESTION || "";
  const model = process.env.AI_REVIEW_MODEL || process.env.CODEX_MODEL || "";
  const effort = process.env.AI_REVIEW_EFFORT || process.env.CODEX_EFFORT || "";
  const reviewers = expandReviewers(process.env.REVIEWER || "codex");

  for (const reviewer of reviewers) {
    runReviewer({ reviewer, mode, projectDir, planPath, question, model, effort });
  }
}

if (require.main === module) {
  try {
    main();
  } catch (error) {
    console.log(`ERROR: ${error.message}`);
    process.exit(1);
  }
}

module.exports = {
  buildInvocation,
  buildPrompt,
  expandReviewers,
  normalizeReviewer,
  sanitizeModel,
  sanitizeEffort,
  reviewTimeoutMs,
  normalizeClaudePermissionMode,
  runReviewer,
};
