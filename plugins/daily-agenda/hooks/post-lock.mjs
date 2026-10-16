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
import { sanitizeSessionId, canonicalize } from './lock-core.mjs';
import {
  resolveHome, isSessionTainted, markSessionTainted,
  cleanupOldMarkers, loadSentinel, readExtraTaintPrefixes,
} from './lock-io.mjs';

// ---------------------------------------------------------------------------
// Output helpers
// ---------------------------------------------------------------------------

const outputNoop = () => {
  process.stdout.write(JSON.stringify({}));
};

/** Emit decision:block when we detect raw content but can't write the marker. */
const outputBlock = (reason) => {
  process.stdout.write(JSON.stringify({
    hookSpecificOutput: {
      hookEventName: 'PostToolUse',
      decision: 'block',
      reason,
    },
  }));
};

// ---------------------------------------------------------------------------
// Extract text from tool output for sentinel / path scanning
// ---------------------------------------------------------------------------

/**
 * Extract scannable text from the hook payload.
 * PostToolUse: tool_response (Bash={stdout,stderr}, Read=string, others=object)
 * PostToolUseFailure: error (string with "Exit code N\n<stdout+stderr>")
 */
const extractText = (hookData) => {
  const eventName = hookData?.hook_event_name;
  const toolName = hookData?.tool_name;

  if (eventName === 'PostToolUseFailure') {
    const err = hookData?.error;
    return typeof err === 'string' ? err : '';
  }

  const toolResponse = hookData?.tool_response;
  if (toolResponse == null) return '';

  // Bash: {stdout, stderr, ...}
  if (toolName === 'Bash') {
    const parts = [];
    if (typeof toolResponse.stdout === 'string') parts.push(toolResponse.stdout);
    if (typeof toolResponse.stderr === 'string') parts.push(toolResponse.stderr);
    return parts.join('\n');
  }

  // Read: raw file content as string
  if (typeof toolResponse === 'string') return toolResponse;

  // Grep, Monitor, and others: stringify the structured response
  try {
    return JSON.stringify(toolResponse);
  } catch {
    return '';
  }
};

// ---------------------------------------------------------------------------
// Protected-path detection in grep-style output (Item 3)
// ---------------------------------------------------------------------------

/**
 * Build the set of protected path prefixes (canonicalized, with both /
 * and the MSYS /c/Users/... form).
 */
const buildProtectedPrefixes = (daHome, extraPrefixes) => {
  const normHome = canonicalize(daHome);
  if (!normHome) return [];
  const prefixes = [
    normHome + '/runs',
    normHome + '/gen',
    normHome + '/output',
    normHome + '/config.json',
  ];
  if (Array.isArray(extraPrefixes)) {
    for (const ep of extraPrefixes) {
      if (typeof ep === 'string' && ep.length > 0) {
        prefixes.push(canonicalize(ep));
      }
    }
  }
  return prefixes.filter(Boolean);
};

/**
 * Normalize a path candidate from grep output for comparison.
 * Handles: backslash→slash, lowercase, MSYS /c/Users → c:/users.
 */
const normForComparison = (p) => {
  let r = p.replace(/\\/g, '/').toLowerCase();
  // MSYS form: /c/Users/... → c:/Users/...
  const msys = r.match(/^\/([a-z])\//);
  if (msys) r = msys[1] + ':' + r.slice(2);
  return r;
};

/**
 * Check if grep-style tool output contains lines referencing protected paths.
 *
 * For Grep tool: check tool_input.output_mode — only 'content' mode returns
 * file content (taint); 'files_with_matches'/'count' only list paths/counts.
 *
 * For Bash: look for grep-style line prefixes `<path>:<lineno>:` or
 * `<path>:` at line start.  Excludes our own JSON outputs (paths appear
 * after `":` inside JSON values, never at line start).
 */
const checkProtectedPaths = (hookData, text, protectedPrefixes) => {
  if (!text || protectedPrefixes.length === 0) return false;

  const toolName = hookData?.tool_name;

  // Grep tool: only content mode
  if (toolName === 'Grep') {
    const outputMode = hookData?.tool_input?.output_mode;
    if (outputMode === 'files_with_matches' || outputMode === 'count') return false;
    // Content mode: check if any path in the response matches protected prefixes
    const normText = normForComparison(text);
    for (const pp of protectedPrefixes) {
      if (normText.includes(pp)) return true;
    }
    return false;
  }

  // Bash / Read / Monitor: line-start grep-prefix heuristic
  const lines = text.split('\n');
  for (const line of lines) {
    if (!line) continue;
    const normLine = normForComparison(line);
    for (const pp of protectedPrefixes) {
      // Match: <protected-path><rest>:<content> at line start
      // This catches grep output like /home/x/.daily-agenda/gen/1/agenda.html:42:<match>
      if (normLine.startsWith(pp)) {
        // Ensure it's a grep-style prefix, not a JSON value
        const rest = normLine.slice(pp.length);
        // Must be followed by path continuation + separator, or direct separator
        // e.g. /file.html:42:text or /file.html:text or /dir/:
        if (/^[^:\s]*:/.test(rest)) return true;
      }
    }
  }
  return false;
};

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
      outputBlock('無法寫入安全鎖定標記。偵測到原始內容但鎖定失敗，為安全起見停止。請檢查 .daily-agenda/taint/ 目錄權限或磁碟空間。');
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
