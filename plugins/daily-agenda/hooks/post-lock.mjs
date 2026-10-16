#!/usr/bin/env node

/**
 * PostToolUse + PostToolUseFailure hook: content-based taint detection.
 *
 * Layer 2 backstop — scans tool output for:
 *  (a) the raw sentinel (DA-RAW-3f9a) — makes shell-form bypasses irrelevant
 *  (b) protected-path references in Grep content / Bash grep-style output —
 *      catches ancestor Grep hitting unstamped gen/output files
 *
 * Registered for BOTH PostToolUse and PostToolUseFailure (failed Bash exit
 * still carries stdout in the `error` field).
 *
 * Matched tools: Bash (stdout + stderr / error), Read (file content),
 * Grep (output), Monitor (event stream).
 * NOT matched: Glob (returns paths only).
 *
 * Fail-safe: on parse errors or any exception, output {} (no-op).
 * When markSessionTainted fails, emit decision:block to end the turn.
 * Never crashes the tool flow.
 */

import { readFileSync } from 'node:fs';
import { sanitizeSessionId } from './lock-core.mjs';
import {
  resolveHome, isSessionTainted, markSessionTainted,
  cleanupOldMarkers, loadSentinel, readExtraTaintPrefixes,
} from './lock-io.mjs';
import {
  extractText, buildProtectedPrefixes, checkProtectedPaths,
} from './post-lock-core.mjs';

// ---------------------------------------------------------------------------
// Output helpers
// ---------------------------------------------------------------------------

const outputNoop = () => {
  process.stdout.write(JSON.stringify({}));
};

/**
 * Emit continue:false to hard-stop the session when we detect raw content
 * but can't write the taint marker.  This is the universal hook stop signal
 * (code.claude.com/docs/en/hooks § JSON output: "Every event accepts them",
 * "Takes precedence over any event-specific decision fields").
 * Works for both PostToolUse and PostToolUseFailure events.
 */
const outputStop = (stopReason) => {
  process.stdout.write(JSON.stringify({
    continue: false,
    stopReason,
  }));
};

// Functions extracted to post-lock-core.mjs for testability:
// extractText, buildProtectedPrefixes, checkProtectedPaths,
// normForComparison, collectStringLeaves, matchesProtectedPrefix,
// resolveAgainstCwd

// ---------------------------------------------------------------------------
// Main
// ---------------------------------------------------------------------------

try {
  let input;
  try { input = readFileSync(0, 'utf8'); } catch { outputNoop(); process.exit(0); }

  let hookData;
  try { hookData = JSON.parse(input); } catch { outputNoop(); process.exit(0); }

  const toolName = hookData?.tool_name;
  const sessionId = hookData?.session_id;
  const transcriptPath = hookData?.transcript_path;

  if (typeof toolName !== 'string') { outputNoop(); process.exit(0); }

  const daHome = resolveHome();
  if (!daHome) { outputNoop(); process.exit(0); }

  const sanitizedId = sanitizeSessionId(sessionId);
  if (!sanitizedId) { outputNoop(); process.exit(0); }

  // Fast path: already tainted → nothing to do
  if (isSessionTainted(daHome, sanitizedId)) {
    outputNoop();
    process.exit(0);
  }

  // Extract text from the appropriate field based on event type
  const text = extractText(hookData);

  // Check 1: sentinel detection
  const sentinel = loadSentinel();
  let shouldTaint = false;
  let taintReason = '';

  if (text.includes(sentinel)) {
    shouldTaint = true;
    taintReason = `raw sentinel detected in ${toolName} output`;
  }

  // Check 2: protected-path references in grep-style output
  if (!shouldTaint && text) {
    const extraPrefixes = readExtraTaintPrefixes(daHome);
    const protectedPrefixes = buildProtectedPrefixes(daHome, extraPrefixes);
    if (checkProtectedPaths(hookData, text, protectedPrefixes)) {
      shouldTaint = true;
      taintReason = `tool output references protected DA path`;
    }
  }

  if (shouldTaint) {
    const marked = markSessionTainted(daHome, sanitizedId, taintReason, transcriptPath);
    if (!marked) {
      // Item 5a: marker write failed — block the turn so raw content
      // doesn't proceed in an unlocked session.
      outputStop('daily-agenda 安全鎖無法寫入上鎖紀錄，為安全起見已停止。請檢查 .daily-agenda/taint 資料夾權限或磁碟空間，並開新對話。');
      process.exit(0);
    }
    cleanupOldMarkers(daHome);
  }

  outputNoop();
} catch (err) {
  // Fail-safe: never crash the tool flow
  process.stderr.write(`post-lock.mjs error (fail-safe noop): ${err.message}\n`);
  try { process.stdout.write(JSON.stringify({})); } catch { /* last resort */ }
}
