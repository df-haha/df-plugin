#!/usr/bin/env node

/**
 * PreToolUse hook: session taint lock.
 *
 * Fail-open when untainted: internal errors → allow.
 * Fail-closed when tainted: internal errors → deny.
 * Emits {} for allow (never "allow" — that suppresses permission prompts).
 */

import { readFileSync } from 'node:fs';
import {
  checkBashTaint,
  checkReadTaint,
  checkTaintedAllow,
  sanitizeSessionId,
  canonicalize,
} from './lock-core.mjs';
import {
  resolveHome, isSessionTainted, markSessionTainted,
  cleanupOldMarkers, readExtraTaintPrefixes,
  appendTaintedDraft, loadTaintedDrafts,
} from './lock-io.mjs';

// ---------------------------------------------------------------------------
// Output helpers (match guard.mjs protocol)
// ---------------------------------------------------------------------------

const outputDeny = (reason) => {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'deny',
      permissionDecisionReason: reason,
    },
  }));
};

const outputAllow = () => {
  process.stdout.write(JSON.stringify({}));
};

// ---------------------------------------------------------------------------
// Main — variables hoisted for catch block access
// ---------------------------------------------------------------------------

let alreadyTainted = false;

try {
  let input;
  try { input = readFileSync(0, 'utf8'); } catch { outputAllow(); process.exit(0); }

  let hookData;
  try { hookData = JSON.parse(input); } catch { outputAllow(); process.exit(0); }

  const toolName = hookData?.tool_name;
  const toolInput = hookData?.tool_input || {};
  const sessionId = hookData?.session_id;
  const hookCwd = hookData?.cwd || process.cwd();

  if (typeof toolName !== 'string') { outputAllow(); process.exit(0); }

  const daHome = resolveHome();
  if (!daHome) { outputAllow(); process.exit(0); }

  const sanitizedId = sanitizeSessionId(sessionId);
  if (!sanitizedId) { outputAllow(); process.exit(0); }

  const extraPrefixes = readExtraTaintPrefixes(daHome);
  const taintedDrafts = loadTaintedDrafts(daHome);
  const taintCtx = { daHome, cwd: hookCwd, extraPrefixes, taintedDrafts };

  // ── Fast path: check if already tainted ──
  alreadyTainted = isSessionTainted(daHome, sanitizedId);

  if (!alreadyTainted) {
    let shouldTaint = false;
    let taintReason = '';

    // F4a: check Bash AND Monitor for taint in untainted path
    if (toolName === 'Bash') {
      const bashCheck = checkBashTaint(toolInput?.command, taintCtx);
      if (bashCheck.taint) { shouldTaint = true; taintReason = bashCheck.reason; }
    } else if (toolName === 'Monitor') {
      const monCmd = toolInput?.command;
      if (typeof monCmd === 'string') {
        const monCheck = checkBashTaint(monCmd, taintCtx);
        if (monCheck.taint) { shouldTaint = true; taintReason = monCheck.reason; }
      }
    } else if (toolName === 'Read' || toolName === 'Grep') {
      const pathToCheck = toolInput?.file_path || toolInput?.path || '';
      const readCheck = checkReadTaint(pathToCheck, daHome, extraPrefixes, hookCwd, taintedDrafts);
      if (readCheck.taint) { shouldTaint = true; taintReason = readCheck.reason; }
    }

    if (shouldTaint) {
      const transcriptPath = hookData?.transcript_path;
      const marked = markSessionTainted(daHome, sanitizedId, taintReason, transcriptPath);
      if (!marked) {
        outputDeny('無法建立安全標記檔案，為安全起見已阻擋此指令。請確認 .daily-agenda 目錄可寫入。');
        process.exit(0);
      }
      cleanupOldMarkers(daHome);
    }

    outputAllow();
    process.exit(0);
  }

  // ── Tainted path: strict allowlist ──
  const ctx = { daHome, cwd: hookCwd, extraPrefixes };
  const result = checkTaintedAllow(toolName, toolInput, ctx);

  if (result.allow) {
    // Draft laundering prevention: record draft paths written during tainted sessions
    if ((toolName === 'Write' || toolName === 'Edit') && toolInput?.file_path) {
      appendTaintedDraft(daHome, toolInput.file_path);
    }
    outputAllow();
  } else {
    outputDeny(result.reason || '這個對話已讀取外部訊息原文，為了防止訊息裡藏的指令被執行，已鎖定：只能繼續使用 daily-agenda 的功能。要做其他事請開新對話。');
  }
} catch (err) {
  if (alreadyTainted) {
    process.stderr.write(`lock.mjs error (tainted, denied): ${err.message}\n`);
    outputDeny(`Hook 內部錯誤（已鎖定模式，安全拒絕）: ${err.message}`);
  } else {
    process.stderr.write(`lock.mjs error (fail-open): ${err.message}\n`);
    outputAllow();
  }
}
