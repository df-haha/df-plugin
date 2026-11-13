#!/usr/bin/env node

/**
 * PostToolUse + PostToolUseFailure hook: content-based taint detection.
 *
 * Layer 2 backstop — marks the session tainted when tool output contains the
 * raw sentinel (DA-RAW-3f9a), which makes shell-form bypasses irrelevant.
 * (Guessing raw content from protected paths in output was removed: it
 * missed many output shapes and tainted unrelated sessions.)
 *
 * Registered for BOTH PostToolUse and PostToolUseFailure (failed Bash exit
 * still carries stdout in the `error` field).
 *
 * Matched tools: Bash / PowerShell (stdout + stderr / error), Read (file
 * content), Grep (output), Monitor (event stream).
 * NOT matched: Glob (returns paths only).
 *
 * Fail-safe: on parse errors, marker-write failure or any exception, output
 * {} (no-op).  Never blocks or stops the tool flow: since 2026-11-13 the
 * marker is a record only.
 */

import { readFileSync } from 'node:fs';
import { sanitizeSessionId } from './lock-core.mjs';
import {
  resolveHome, isSessionTainted, markSessionTainted,
  cleanupOldMarkers, loadSentinel,
} from './lock-io.mjs';
import { extractText } from './post-lock-core.mjs';

// ---------------------------------------------------------------------------
// Output helpers
// ---------------------------------------------------------------------------

const outputNoop = () => {
  process.stdout.write(JSON.stringify({}));
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

  if (text.includes(loadSentinel())) {
    const taintReason = `raw sentinel detected in ${toolName} output`;
    // The marker is a record only (2026-11-13): a failed write never stops
    // the session.
    if (markSessionTainted(daHome, sanitizedId, taintReason, transcriptPath)) {
      cleanupOldMarkers(daHome);
    }
  }

  outputNoop();
} catch (err) {
  // Fail-safe: never crash the tool flow
  process.stderr.write(`post-lock.mjs error (fail-safe noop): ${err.message}\n`);
  try { process.stdout.write(JSON.stringify({})); } catch { /* last resort */ }
}
