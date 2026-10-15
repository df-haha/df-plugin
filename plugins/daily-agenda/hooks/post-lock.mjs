#!/usr/bin/env node

/**
 * PostToolUse hook: content-based (output) taint detection.
 *
 * Scans tool output for the raw sentinel (DA-RAW-3f9a).  If found, marks
 * the session as tainted using the same marker mechanism as the PreToolUse
 * lock.  This is the backstop that makes command-form bypass tricks
 * irrelevant — no matter HOW the raw content was produced, the sentinel
 * in the output triggers the lock.
 *
 * Matched tools: Bash (stdout + stderr), Read (file content), Grep (output).
 * NOT matched: Glob (returns paths only), Monitor (event notifications,
 * not tool results in the transcript).
 *
 * Fail-safe: on parse errors or any exception, output {} (no-op).
 * Never crashes the tool flow.
 */

import { readFileSync } from 'node:fs';
import { sanitizeSessionId } from './lock-core.mjs';
import {
  resolveHome, isSessionTainted, markSessionTainted,
  cleanupOldMarkers, loadSentinel,
} from './lock-io.mjs';

// ---------------------------------------------------------------------------
// Output helper
// ---------------------------------------------------------------------------

const outputNoop = () => {
  process.stdout.write(JSON.stringify({}));
};

// ---------------------------------------------------------------------------
// Extract text from tool_response for sentinel scanning
// ---------------------------------------------------------------------------

const extractText = (toolName, toolResponse) => {
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

  // Grep and others: stringify the structured response
  try {
    return JSON.stringify(toolResponse);
  } catch {
    return '';
  }
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
  const toolResponse = hookData?.tool_response;
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

  // Scan for sentinel
  const sentinel = loadSentinel();
  const text = extractText(toolName, toolResponse);

  if (text.includes(sentinel)) {
    markSessionTainted(daHome, sanitizedId,
      `raw sentinel detected in ${toolName} output`, transcriptPath);
    cleanupOldMarkers(daHome);
  }

  outputNoop();
} catch (err) {
  // Fail-safe: never crash the tool flow
  process.stderr.write(`post-lock.mjs error (fail-safe noop): ${err.message}\n`);
  try { process.stdout.write(JSON.stringify({})); } catch { /* last resort */ }
}
