#!/usr/bin/env node

/**
 * PreToolUse hook: deny non-proxy-path LINE send-class tools.
 *
 * This file is ALWAYS the entry point when run by Claude Code hooks.
 * Tests import isBlockedLineTool from guard-core.mjs instead.
 *
 * Fail-closed for LINE send-class tools (broken input => deny).
 * Fail-open for everything else (broken input => allow, so hook errors
 * don't break the user's entire Claude Code session).
 *
 * df-graph write tools are NOT blocked (see design rationale in report).
 */

import { readFileSync } from 'node:fs';
import { isBlockedLineTool } from './guard-core.mjs';

const outputDeny = (reason) => {
  const result = {
    hookSpecificOutput: {
      hookEventName: 'PreToolUse',
      permissionDecision: 'deny',
      permissionDecisionReason: reason,
    },
  };
  process.stdout.write(JSON.stringify(result));
};

const outputAllow = () => {
  process.stdout.write(JSON.stringify({}));
};

try {
  let input;
  try {
    input = readFileSync(0, 'utf8');
  } catch {
    outputDeny('Hook stdin read failed — denied for safety');
    process.exit(0);
  }

  let hookData;
  try {
    hookData = JSON.parse(input);
  } catch {
    outputDeny('Hook input parse failed — denied for safety');
    process.exit(0);
  }

  const toolName = hookData?.tool_name;
  if (typeof toolName !== 'string') {
    outputDeny('No tool_name in hook input — denied for safety');
    process.exit(0);
  }

  if (isBlockedLineTool(toolName)) {
    outputDeny(`請改用 /agenda-reply 經確認後送出。直接使用 ${toolName} 已被攔截。`);
  } else {
    outputAllow();
  }
} catch (err) {
  // Hook itself crashed — fail-closed for safety
  process.stderr.write(`guard.mjs error: ${err.message}\n`);
  outputDeny(`Hook internal error — denied for safety: ${err.message}`);
}
