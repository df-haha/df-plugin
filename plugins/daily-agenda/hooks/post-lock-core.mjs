/**
 * Pure logic for PostToolUse/PostToolUseFailure content-based taint detection.
 * No I/O, no process.exit — testable as a module.
 *
 * Extracted from post-lock.mjs so Windows-path edge cases (MSYS, backslash,
 * relative prefixes) can be unit-tested on Linux without spawning the hook
 * subprocess (which attempts mkdirSync on the fake Windows home path).
 */

import { posix } from 'node:path';
import { canonicalize } from './lock-core.mjs';

// ---------------------------------------------------------------------------
// Extract text from tool output for sentinel / path scanning
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

// ---------------------------------------------------------------------------
// Protected-path detection in grep-style output (Item 3)
// ---------------------------------------------------------------------------

/**
 * Build the set of protected path prefixes (canonicalized).
 */
export const buildProtectedPrefixes = (daHome, extraPrefixes) => {
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
 * Normalize a path candidate for comparison: backslash->slash, collapse
 * repeated separators, lowercase, MSYS /c/Users -> c:/Users.
 */
export const normForComparison = (p) => {
  let r = p.replace(/\\/g, '/');
  r = r.replace(/\/\/+/g, '/');      // collapse repeated /
  r = r.toLowerCase();
  // MSYS form: /c/Users/... -> c:/Users/...
  const msys = r.match(/^\/([a-z])\//);
  if (msys) r = msys[1] + ':' + r.slice(2);
  return r;
};

/**
 * Walk all string leaves of a JSON-like value.  Avoids JSON.stringify
 * so Windows backslash paths aren't double-escaped.
 */
export const collectStringLeaves = (val, out) => {
  if (typeof val === 'string') { out.push(val); return; }
  if (Array.isArray(val)) { for (const v of val) collectStringLeaves(v, out); return; }
  if (val && typeof val === 'object') {
    for (const v of Object.values(val)) collectStringLeaves(v, out);
  }
};

/**
 * Check if a single path candidate (after normForComparison) matches any
 * protected prefix with segment boundary.
 */
export const matchesProtectedPrefix = (normPath, protectedPrefixes) => {
  for (const pp of protectedPrefixes) {
    if (normPath === pp || normPath.startsWith(pp + '/') || normPath.startsWith(pp + ':')) {
      return true;
    }
  }
  return false;
};

/**
 * Resolve a relative path against cwd (normalizing both through normForComparison).
 * Applies posix.normalize to handle ./  ../ segments in the joined result.
 * Returns the normalized resolved path.
 */
export const resolveAgainstCwd = (relPath, cwd) => {
  if (!cwd) return normForComparison(relPath);
  const normCwd = normForComparison(cwd);
  const normRel = normForComparison(relPath);
  // Already absolute?
  if (/^[a-z]:\//.test(normRel) || normRel.startsWith('/')) return normRel;
  // Resolve and normalize (handles ./ and ../ segments)
  const joined = normCwd.replace(/\/$/, '') + '/' + normRel;
  return posix.normalize(joined);
};

/**
 * Check if grep-style tool output contains lines referencing protected paths.
 *
 * For Grep tool: check tool_input.output_mode -- only explicit 'content' mode
 * returns file content (taint); missing output_mode defaults to
 * 'files_with_matches' per the Grep tool spec (no content -> no taint).
 *
 * For structured Grep responses: walk string leaves of the tool_response
 * (never JSON.stringify) so Windows backslash paths are preserved intact.
 *
 * For Bash: look for grep-style line prefixes at line start.
 * Also resolves relative prefixes against hookData.cwd.
 */
export const checkProtectedPaths = (hookData, text, protectedPrefixes) => {
  if (!text || protectedPrefixes.length === 0) return false;

  const toolName = hookData?.tool_name;
  const hookCwd = hookData?.cwd;

  // Grep tool: only explicit content mode
  if (toolName === 'Grep') {
    const outputMode = hookData?.tool_input?.output_mode;
    // M fix: missing output_mode defaults to files_with_matches (no content)
    if (outputMode !== 'content') return false;

    // Walk string leaves of the structured response (H2 fix: no JSON.stringify)
    const toolResponse = hookData?.tool_response;
    if (toolResponse == null) return false;
    const leaves = [];
    collectStringLeaves(toolResponse, leaves);
    for (const leaf of leaves) {
      const normLeaf = normForComparison(leaf);
      if (matchesProtectedPrefix(normLeaf, protectedPrefixes)) return true;
    }
    return false;
  }

  // Bash / Read / Monitor: line-start grep-prefix heuristic
  const lines = text.split('\n');
  for (const line of lines) {
    if (!line) continue;
    // Try the line as-is (absolute paths)
    const normLine = normForComparison(line);
    for (const pp of protectedPrefixes) {
      if (normLine.startsWith(pp)) {
        const rest = normLine.slice(pp.length);
        if (/^[^:\s]*:/.test(rest)) return true;
      }
    }
    // H2 fix: try resolving relative path prefixes against cwd
    if (hookCwd) {
      const grepMatch = line.match(/^([^:\n]+?)(?::(?:\d+:)?)/);
      if (grepMatch) {
        const rawPrefix = grepMatch[1];
        const resolved = resolveAgainstCwd(rawPrefix, hookCwd);
        if (resolved !== normForComparison(rawPrefix)) {
          if (matchesProtectedPrefix(resolved, protectedPrefixes)) return true;
        }
      }
    }
  }
  return false;
};
