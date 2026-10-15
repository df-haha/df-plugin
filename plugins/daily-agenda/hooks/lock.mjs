#!/usr/bin/env node

/**
 * PreToolUse hook: session taint lock.
 *
 * Fail-open when untainted: internal errors → allow.
 * Fail-closed when tainted: internal errors → deny.
 * Emits {} for allow (never "allow" — that suppresses permission prompts).
 */

import { readFileSync, writeFileSync, mkdirSync, existsSync, statSync, readdirSync, unlinkSync } from 'node:fs';
import { join } from 'node:path';
import {
  checkBashTaint,
  checkReadTaint,
  checkTaintedAllow,
  sanitizeSessionId,
  markerPath,
} from './lock-core.mjs';

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
// Resolve daily-agenda home directory
// ---------------------------------------------------------------------------

const resolveHome = () => {
  const envHome = process.env.DAILY_AGENDA_HOME;
  if (envHome) return envHome;
  const userProfile = process.env.USERPROFILE;
  if (userProfile) return join(userProfile, '.daily-agenda');
  const home = process.env.HOME;
  if (home) return join(home, '.daily-agenda');
  return null;
};

// ---------------------------------------------------------------------------
// Taint marker operations
// ---------------------------------------------------------------------------

const isSessionTainted = (daHome, sanitizedId) => {
  try {
    return existsSync(markerPath(daHome, sanitizedId));
  } catch {
    return false;
  }
};

const markSessionTainted = (daHome, sanitizedId, reason, transcriptPath) => {
  try {
    mkdirSync(join(daHome, 'taint'), { recursive: true });
    const marker = {
      tainted_at: new Date().toISOString(),
      reason,
    };
    if (typeof transcriptPath === 'string' && transcriptPath.length > 0) {
      marker.transcript_path = transcriptPath;
    }
    writeFileSync(markerPath(daHome, sanitizedId), JSON.stringify(marker), 'utf8');
    return true;
  } catch {
    return false;
  }
};

/**
 * Opportunistic cleanup of markers older than 60 days.
 *
 * M2 safe-delete rule: a marker is only deleted if it is older than 60 days
 * AND its recorded transcript file no longer exists. If the marker has no
 * transcript_path (legacy), or if the transcript file still exists, the marker
 * is kept — a resumed session would still have raw content in context.
 */
const cleanupOldMarkers = (daHome) => {
  try {
    const taintDir = join(daHome, 'taint');
    const files = readdirSync(taintDir);
    const now = Date.now();
    const maxAge = 60 * 24 * 60 * 60 * 1000;
    for (const f of files) {
      try {
        const fp = join(taintDir, f);
        const st = statSync(fp);
        if (now - st.mtimeMs <= maxAge) continue; // Not old enough

        // Read the marker to check transcript_path
        let markerData;
        try {
          markerData = JSON.parse(readFileSync(fp, 'utf8'));
        } catch {
          // Unparsable marker → keep (fail closed)
          continue;
        }

        const tp = markerData?.transcript_path;
        if (typeof tp !== 'string' || tp.length === 0) {
          // Legacy marker without transcript_path → keep (fail closed)
          continue;
        }

        // Only delete if the transcript file no longer exists
        if (existsSync(tp)) continue;

        unlinkSync(fp);
      } catch { /* ignore per-file errors */ }
    }
  } catch { /* ignore */ }
};

// ---------------------------------------------------------------------------
// Read extra taint prefixes from config
// ---------------------------------------------------------------------------

const readExtraTaintPrefixes = (daHome) => {
  try {
    const raw = readFileSync(join(daHome, 'config.json'), 'utf8');
    const cfg = JSON.parse(raw);
    const outputDir = cfg?.output?.dir;
    if (typeof outputDir === 'string' && outputDir.length > 0) return [outputDir];
  } catch { /* fail-open */ }
  return [];
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
  const taintCtx = { daHome, cwd: hookCwd, extraPrefixes };

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
      const readCheck = checkReadTaint(pathToCheck, daHome, extraPrefixes, hookCwd);
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
