/**
 * Pure logic for PostToolUse/PostToolUseFailure content-based taint detection.
 * No I/O, no process.exit — testable as a module.
 *
 * Extracted from post-lock.mjs so it can be unit-tested without spawning the
 * hook subprocess.
 */

// ---------------------------------------------------------------------------
// Extract text from tool output for sentinel scanning
// ---------------------------------------------------------------------------

/**
 * Extract scannable text from the hook payload.
 * PostToolUse: tool_response (Bash={stdout,stderr}, Read=string, others=object)
 * PostToolUseFailure: error (string with "Exit code N\n<stdout+stderr>")
 */
export const extractText = (hookData) => {
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
