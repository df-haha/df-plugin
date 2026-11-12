#!/usr/bin/env node

/**
 * PreToolUse hook: session taint marker.
 *
 * Decides whether this call exposes third-party raw text (Read under runs/,
 * raw-emitting DA subcommands, Monitor on runs/) and, if so, records a taint
 * marker for the session.  Since 2026-11-13 the marker blocks nothing: every
 * call is allowed, locked or not.  Instructions hidden in third-party text
 * are handled by the model (CLI output fences untrusted text); sending LINE
 * messages / creating calendar events still needs the user's confirmation
 * through guard.mjs, which is a separate hook.
 *
 * Always fail-open.  Emits {} (never "allow" — that suppresses permission
 * prompts).
 */

import { readFileSync } from 'node:fs';
import {
  checkBashTaint,
  checkReadTaint,
  checkMonitorPreTaint,
  sanitizeSessionId,
  isUnderRuns,
} from './lock-core.mjs';
import {
  resolveHome, isSessionTainted, markSessionTainted,
  cleanupOldMarkers, appendTaintedDraft, loadTaintedDrafts,
} from './lock-io.mjs';

const outputAllow = () => {
  process.stdout.write(JSON.stringify({}));
};

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

  if (isSessionTainted(daHome, sanitizedId)) {
    // Draft laundering record: runs/ paths written while tainted (best-effort;
    // a failed record never blocks the write).
    if ((toolName === 'Write' || toolName === 'Edit') &&
        isUnderRuns(toolInput?.file_path, { daHome, cwd: hookCwd })) {
      appendTaintedDraft(daHome, toolInput.file_path);
    }
    outputAllow();
    process.exit(0);
  }

  const taintedDrafts = loadTaintedDrafts(daHome);
  const taintCtx = { daHome, cwd: hookCwd, taintedDrafts };
  let taintReason = '';

  if (toolName === 'Bash' || toolName === 'PowerShell') {
    const r = checkBashTaint(toolInput?.command, taintCtx);
    if (r.taint) taintReason = r.reason;
  } else if (toolName === 'Monitor') {
    const monCmd = toolInput?.command;
    if (typeof monCmd === 'string') {
      // Monitor streams lines before PostToolUse fires, so decide up front.
      const r = checkBashTaint(monCmd, taintCtx);
      if (r.taint) taintReason = r.reason;
      else {
        const pre = checkMonitorPreTaint(monCmd, taintCtx);
        if (pre.taint) taintReason = pre.reason;
      }
    }
  } else if (toolName === 'Read' || toolName === 'Grep') {
    const pathToCheck = toolInput?.file_path || toolInput?.path || '';
    const r = checkReadTaint(pathToCheck, daHome, [], hookCwd, taintedDrafts);
    if (r.taint) taintReason = r.reason;
  }

  if (taintReason) {
    if (markSessionTainted(daHome, sanitizedId, taintReason, hookData?.transcript_path)) {
      cleanupOldMarkers(daHome);
    }
  }

  outputAllow();
} catch (err) {
  process.stderr.write(`lock.mjs error (fail-open): ${err.message}\n`);
  outputAllow();
}
