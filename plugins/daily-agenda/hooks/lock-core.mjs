/**
 * Pure logic for the session taint-lock system.
 * No I/O, no process.exit — testable as a module.
 *
 * Fail-open when untainted (internal errors must not block normal sessions).
 * Fail-closed when tainted (internal errors deny the call for safety).
 */

import { readFileSync, realpathSync } from 'node:fs';
import { join, dirname, basename, posix, resolve } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

/** The real plugin scripts directory, derived from this hook's own location. */
const PLUGIN_SCRIPTS_DIR = (() => {
  try {
    // hooks/ is a sibling of scripts/ under the plugin root
    return realpathSync(resolve(__dirname, '..', 'scripts'));
  } catch {
    // If resolve fails (hook installed in unusual location), fall back to string-based
    return resolve(__dirname, '..', 'scripts');
  }
})();

// ---------------------------------------------------------------------------
// Path canonicalization
// ---------------------------------------------------------------------------

/**
 * Canonicalize a path for safe prefix comparison.
 * @param {string} p
 * @param {string} [cwd]
 * @returns {string}
 */
export const canonicalize = (p, cwd) => {
  if (typeof p !== 'string' || p.length === 0) return '';

  let result = p;

  const home = process.env.USERPROFILE || process.env.HOME || '';
  if (result === '~') result = home;
  else if (result.startsWith('~/') || result.startsWith('~\\')) result = home + result.slice(1);

  result = result.replace(/\\/g, '/');

  const isAbsolute = /^[A-Za-z]:\//.test(result) || result.startsWith('/');
  if (!isAbsolute && cwd) {
    result = cwd.replace(/\\/g, '/') + '/' + result;
  }

  result = posix.normalize(result);
  result = result.toLowerCase();

  if (result.length > 1 && result.endsWith('/')) result = result.slice(0, -1);

  if (process.platform === 'win32') {
    try {
      const native = realpathSync.native(p);
      result = posix.normalize(native.replace(/\\/g, '/').toLowerCase());
      if (result.length > 1 && result.endsWith('/')) result = result.slice(0, -1);
    } catch { /* path doesn't exist — use string-based */ }
  }

  return result;
};

/**
 * Keys identifying a draft file in tainted-drafts.json. Record (PreToolUse
 * Write/Edit) and check (Bash --file / Read) both use this, so a draft
 * written through a symlinked/junction parent still matches when shown via
 * the real path (and vice versa). Returns the plain canonical form plus the
 * form with the parent directory resolved through realpath.
 *
 * @param {string} p
 * @param {string} [cwd]
 * @returns {string[]}
 */
export const draftKeys = (p, cwd) => {
  const plain = canonicalize(p, cwd);
  if (!plain) return [];
  const keys = new Set([plain]);
  const resolveParent = (candidate) => {
    try {
      const real = process.platform === 'win32'
        ? realpathSync.native(dirname(candidate))
        : realpathSync(dirname(candidate));
      const k = canonicalize(join(real, basename(candidate)));
      if (k) keys.add(k);
    } catch { /* parent missing — plain form only */ }
  };
  if (typeof p === 'string' && p.length > 0) {
    const home = process.env.USERPROFILE || process.env.HOME || '';
    let raw = p;
    if (raw === '~' || raw.startsWith('~/') || raw.startsWith('~\\')) raw = home + raw.slice(1);
    const isAbs = /^[A-Za-z]:[\\/]/.test(raw) || raw.startsWith('/') || raw.startsWith('\\');
    if (!isAbs && cwd) raw = join(cwd, raw);
    resolveParent(raw);
  }
  resolveParent(plain);
  return [...keys];
};

/** True if any key of `p` is in the tainted-drafts set. */
export const isTaintedDraft = (taintedDrafts, p, cwd) => {
  if (!(taintedDrafts instanceof Set) || taintedDrafts.size === 0) return false;
  return draftKeys(p, cwd).some((k) => taintedDrafts.has(k));
};

// ---------------------------------------------------------------------------
// Registry
// ---------------------------------------------------------------------------

let _registryCache = null;

export const loadRegistry = () => {
  if (_registryCache) return _registryCache;
  try {
    const data = JSON.parse(readFileSync(join(__dirname, 'raw-commands.json'), 'utf8'));
    _registryCache = new Set(data.commands || []);
  } catch {
    _registryCache = new Set(['chats', 'outlook-preview', 'raw', 'scan']);
  }
  return _registryCache;
};

// ---------------------------------------------------------------------------
// Taint detection
// ---------------------------------------------------------------------------

/**
 * N5-safe registry check: match `python [...] -m daily_agenda <subcommand>`
 * where <subcommand> is a registry token. Tolerates the known root flag
 * --progress-file between daily_agenda and the subcommand; unknown flags
 * trigger taint (fail closed).
 *
 * Does NOT false-taint developer commands like `grep -rn chats scripts/`.
 */
// Known root flags that take a value after a space (not just =).
const KNOWN_VALUE_FLAGS = new Set(['--progress-file']);

/**
 * Shell-aware tokenizer: split on unquoted whitespace, respecting "..." and
 * '...' quotes.  Follows bash semantics for backslash escapes:
 *
 *  - Outside quotes: `\x` → `x` (the backslash is consumed).
 *  - Inside double quotes: `\"` → `"`, `\\` → `\`; other `\x` kept literal
 *    (preserves Windows paths like `"C:\Users\..."`).
 *  - Inside single quotes: everything literal (no escape processing).
 *
 * Consequence: an unquoted `C:\Users\x` becomes `C:Usersx` (same as bash;
 * the skill always quotes paths).  A double-quoted path ending in `\"`
 * doesn't close the quote (same as bash; would be a broken command).
 */
export const shellTokenize = (str) => {
  const tokens = [];
  let current = '';
  let inSingle = false;
  let inDouble = false;
  for (let i = 0; i < str.length; i++) {
    const ch = str[i];
    if (inSingle) {
      if (ch === "'") { inSingle = false; continue; }
      current += ch;
    } else if (inDouble) {
      if (ch === '\\' && i + 1 < str.length) {
        const next = str[i + 1];
        // In double quotes, bash only escapes: \\ \" \` \$ \newline
        if (next === '\\' || next === '"') {
          current += next;
          i++;
          continue;
        }
        // All other \x inside double quotes: keep both characters literal
        current += ch;
      } else if (ch === '"') {
        inDouble = false;
      } else {
        current += ch;
      }
    } else if (ch === '\\' && i + 1 < str.length) {
      // Outside quotes: \x → x (bash behavior)
      current += str[i + 1];
      i++;
    } else if (ch === "'") {
      inSingle = true;
    } else if (ch === '"') {
      inDouble = true;
    } else if (/\s/.test(ch)) {
      if (current.length > 0) { tokens.push(current); current = ''; }
    } else {
      current += ch;
    }
  }
  if (current.length > 0) tokens.push(current);
  return tokens;
};

/**
 * Interpreter regex fragment: matches python/py/python3.13/python.exe etc.,
 * case-insensitive.  Requires the match to follow a shell boundary or start
 * of string.  Does NOT match `pytest` (after `py`, digits/`.exe` are optional
 * but the next char must be `\s` or end — `t` fails).
 */
const _INTERP_RE = /(?:^|[\s;|&(`])(?:\S*[/\\])?(?:py|python)w?\d*(?:\.\d+)*(?:\.exe)?(?=\s)/i;

const _checkRegistryInvocation = (command) => {
  const registry = loadRegistry();

  // ── Form A: `-m` module invocation ──
  // Tolerates interpreter flags (e.g. -P) between the interpreter and -m.
  const anchorA = command.match(
    /(?:py|python)w?\d*(?:\.\d+)*(?:\.exe)?\s+(?:\S+\s+)*?-m\s+(daily_agenda(?:\.\S*)?)\b(.*)/i
  );
  if (anchorA) {
    const moduleName = anchorA[1];
    // Non-canonical module name (e.g. daily_agenda.__main__) → taint
    if (moduleName.toLowerCase() !== 'daily_agenda') {
      return { taint: true, reason: `non-canonical module: ${moduleName}` };
    }

    const rest = anchorA[2].trim();
    if (!rest) return { taint: false };

    // Fail closed on unexpanded shell variables / backticks in the rest
    if (/[$`]|%\w+%/.test(rest)) {
      return { taint: true, reason: 'unexpanded shell variable or backtick after daily_agenda' };
    }

    const tokens = shellTokenize(rest);
    let skipNext = false;
    for (const token of tokens) {
      if (skipNext) { skipNext = false; continue; }

      if (token.startsWith('--')) {
        if (token.includes('=')) {
          const flagName = token.slice(0, token.indexOf('='));
          if (!KNOWN_VALUE_FLAGS.has(flagName)) {
            return { taint: true, reason: `unknown root flag: ${flagName}` };
          }
          continue;
        }
        if (KNOWN_VALUE_FLAGS.has(token)) {
          skipNext = true;
          continue;
        }
        return { taint: true, reason: `unknown root flag: ${token}` };
      }
      if (token.startsWith('-') && token.length > 1) {
        return { taint: true, reason: `unknown root flag: ${token}` };
      }

      // First non-flag token is the subcommand
      if (registry.has(token)) {
        return { taint: true, reason: `raw-emitting subcommand: ${token}` };
      }
      return { taint: false };
    }
    return { taint: false };
  }

  // ── Form B: `-c` inline code mentioning daily_agenda ──
  if (_INTERP_RE.test(command) &&
      /\s+-c\s/.test(command) &&
      /daily_agenda/i.test(command)) {
    return { taint: true, reason: 'python -c invocation mentioning daily_agenda' };
  }

  // ── Form C: script-path invocation (e.g. `python daily_agenda/__main__.py`) ──
  // Matches an interpreter followed by flags (not -m/-c) and then a path
  // containing `daily_agenda`.
  const anchorC = command.match(
    /(?:py|python)w?\d*(?:\.\d+)*(?:\.exe)?\s+(?:-(?!m\s|-m\s|c\s|-c\s)\S*\s+)*\S*daily_agenda(?:[/\\]|\b)/i
  );
  if (anchorC) {
    // Don't match if it was actually a -m form (already handled above)
    if (!/\s-m\s/.test(command.slice(0, anchorC.index + anchorC[0].length))) {
      return { taint: true, reason: 'script-path invocation of daily_agenda' };
    }
  }

  return { taint: false };
};

// ---------------------------------------------------------------------------
// Canonical DA invocation parser (shared by tainted allowlist + exemption)
// ---------------------------------------------------------------------------

/**
 * Parse a canonical DA invocation from a Bash command.
 * Returns {ok, projectPath, subcommand, argsStr} or {ok:false}.
 * This is the ONLY canonical-form regex in lock-core.mjs.  Used by both
 * tainted allowlist (checkTaintedBash) and untainted exemption (checkBashTaint),
 * and by checkMonitorCommand, so they cannot drift.
 *
 * Canonical form: `uv run --quiet --project "..." python -I -X utf8 -m daily_agenda <sub> ...`
 * `-I` = isolated mode (ignores PYTHONPATH, cwd, user site-packages).
 * `-X utf8` = UTF-8 mode (all streams default to UTF-8, replacing PYTHONIOENCODING).
 */
export const _parseCanonicalDA = (command) => {
  // Pre-checks: sanitize ${CLAUDE_PLUGIN_ROOT}; reject metachar / $ / multiline
  const sanitized = command.replace(/\$\{CLAUDE_PLUGIN_ROOT\}/g, '__PLUGIN_ROOT__');
  if (/[;|&`><()\n\r{}*?~]/.test(sanitized)) return { ok: false };
  if (/\$/.test(sanitized)) return { ok: false };
  if (command.includes('\n') || command.includes('\r')) return { ok: false };

  const m = command.match(
    /^uv\s+run\s+--quiet\s+--project\s+"([^"]+)"\s+python\s+-I\s+-X\s+utf8\s+-m\s+daily_agenda\s+([a-z][-a-z0-9]*)((?:\s+.*)?)$/
  );
  if (!m) return { ok: false };

  const projectPath = m[1];
  if (!_isAllowedProjectPath(projectPath)) return { ok: false };

  return { ok: true, projectPath, subcommand: m[2], argsStr: (m[3] || '').trim() };
};

// ---------------------------------------------------------------------------
// Path-rule helpers (shared between tainted allowlist + untainted exemption)
// ---------------------------------------------------------------------------

/** Allowed draft basenames that can appear under runs/ in --file values. */
const DRAFT_BASENAMES = new Set([
  'criteria-draft.md', 'profile-draft.md', 'coverage-draft.json',
]);

/**
 * Check if a path is a valid progress-file: under <home>/runs/progress/,
 * ends with .jsonl, single depth after progress/.
 */
export const _isProgressFilePath = (filePath, ctx) => {
  if (!ctx?.daHome) return false;
  const normFile = canonicalize(filePath);
  const normHome = canonicalize(ctx.daHome);
  if (!normFile || !normHome) return false;
  if (!normFile.endsWith('.jsonl')) return false;
  const prefix = normHome + '/runs/progress/';
  if (!normFile.startsWith(prefix)) return false;
  // Must be one level deep (no further / after progress/)
  const remainder = normFile.slice(prefix.length);
  if (remainder.includes('/')) return false;
  return true;
};

/**
 * Check if a path is a valid draft file: under <home>/runs/, basename is a
 * known draft name, and no path segment is 'raw' (to prevent reading raw data
 * files that happen to be named like drafts).
 */
export const _isDraftFilePath = (filePath, ctx) => {
  if (!ctx?.daHome) return false;
  const normFile = canonicalize(filePath);
  const normHome = canonicalize(ctx.daHome);
  if (!normFile || !normHome) return false;
  if (!normFile.startsWith(normHome + '/runs/')) return false;
  // Check basename
  const basename = normFile.split('/').pop();
  if (!DRAFT_BASENAMES.has(basename)) return false;
  // Reject if any segment is 'raw'
  const relative = normFile.slice((normHome + '/runs/').length);
  const segments = relative.split('/');
  if (segments.some(s => s === 'raw')) return false;
  return true;
};

/**
 * Subcommand → allowed --file draft basename(s).  Subcommands not listed here
 * get no --file exemption in mechanism 2.
 *
 * `draft show` accepts any of the three draft basenames.
 * `criteria save` and `refine apply` accept only criteria-draft.md.
 * `profile save` accepts only profile-draft.md.
 * `profile coverage save` accepts only coverage-draft.json.
 */
const _SUBCMD_DRAFT_MAP = {
  'draft show':             DRAFT_BASENAMES,
  'criteria save':          new Set(['criteria-draft.md']),
  'refine apply':           new Set(['criteria-draft.md']),
  'profile save':           new Set(['profile-draft.md']),
  'profile coverage save':  new Set(['coverage-draft.json']),
};

/**
 * Like _isDraftFilePath but restricted to basenames allowed for a specific
 * subcommand + sub-args combination.
 */
const _isDraftFileForSubcommand = (filePath, ctx, subcommand, argsStr) => {
  if (!ctx?.daHome) return false;
  const normFile = canonicalize(filePath);
  const normHome = canonicalize(ctx.daHome);
  if (!normFile || !normHome) return false;
  if (!normFile.startsWith(normHome + '/runs/')) return false;

  const basename = normFile.split('/').pop();

  // Build lookup key from subcommand + leading sub-args word(s)
  // e.g. subcommand='criteria', argsStr='save --file ...' → 'criteria save'
  //      subcommand='profile', argsStr='coverage save --file ...' → 'profile coverage save'
  const subArgWords = argsStr.split(/\s+/);
  let key = subcommand;
  // Try longest match first: "profile coverage save", "profile save", "profile"
  for (let n = Math.min(subArgWords.length, 3); n >= 1; n--) {
    const candidate = subcommand + ' ' + subArgWords.slice(0, n).join(' ');
    if (_SUBCMD_DRAFT_MAP[candidate]) {
      key = candidate;
      break;
    }
  }

  const allowed = _SUBCMD_DRAFT_MAP[key];
  if (!allowed || !allowed.has(basename)) return false;

  // Reject if any segment is 'raw'
  const relative = normFile.slice((normHome + '/runs/').length);
  const segments = relative.split('/');
  if (segments.some(s => s === 'raw')) return false;
  return true;
};

/**
 * Build the set of canonicalized paths exempt from mechanism 2 taint, based
 * on the canonical DA invocation's subcommand and flag values.
 *
 * Exemption rules (subcommand-bound):
 * - --progress-file (root flag, any subcommand): progress path only
 * - progress show|watch --file: progress path only
 * - draft show --file: any draft basename
 * - criteria save / refine apply --file: criteria-draft.md only
 * - profile save --file: profile-draft.md only
 * - profile coverage save --file: coverage-draft.json only
 * - All other subcommand --file: no exemption
 */
const _buildExemptSet = (command, ctx) => {
  const exempt = new Set();
  const canonical = _parseCanonicalDA(command);
  if (!canonical.ok || !ctx?.daHome) return exempt;

  const { subcommand, argsStr } = canonical;

  // 1. --progress-file (root flag, any subcommand): progress path only
  if (!argsStr.includes('--progress-file=')) {
    const pfVals = _extractAllFlag(argsStr, '--progress-file');
    if (pfVals.length === 1 && _isProgressFilePath(pfVals[0], ctx)) {
      exempt.add(canonicalize(pfVals[0]));
    }
  }

  // 2. --file: meaning depends on subcommand
  if (!argsStr.includes('--file=')) {
    const fVals = _extractAllFlag(argsStr, '--file');
    if (fVals.length === 1) {
      const isProgressSub = subcommand === 'progress' &&
        (/^show\b/.test(argsStr) || /^watch\b/.test(argsStr));

      if (isProgressSub) {
        // progress show|watch --file → progress path only
        if (_isProgressFilePath(fVals[0], ctx)) {
          exempt.add(canonicalize(fVals[0]));
        }
      } else if (_isDraftFileForSubcommand(fVals[0], ctx, subcommand, argsStr)) {
        // Subcommand-specific draft file
        exempt.add(canonicalize(fVals[0]));
      }
      // else: no exemption (e.g. unknown subcommand or wrong basename)
    }
  }

  return exempt;
};

// ---------------------------------------------------------------------------
// Taint detection — mechanism 2 path-reference check
// ---------------------------------------------------------------------------

/**
 * Check if a Bash command should taint the session.
 *
 * Two mechanisms:
 * 1. Registry: `python -m daily_agenda <registry-subcommand>` (N5-safe)
 * 2. Path references: command tokens that resolve inside DA taint dirs
 *
 * Mechanism 2 exempts flag values from canonical DA invocations that point to
 * allowed locations (progress files under runs/progress/, draft files under
 * runs/ with known basenames).  This prevents `/agenda run --progress-file`
 * from locking a session that the user decided should stay unlocked.
 *
 * @param {string} command
 * @param {{ daHome?: string, cwd?: string, extraPrefixes?: string[] }} [ctx]
 */
export const checkBashTaint = (command, ctx) => {
  if (typeof command !== 'string') return { taint: false };

  // 1. Registry check (only for real DA invocations, not grep/git commands)
  if (/daily_agenda/i.test(command)) {
    const regResult = _checkRegistryInvocation(command);
    if (regResult.taint) return regResult;
  }

  // 2. Path-reference check: tokenize, expand, canonicalize, check
  if (ctx?.daHome) {
    const normHome = canonicalize(ctx.daHome);
    if (!normHome) return { taint: false };

    // Build exempt set: canonicalized paths from canonical DA flag values,
    // bound to subcommand semantics (item 2).
    const exempt = _buildExemptSet(command, ctx);

    // Draft laundering check: if any exempt path was written in a tainted
    // session, revoke its exemption and taint immediately.
    if (ctx.taintedDrafts instanceof Set && ctx.taintedDrafts.size > 0) {
      for (const ep of exempt) {
        if (isTaintedDraft(ctx.taintedDrafts, ep)) {
          return { taint: true, reason: `draft file written in a locked session: ${ep}` };
        }
      }
    }

    const taintDirs = [
      normHome + '/runs', normHome + '/gen', normHome + '/output',
    ];
    if (Array.isArray(ctx.extraPrefixes)) {
      for (const ep of ctx.extraPrefixes) {
        if (typeof ep === 'string' && ep.length > 0) taintDirs.push(canonicalize(ep));
      }
    }

    // Tokenize: split on whitespace and shell operators, then drop every quote
    // char — the shell concatenates `~/.daily-agenda/"runs"/x` into one path.
    const tokens = command.split(/[\s;|&<>()`]+/);
    for (const token of tokens) {
      // Process ANSI-C $'...' BEFORE stripping quotes (it needs the $' wrapper)
      let clean = token.replace(/\$'([^']*)'/g, (_, inner) =>
        inner.replace(/\\n/g, '\n').replace(/\\t/g, '\t')
             .replace(/\\'/g, "'").replace(/\\\\/g, '\\')
             .replace(/\\(.)/g, '$1')
      );
      // Strip remaining quotes
      clean = clean.replace(/["']/g, '');
      if (!clean || clean.startsWith('-')) continue;

      // Bash unescape: unquoted \x → x (same rule as shellTokenize)
      clean = clean.replace(/\\(.)/g, '$1');

      // Expand shell variables in the token
      let expanded = clean;
      const homeStr = process.env.USERPROFILE || process.env.HOME || '';
      if (homeStr) {
        const normHomePath = homeStr.replace(/\\/g, '/').toLowerCase();
        expanded = expanded.replace(/^~/g, normHomePath);
        // $HOME, ${HOME}, %USERPROFILE%, $USERPROFILE
        expanded = expanded.replace(/\$HOME\b|\$\{HOME\}|%USERPROFILE%|\$USERPROFILE\b/gi, normHomePath);
      }

      const normToken = canonicalize(expanded, ctx.cwd);
      if (!normToken) continue;

      // Skip exempt paths (canonical DA flag values)
      if (exempt.has(normToken)) continue;

      // Check if the token is inside a taint dir
      for (const td of taintDirs) {
        if (normToken === td || normToken.startsWith(td + '/')) {
          return { taint: true, reason: `command references taint-protected dir: ${clean}` };
        }
      }

      // Check if token is/inside daHome itself (covers `cd ~/.daily-agenda`)
      if (normToken === normHome || normToken.startsWith(normHome + '/')) {
        if (normToken === normHome) {
          return { taint: true, reason: `command references DA home: ${clean}` };
        }
        // config.json contains raw chat names
        if (normToken === normHome + '/config.json') {
          return { taint: true, reason: `command references DA config (raw chat names): ${clean}` };
        }
        const relative = normToken.slice(normHome.length + 1);
        const firstComponent = relative.split('/')[0];
        if (['runs', 'gen', 'output'].includes(firstComponent)) {
          return { taint: true, reason: `command references taint-protected dir: ${clean}` };
        }
      }
    }
  }

  return { taint: false };
};

/**
 * Check if a Read/Grep/Glob path targets DA taint-protected directories
 * or a draft file that was written during a tainted session.
 *
 * @param {string} filePath
 * @param {string} daHome
 * @param {string[]} [extraPrefixes]
 * @param {string} [cwd]
 * @param {Set<string>} [taintedDrafts] - canonicalized paths from tainted-drafts.json
 */
export const checkReadTaint = (filePath, daHome, extraPrefixes, cwd, taintedDrafts) => {
  if (typeof filePath !== 'string' || typeof daHome !== 'string') return { taint: false };
  if (filePath.length === 0) return { taint: false };

  const normPath = canonicalize(filePath, cwd);
  const normHome = canonicalize(daHome);
  if (!normPath || !normHome) return { taint: false };

  // Draft laundering check: taint if reading a draft written in a tainted session
  if (isTaintedDraft(taintedDrafts, filePath, cwd)) {
    return { taint: true, reason: `Read of draft written in a locked session: ${filePath}` };
  }

  const taintDirs = [normHome + '/runs', normHome + '/gen', normHome + '/output'];

  if (Array.isArray(extraPrefixes)) {
    for (const ep of extraPrefixes) {
      if (typeof ep !== 'string' || ep.length === 0) continue;
      taintDirs.push(canonicalize(ep, cwd));
    }
  }

  for (const td of taintDirs) {
    if (!td) continue;
    if (normPath === td || normPath.startsWith(td + '/')) {
      return { taint: true, reason: `Read under taint-protected dir: ${filePath}` };
    }
  }

  // config.json contains raw chat names in excluded_chats[].name
  if (normPath === normHome + '/config.json') {
    return { taint: true, reason: `Read of DA config (contains raw chat names): ${filePath}` };
  }

  // Narrow exception: path IS daHome itself
  if (normPath === normHome) {
    return { taint: true, reason: `Read/Grep of DA home: ${filePath}` };
  }

  return { taint: false };
};

// ---------------------------------------------------------------------------
// Shell metachar detection
// ---------------------------------------------------------------------------

const SHELL_METACHARS = /[;|&`$><\n(){}*?~]/;

export const hasShellMetachars = (command) => SHELL_METACHARS.test(command);

// ---------------------------------------------------------------------------
// Session ID sanitization
// ---------------------------------------------------------------------------

export const sanitizeSessionId = (sessionId) => {
  if (typeof sessionId !== 'string') return null;
  if (sessionId.length === 0 || sessionId.length > 256) return null;
  if (sessionId.includes('..') || sessionId.includes('/') || sessionId.includes('\\')) return null;
  return sessionId.replace(/[^a-zA-Z0-9_-]/g, '_');
};

// ---------------------------------------------------------------------------
// Unified deny reason
// ---------------------------------------------------------------------------

const DENY_BASE = '這個對話已讀取外部訊息原文，為了防止訊息裡藏的指令被執行，已鎖定';
const DENY_DEFAULT = `${DENY_BASE}：只能繼續使用 daily-agenda 的功能。要做其他事請開新對話。`;

const deny = (suffix) => ({
  allow: false,
  reason: suffix ? `${DENY_BASE}${suffix}` : DENY_DEFAULT,
});

// ---------------------------------------------------------------------------
// Tainted-state tool allowlist
// ---------------------------------------------------------------------------

const TAINTED_ALLOWED_TOOLS = new Set([
  'Read', 'Glob', 'Grep', 'AskUserQuestion',
  'TodoRead', 'TodoWrite', 'Monitor', 'TaskStop', 'ToolSearch',
]);

const ACTIONS_PREFIX = 'mcp__plugin_daily-agenda_actions__';

const ALLOWED_SKILLS = new Set([
  'agenda', 'agenda-setup', 'agenda-reply',
  'daily-agenda:agenda', 'daily-agenda:agenda-setup', 'daily-agenda:agenda-reply',
]);

// Fully denied (cannot be scoped safely)
const DENIED_SUBCOMMANDS = new Set([
  'schedule', 'uninstall', 'runtime', 'trust', 'ctx', 'docs',
]);

export const checkTaintedAllow = (toolName, toolInput, ctx) => {
  if (typeof toolName !== 'string') return deny('（工具名稱無效）');

  if (TAINTED_ALLOWED_TOOLS.has(toolName)) {
    if (toolName === 'Monitor') return checkMonitorCommand(toolInput, ctx);
    return { allow: true };
  }

  if (toolName.startsWith(ACTIONS_PREFIX)) return { allow: true };

  if (toolName === 'Skill') {
    const skillName = toolInput?.skill;
    if (typeof skillName === 'string' && ALLOWED_SKILLS.has(skillName)) return { allow: true };
    return deny(`（skill: ${skillName || '(none)'}）。要做其他事請開新對話。`);
  }

  if (toolName === 'Bash') return checkTaintedBash(toolInput, ctx);
  if (toolName === 'Write' || toolName === 'Edit') return checkTaintedWrite(toolInput, ctx);

  return deny();
};

// ---------------------------------------------------------------------------
// Shared project path validation (H2 + L2: single implementation for Bash
// and Monitor; realpath equality, no suffix matching)
// ---------------------------------------------------------------------------

/**
 * Check whether a --project path points to the real plugin scripts directory.
 * Accepts both:
 *  - The ${CLAUDE_PLUGIN_ROOT}/scripts template form (unexpanded by the hook)
 *  - An absolute path that resolves to the same directory as PLUGIN_SCRIPTS_DIR
 *
 * On Windows: case-insensitive comparison (via realpath normalization).
 * Nonexistent paths → deny (fail closed in tainted state).
 *
 * Exported for testing.
 */
export const _isAllowedProjectPath = (projectPath) => {
  // Template form: always allowed (the shell expands it at runtime)
  const norm = projectPath.replace(/\\/g, '/');
  if (norm === '__PLUGIN_ROOT__/scripts' ||
      norm === '${CLAUDE_PLUGIN_ROOT}/scripts') {
    return true;
  }

  // Absolute path: compare via realpath
  try {
    const resolvedCandidate = realpathSync(projectPath);
    const resolvedPlugin = realpathSync(PLUGIN_SCRIPTS_DIR);
    // On Windows, realpath returns native case but we compare case-insensitively.
    if (process.platform === 'win32') {
      return resolvedCandidate.toLowerCase() === resolvedPlugin.toLowerCase();
    }
    return resolvedCandidate === resolvedPlugin;
  } catch {
    // Path doesn't exist or can't be resolved → deny (fail closed)
    return false;
  }
};

// ---------------------------------------------------------------------------
// Tainted Bash check
// ---------------------------------------------------------------------------

/**
 * Extract all occurrences of a --flag from an args string.
 * Handles both `--flag value` and `--flag=value` forms.
 * Returns array of extracted values (empty if not present).
 */
const _extractAllFlag = (argsStr, flagName) => {
  const values = [];
  // --flag=value form
  const eqRe = new RegExp(`${flagName}=("([^"]+)"|'([^']+)'|(\\S+))`, 'g');
  let m;
  while ((m = eqRe.exec(argsStr)) !== null) {
    values.push(m[2] || m[3] || m[4]);
  }
  // --flag value form (space-separated)
  const spRe = new RegExp(`${flagName}\\s+("([^"]+)"|'([^']+)'|(\\S+))`, 'g');
  while ((m = spRe.exec(argsStr)) !== null) {
    values.push(m[2] || m[3] || m[4]);
  }
  return values;
};

export const checkTaintedBash = (toolInput, ctx) => {
  const command = toolInput?.command;
  if (typeof command !== 'string') return deny('（Bash：指令格式無效）');
  if (command.includes('\n') || command.includes('\r')) return deny('（Bash：不允許多行指令）');

  const sanitized = command.replace(/\$\{CLAUDE_PLUGIN_ROOT\}/g, '__PLUGIN_ROOT__');
  if (/[;|&`><()\n\r{}*?~]/.test(sanitized)) return deny('（Bash：不允許 shell 特殊字元）');
  if (/\$/.test(sanitized)) return deny('（Bash：不允許變數展開，$CLAUDE_PLUGIN_ROOT 除外）');

  const canonical = _parseCanonicalDA(command);
  if (!canonical.ok) return deny();

  const subcommand = canonical.subcommand;
  const argsStr = canonical.argsStr;

  // F3/item2: validate --progress-file (reject duplicates, =form, path)
  const pfResult = _validateFlagOccurrences(argsStr, '--progress-file', ctx, true);
  if (pfResult) return pfResult;

  // Item3: validate --file (reject duplicates, =form, path)
  // Only validate if it's present (some subcommands don't use --file)
  const ffResult = _validateFlagOccurrences(argsStr, '--file', ctx, false);
  if (ffResult) return ffResult;

  if (command.includes('--with')) return deny('（Bash：不允許 --with）');

  // Subcommand-specific rules

  // Item1: consent --status is read-only (allowed); consent --accept is denied
  if (subcommand === 'consent') {
    if (argsStr === '--status' || argsStr === '') return { allow: true };
    return deny('（Bash：不允許 consent --accept）');
  }

  if (subcommand === 'config') {
    if (argsStr.startsWith('get') || argsStr === '') return { allow: true };
    return deny('（Bash：不允許 config set）');
  }

  if (subcommand === 'criteria') {
    if (argsStr.startsWith('show') || argsStr === '') return { allow: true };
    if (argsStr.startsWith('draft')) return { allow: true };
    if (argsStr.startsWith('save')) return _requireFileUnderRuns(argsStr, 'criteria', ctx);
    if (argsStr.startsWith('restore')) return { allow: true };
    return deny('（Bash：不允許修改判斷準則）');
  }

  if (subcommand === 'refine') {
    if (['status', 'draft', 'skip'].some(s => argsStr.startsWith(s)) || argsStr === '') {
      return { allow: true };
    }
    if (argsStr.startsWith('apply')) return _requireFileUnderRuns(argsStr, 'refine apply', ctx);
    return deny('（Bash：不允許此 refine 操作）');
  }

  if (subcommand === 'profile') {
    if (argsStr === '' || argsStr.startsWith('show') || argsStr.startsWith('list') || argsStr.startsWith('next-question')) {
      return { allow: true };
    }
    if (argsStr.startsWith('restore')) return { allow: true };
    if (argsStr.startsWith('cadence')) return { allow: true };
    if (argsStr.startsWith('save')) return _requireFileUnderRuns(argsStr, 'profile save', ctx);
    if (argsStr.startsWith('coverage save')) return _requireFileUnderRuns(argsStr, 'profile coverage save', ctx);
    if (argsStr.startsWith('coverage')) return { allow: true }; // coverage (read) without save
    return deny('（Bash：不允許此 profile 操作）');
  }

  if (DENIED_SUBCOMMANDS.has(subcommand)) return deny(`（Bash：不允許 ${subcommand}）`);

  return { allow: true };
};

// ---------------------------------------------------------------------------
// Flag validation helpers
// ---------------------------------------------------------------------------

/**
 * Validate a --flag: reject duplicates and =form, validate path under
 * the allowed directory.
 *
 * @param {string} argsStr
 * @param {string} flagName - e.g. '--progress-file' or '--file'
 * @param {object} ctx
 * @param {boolean} isProgressFile - true → require under runs/progress/*.jsonl
 * @returns {object|null} deny result or null if OK
 */
const _validateFlagOccurrences = (argsStr, flagName, ctx, isProgressFile) => {
  // Reject =form
  if (argsStr.includes(flagName + '=')) {
    return deny(`（Bash：${flagName} 不能使用 = 格式）`);
  }

  // Extract all occurrences
  const values = _extractAllFlag(argsStr, flagName);
  if (values.length === 0) return null; // not present = OK
  if (values.length > 1) {
    return deny(`（Bash：${flagName} 不能重複指定）`);
  }

  const filePath = values[0];

  if (isProgressFile) {
    if (!filePath.endsWith('.jsonl')) {
      return deny(`（Bash：${flagName} 必須是 .jsonl 檔）`);
    }
    const daHome = ctx?.daHome;
    if (daHome) {
      const normPf = canonicalize(filePath);
      const normHome = canonicalize(daHome);
      if (!normPf.startsWith(normHome + '/runs/progress')) {
        return deny(`（Bash：${flagName} 只能指向 .daily-agenda/runs/progress/ 內）`);
      }
    }
  } else {
    // --file: validate under runs/
    const daHome = ctx?.daHome;
    if (daHome) {
      const normFile = canonicalize(filePath);
      const normHome = canonicalize(daHome);
      if (!normFile.startsWith(normHome + '/runs/') && normFile !== normHome + '/runs') {
        return deny(`（Bash ${flagName}：只能指向 .daily-agenda/runs/ 內的檔案）`);
      }
    }
  }

  return null;
};

/**
 * Require --file to be present and under <home>/runs/.
 */
const _requireFileUnderRuns = (argsStr, label, ctx) => {
  // Check =form is already rejected by _validateFlagOccurrences
  const values = _extractAllFlag(argsStr, '--file');
  if (values.length === 0) return deny(`（Bash ${label}：需要 --file 參數）`);
  // Validation already done by _validateFlagOccurrences; just allow
  return { allow: true };
};

// ---------------------------------------------------------------------------
// Monitor check
// ---------------------------------------------------------------------------

export const checkMonitorCommand = (toolInput, ctx) => {
  const command = toolInput?.command;
  if (typeof command !== 'string' || command.length === 0) {
    return deny('（Monitor：需要指定 daily-agenda 進度追蹤指令）');
  }

  // Use the shared canonical parser (item 3: single canonical-form regex)
  const canonical = _parseCanonicalDA(command);
  if (!canonical.ok) return deny('（Monitor：只能追蹤 daily-agenda 進度）');

  // Must be `progress watch`
  if (canonical.subcommand !== 'progress' || !/^watch\b/.test(canonical.argsStr)) {
    return deny('（Monitor：只能追蹤 daily-agenda 進度）');
  }

  // Validate --file in progress watch args
  const argsAfterWatch = canonical.argsStr.slice(5).trim(); // skip "watch"
  const pfResult = _validateFlagOccurrences(argsAfterWatch, '--file', ctx, true);
  if (pfResult) return pfResult;

  return { allow: true };
};

// ---------------------------------------------------------------------------
// Tainted Write/Edit scope check
// ---------------------------------------------------------------------------

export const checkTaintedWrite = (toolInput, ctx) => {
  const filePath = toolInput?.file_path;
  if (typeof filePath !== 'string') return deny('（Write：路徑無效）');

  const daHome = ctx?.daHome;
  if (daHome) {
    const normFile = canonicalize(filePath);
    const normHome = canonicalize(daHome);
    if (normFile.startsWith(normHome + '/runs/') && normFile.endsWith('/criteria-draft.md')) {
      return { allow: true };
    }
    if (normFile.startsWith(normHome + '/runs/') && normFile.endsWith('/profile-draft.md')) {
      return { allow: true };
    }
    if (normFile.startsWith(normHome + '/runs/') && normFile.endsWith('/coverage-draft.json')) {
      return { allow: true };
    }
  } else {
    if (/[/\\]\.daily-agenda[/\\]runs[/\\][^/\\]+[/\\]criteria-draft\.md$/i.test(filePath)) {
      return { allow: true };
    }
    if (/[/\\]\.daily-agenda[/\\]runs[/\\][^/\\]+[/\\]profile-draft\.md$/i.test(filePath)) {
      return { allow: true };
    }
    if (/[/\\]\.daily-agenda[/\\]runs[/\\][^/\\]+[/\\]coverage-draft\.json$/i.test(filePath)) {
      return { allow: true };
    }
  }

  return deny('（Write：只能寫入 .daily-agenda/runs/ 下的草稿檔案）');
};

// ---------------------------------------------------------------------------
// Marker path helpers
// ---------------------------------------------------------------------------

export const markerPath = (daHome, sanitizedSessionId) => join(daHome, 'taint', sanitizedSessionId);
