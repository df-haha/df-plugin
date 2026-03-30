#!/usr/bin/env node
// Codex CLI Review Runner — 核心執行引擎
// 透過環境變數接收模式與參數，組裝並執行 codex 指令，output 寫入暫存檔
//
// Security note: 此腳本所有輸入來自環境變數（由 Claude skill 控制），
// 不接受使用者直接輸入。execSync 用於組合 codex CLI 管道��令，
// 已對 shell 特殊字元做跳脫處理。
"use strict";

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

// ── 設�� ──────────────────��───────────────────────────
const COOLDOWN_FILE = path.join(
  process.env.HOME || "/tmp",
  ".claude",
  "codex-review-cooldown.json"
);
const COOLDOWN_MS = 60_000; // 60 秒間隔

// ── Shell 跳脫 ──────────────────────────────────────
function shellEscape(str) {
  return "'" + str.replace(/'/g, "'\\'") + "'";
}

// ── Rate Limit 檢查 ──────────────────────────────────
function checkCooldown() {
  try {
    if (fs.existsSync(COOLDOWN_FILE)) {
      const data = JSON.parse(fs.readFileSync(COOLDOWN_FILE, "utf8"));
      const elapsed = Date.now() - (data.lastRun || 0);
      if (elapsed < COOLDOWN_MS) {
        const remaining = Math.ceil((COOLDOWN_MS - elapsed) / 1000);
        console.log(
          `COOLDOWN: 距離上次執行不到 60 秒，請等待 ${remaining} 秒後再試。`
        );
        process.exit(0);
      }
    }
  } catch {
    // cooldown 檔案損壞，忽略繼續
  }
}

function updateCooldown() {
  try {
    fs.mkdirSync(path.dirname(COOLDOWN_FILE), { recursive: true });
    fs.writeFileSync(
      COOLDOWN_FILE,
      JSON.stringify({ lastRun: Date.now() }),
      "utf8"
    );
  } catch {
    // 寫入失敗不阻塞
  }
}

// ── 輸出檔案���徑 ─────���───────────────────────────────
function makeOutputPath() {
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  return `/tmp/codex-review-${ts}.md`;
}

// ─�� 模式：code ───────────────────────────────────────
function buildCodeCommand(projectDir, outputFile) {
  return {
    cmd: `codex review --uncommitted 2>&1 | tee ${shellEscape(outputFile)}`,
    cwd: projectDir,
  };
}

// ── 模式：plan ───────────────────────────────────────
function buildPlanCommand(planPath, projectDir, outputFile) {
  let planContent;
  try {
    planContent = fs.readFileSync(planPath, "utf8");
  } catch (e) {
    console.log(`ERROR: 無法讀取計畫檔案: ${planPath}`);
    process.exit(1);
  }

  // 截斷過長的計畫內容（避免超過 CLI 引數限���）
  if (planContent.length > 8000) {
    planContent = planContent.slice(0, 8000) + "\n\n[... 內容截斷 ...]";
  }

  const prompt = `請審查以下實作計��，指出潛在問題、遺漏、風險和改進建議：\n\n${planContent}`;

  // 寫入暫存檔避免 shell 引號問題
  const promptFile = `/tmp/codex-review-prompt-${Date.now()}.txt`;
  fs.writeFileSync(promptFile, prompt, "utf8");

  return {
    cmd: `cat ${shellEscape(promptFile)} | codex exec - --ephemeral --skip-git-repo-check -o ${shellEscape(outputFile)} 2>&1`,
    cwd: projectDir,
    cleanup: promptFile,
  };
}

// ���─ 模式：debate ��───────────────���────────────────────
function buildDebateCommand(question, projectDir, outputFile) {
  const prompt = `你是魔鬼代言人（Devil's Advocate）。針對以下技術決策或問題，請提出反對意見、潛在風險、替代方案和需要考慮的 trade-off：\n\n${question}`;

  const promptFile = `/tmp/codex-review-prompt-${Date.now()}.txt`;
  fs.writeFileSync(promptFile, prompt, "utf8");

  return {
    cmd: `cat ${shellEscape(promptFile)} | codex exec - --ephemeral --skip-git-repo-check -o ${shellEscape(outputFile)} 2>&1`,
    cwd: projectDir,
    cleanup: promptFile,
  };
}

// ���─ 主程式 ─���─────────────────────────────────────────
function main() {
  const mode = process.env.CODEX_MODE || "code";
  const projectDir = process.env.PROJECT_DIR || process.cwd();
  const planPath = process.env.PLAN_PATH || "";
  const question = process.env.CODEX_QUESTION || "";
  const model = process.env.CODEX_MODEL || "";

  // Rate limit
  checkCooldown();

  const outputFile = makeOutputPath();
  let build;

  switch (mode) {
    case "code":
      build = buildCodeCommand(projectDir, outputFile);
      break;
    case "plan":
      if (!planPath) {
        console.log("ERROR: plan 模式��要 PLAN_PATH 環境變數");
        process.exit(1);
      }
      build = buildPlanCommand(planPath, projectDir, outputFile);
      break;
    case "debate":
      if (!question) {
        console.log("ERROR: debate 模式需要 CODEX_QUESTION 環境變數");
        process.exit(1);
      }
      build = buildDebateCommand(question, projectDir, outputFile);
      break;
    default:
      console.log(`ERROR: 未知模式 "${mode}"，���援: code, plan, debate`);
      process.exit(1);
  }

  // ���入 model 旗標（僅在使用者明確指定時）
  if (model) {
    const safeModel = model.replace(/[^a-zA-Z0-9._-]/g, "");
    const modelFlag = ` -c model="${safeModel}"`;
    build.cmd = build.cmd.replace(/(codex (?:review|exec))/, `$1${modelFlag}`);
  }

  // 執行
  updateCooldown();
  console.log(`MODE: ${mode}${model ? ` (model: ${model})` : ""}`);
  console.log(`OUTPUT: ${outputFile}`);
  console.log(`EXECUTING: codex ${mode === "code" ? "review" : "exec"} ...`);

  try {
    execSync(build.cmd, {
      cwd: build.cwd,
      timeout: 300_000,
      stdio: ["pipe", "pipe", "pipe"],
      maxBuffer: 10 * 1024 * 1024,
    });
  } catch (e) {
    const stderr = e.stderr?.toString()?.slice(0, 500) || "";
    if (stderr && !fs.existsSync(outputFile)) {
      fs.writeFileSync(outputFile, `Codex 執行錯誤:\n\n${stderr}`, "utf8");
    }
  }

  // 清理暫存 prompt 檔案
  if (build.cleanup) {
    try {
      fs.unlinkSync(build.cleanup);
    } catch {}
  }

  // 驗證 output 存在
  if (fs.existsSync(outputFile) && fs.statSync(outputFile).size > 0) {
    console.log(`SUCCESS: ��果已寫入 ${outputFile}`);
  } else {
    console.log("WARNING: output 檔案為空或不存在���Codex 可能沒有產出");
  }
}

main();
