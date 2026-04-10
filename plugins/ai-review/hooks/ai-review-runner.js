#!/usr/bin/env node
// AI Review Runner — 核心執行引擎（支援 Codex CLI + Gemini CLI）
// 透過環境變數接收模式與參數，組裝並執行 AI CLI 指令，output 寫入暫存檔
//
// Security note: 此腳本所有輸入來自環境變數（由 Claude skill 控制），
// 不接受使用者直接輸入。execSync 用於組合 codex CLI 管道命令，
// 已對 shell 特殊字元做跳脫處理。
"use strict";

const fs = require("fs");
const path = require("path");
const { execSync } = require("child_process");

// ── 設定 ─────────────────────────────────────────────
const COOLDOWN_FILE = path.join(
  process.env.HOME || "/tmp",
  ".claude",
  "ai-review-cooldown.json"
);
const COOLDOWN_MS = 60_000; // 60 秒間隔

// ── Shell 跳脫 ──────────────────────────────────────
function shellEscape(str) {
  return "'" + str.replace(/'/g, "'\\'") + "'";
}

// ── Rate Limit 檢查 ──────────────────────────────────
function checkCooldown(provider) {
  try {
    if (fs.existsSync(COOLDOWN_FILE)) {
      const data = JSON.parse(fs.readFileSync(COOLDOWN_FILE, "utf8"));
      const lastRun = data[provider] || 0;
      const elapsed = Date.now() - lastRun;
      if (elapsed < COOLDOWN_MS) {
        const remaining = Math.ceil((COOLDOWN_MS - elapsed) / 1000);
        console.log(
          `COOLDOWN: ${provider} 距離上次執行不到 60 秒，請等待 ${remaining} 秒後再試。`
        );
        process.exit(0);
      }
    }
  } catch {
    // cooldown 檔案損壞，忽略繼續
  }
}

function updateCooldown(provider) {
  try {
    fs.mkdirSync(path.dirname(COOLDOWN_FILE), { recursive: true });
    let data = {};
    try {
      if (fs.existsSync(COOLDOWN_FILE)) {
        data = JSON.parse(fs.readFileSync(COOLDOWN_FILE, "utf8"));
      }
    } catch { /* ignore */ }
    data[provider] = Date.now();
    fs.writeFileSync(COOLDOWN_FILE, JSON.stringify(data), "utf8");
  } catch {
    // 寫入失敗不阻塞
  }
}

// ── 輸出檔案路徑 ─────────────────────────────────────
function makeOutputPath() {
  const ts = new Date().toISOString().replace(/[:.]/g, "-");
  return `/tmp/ai-review-${ts}.md`;
}

// ── 模式：code ───────────────────────────────────────
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

  // 截斷過長的計畫內容（避免超過 CLI 引數限制）
  if (planContent.length > 8000) {
    planContent = planContent.slice(0, 8000) + "\n\n[... 內容截斷 ...]";
  }

  const prompt = `請審查以下實作計畫，指出潛在問題、遺漏、風險和改進建議：\n\n${planContent}`;

  // 寫入暫存檔避免 shell 引號問題
  const promptFile = `/tmp/ai-review-prompt-${Date.now()}.txt`;
  fs.writeFileSync(promptFile, prompt, "utf8");

  return {
    cmd: `cat ${shellEscape(promptFile)} | codex exec - --ephemeral --skip-git-repo-check -o ${shellEscape(outputFile)} 2>&1`,
    cwd: projectDir,
    cleanup: promptFile,
  };
}

// ── 模式：debate ─────────────────────────────────────
function buildDebateCommand(question, projectDir, outputFile) {
  const prompt = `你是魔鬼代言人（Devil's Advocate）。針對以下技術決策或問題，請提出反對意見、潛在風險、替代方案和需要考慮的 trade-off：\n\n${question}`;

  const promptFile = `/tmp/ai-review-prompt-${Date.now()}.txt`;
  fs.writeFileSync(promptFile, prompt, "utf8");

  return {
    cmd: `cat ${shellEscape(promptFile)} | codex exec - --ephemeral --skip-git-repo-check -o ${shellEscape(outputFile)} 2>&1`,
    cwd: projectDir,
    cleanup: promptFile,
  };
}

// ── Gemini 模式：code ────────────────────────────────────
function buildGeminiCodeCommand(projectDir, outputFile) {
  const prompt = "請審查以下未提交的程式碼變更，指出 bugs、風格問題和改進建議：";
  return {
    cmd: `git diff | gemini -p ${shellEscape(prompt)} --approval-mode plan --output-format text 2>&1 | tee ${shellEscape(outputFile)}`,
    cwd: projectDir,
  };
}

// ── Gemini 模式：plan ────────────────────────────────────
function buildGeminiPlanCommand(planPath, projectDir, outputFile) {
  let planContent;
  try {
    planContent = fs.readFileSync(planPath, "utf8");
  } catch (e) {
    console.log(`ERROR: 無法讀取計畫檔案: ${planPath}`);
    process.exit(1);
  }
  if (planContent.length > 8000) {
    planContent = planContent.slice(0, 8000) + "\n\n[... 內容截斷 ...]";
  }

  const prompt = `請審查以下實作計畫，指出潛在問題、遺漏、風險和改進建議：\n\n${planContent}`;
  const promptFile = `/tmp/ai-review-prompt-${Date.now()}.txt`;
  fs.writeFileSync(promptFile, prompt, "utf8");

  return {
    cmd: `cat ${shellEscape(promptFile)} | gemini -p "" --approval-mode plan --output-format text 2>&1 | tee ${shellEscape(outputFile)}`,
    cwd: projectDir,
    cleanup: promptFile,
  };
}

// ── Gemini 模式：debate ──────────────────────────────────
function buildGeminiDebateCommand(question, projectDir, outputFile) {
  const prompt = `你是魔鬼代言人。針對以下技術決策，提出反對意見、潛在風險、替代方案：\n\n${question}`;
  const promptFile = `/tmp/ai-review-prompt-${Date.now()}.txt`;
  fs.writeFileSync(promptFile, prompt, "utf8");

  return {
    cmd: `cat ${shellEscape(promptFile)} | gemini -p "" --approval-mode plan --output-format text 2>&1 | tee ${shellEscape(outputFile)}`,
    cwd: projectDir,
    cleanup: promptFile,
  };
}

// ── 主程式 ─────────────────────────────────────────
function main() {
  const mode = process.env.CODEX_MODE || "code";
  const projectDir = process.env.PROJECT_DIR || process.cwd();
  const planPath = process.env.PLAN_PATH || "";
  const question = process.env.CODEX_QUESTION || "";
  const model = process.env.CODEX_MODEL || "";
  const reviewer = process.env.REVIEWER || "codex";

  // Rate limit (per-provider)
  checkCooldown(reviewer);

  const outputFile = makeOutputPath();
  let build;

  switch (mode) {
    case "code":
      build = reviewer === "gemini"
        ? buildGeminiCodeCommand(projectDir, outputFile)
        : buildCodeCommand(projectDir, outputFile);
      break;
    case "plan":
      if (!planPath) {
        console.log("ERROR: plan 模式需要 PLAN_PATH 環境變數");
        process.exit(1);
      }
      build = reviewer === "gemini"
        ? buildGeminiPlanCommand(planPath, projectDir, outputFile)
        : buildPlanCommand(planPath, projectDir, outputFile);
      break;
    case "debate":
      if (!question) {
        console.log("ERROR: debate 模式需要 CODEX_QUESTION 環境變數");
        process.exit(1);
      }
      build = reviewer === "gemini"
        ? buildGeminiDebateCommand(question, projectDir, outputFile)
        : buildDebateCommand(question, projectDir, outputFile);
      break;
    default:
      console.log(`ERROR: 未知模式 "${mode}"，支援: code, plan, debate`);
      process.exit(1);
  }

  // 注入 model 旗標（僅在使用者明確指定時）
  if (model) {
    const safeModel = model.replace(/[^a-zA-Z0-9._-]/g, "");
    if (reviewer === "gemini") {
      build.cmd = build.cmd.replace(/(gemini )/, `$1-m ${safeModel} `);
    } else {
      const modelFlag = ` -c model="${safeModel}"`;
      build.cmd = build.cmd.replace(/(codex (?:review|exec))/, `$1${modelFlag}`);
    }
  }

  // 執行
  updateCooldown(reviewer);
  console.log(`REVIEWER: ${reviewer}`);
  console.log(`MODE: ${mode}${model ? ` (model: ${model})` : ""}`);
  console.log(`OUTPUT: ${outputFile}`);
  console.log(`EXECUTING: ${reviewer} ${mode === "code" ? "review" : "exec"} ...`);

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
      fs.writeFileSync(outputFile, `${reviewer} 執行錯誤:\n\n${stderr}`, "utf8");
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
    console.log(`SUCCESS: 結果已寫入 ${outputFile}`);
  } else {
    console.log(`WARNING: output 檔案為空或不存在，${reviewer} 可能沒有產出`);
  }
}

main();
