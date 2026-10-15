/**
 * Pure logic for the session taint-lock system.
 * No I/O, no process.exit — testable as a module.
 *
 * Fail-open when untainted (internal errors must not block normal sessions).
 * Fail-closed when tainted (internal errors deny the call for safety).
 */

import { readFileSync, realpathSync } from 'node:fs';
import { join, dirname, posix } from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = dirname(fileURLToPath(import.meta.url));

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
 * N5-safe registry check: only match `python -m daily_agenda <subcommand>`
 * where <subcommand> is a registry token. Tolerates optional global flags
 * like --progress-file between daily_agenda and the subcommand.
 *
 * Does NOT false-taint developer commands like `grep -rn chats scripts/`.
 */
const _checkRegistryInvocation = (command) => {
  const registry = loadRegistry();

  // Find the "python -m daily_agenda" anchor
  const anchor = command.match(
    /python[23]?\s+-m\s+daily_agenda\b(.*)/
  );
  if (!anchor) return { taint: false };

  // Everything after "daily_agenda" — split into tokens, skip flags + their values
  const rest = anchor[1].trim();
  if (!rest) return { taint: false };

  const tokens = rest.split(/\s+/);
  let skipNext = false;
  for (const token of tokens) {
    const clean = token.replace(/^["']|["']$/g, '');
    if (skipNext) { skipNext = false; continue; }
    // Skip flags and mark their value token to skip too
    if (clean.startsWith('--')) {
      // --flag=value: skip this token only
      if (clean.includes('=')) continue;
      // --flag value: skip this token and the next
      skipNext = true;
      continue;
    }
    if (clean.startsWith('-')) continue;
    // First non-flag token is the subcommand
    if (registry.has(clean)) {
      return { taint: true, reason: `raw-emitting subcommand: ${clean}` };
    }
    // First non-flag, non-registry token: not a raw command
    return { taint: false };
  }
  return { taint: false };
};

/**
 * Check if a Bash command should taint the session.
 *
 * Two mechanisms:
 * 1. Registry: `python -m daily_agenda <registry-subcommand>` (N5-safe)
 * 2. Path references: command tokens that resolve inside DA taint dirs
 *
 * @param {string} command
 * @param {{ daHome?: string, cwd?: string, extraPrefixes?: string[] }} [ctx]
 */
export const checkBashTaint = (command, ctx) => {
  if (typeof command !== 'string') return { taint: false };

  // 1. Registry check (only for real DA invocations, not grep/git commands)
  if (command.includes('daily_agenda')) {
    const regResult = _checkRegistryInvocation(command);
    if (regResult.taint) return regResult;
  }

  // 2. Path-reference check: tokenize, expand, canonicalize, check
  if (ctx?.daHome) {
    const normHome = canonicalize(ctx.daHome);
    if (!normHome) return { taint: false };

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
      const clean = token.replace(/["']/g, '');
      if (!clean || clean.startsWith('-')) continue;

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

      // Check if the token is inside a taint dir
      for (const td of taintDirs) {
        if (normToken === td || normToken.startsWith(td + '/')) {
          return { taint: true, reason: `command references taint-protected dir: ${clean}` };
        }
      }

      // Check if token is/inside daHome itself (covers `cd ~/.daily-agenda`)
      if (normToken === normHome || normToken.startsWith(normHome + '/')) {
        // Only taint if it's a taint dir or daHome itself, not just any subpath
        if (normToken === normHome) {
          return { taint: true, reason: `command references DA home: ${clean}` };
        }
        // Check if it's under a taint dir (glob like <home>/*/x)
        // Extract the first path component after home
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
 * Check if a Read/Grep/Glob path targets DA taint-protected directories.
 *
 * @param {string} filePath
 * @param {string} daHome
 * @param {string[]} [extraPrefixes]
 * @param {string} [cwd]
 */
export const checkReadTaint = (filePath, daHome, extraPrefixes, cwd) => {
  if (typeof filePath !== 'string' || typeof daHome !== 'string') return { taint: false };
  if (filePath.length === 0) return { taint: false };

  const normPath = canonicalize(filePath, cwd);
  const normHome = canonicalize(daHome);
  if (!normPath || !normHome) return { taint: false };

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

  const daMatch = command.match(
    /^uv\s+run\s+--quiet\s+--project\s+"([^"]+)"\s+python\s+-m\s+daily_agenda\s+([a-z][-a-z0-9]*)((?:\s+.*)?)$/
  );
  if (!daMatch) return deny();

  const projectPath = daMatch[1];
  const subcommand = daMatch[2];
  const argsStr = (daMatch[3] || '').trim();

  const normProject = projectPath.replace(/\\/g, '/');
  const isPluginRoot = normProject === '__PLUGIN_ROOT__/scripts' ||
    normProject === '${CLAUDE_PLUGIN_ROOT}/scripts';
  const isAbsoluteDA = /^([A-Za-z]:[/\\]|\/)[^]*[/\\]plugins[/\\]daily-agenda[/\\]scripts$/i.test(projectPath);
  if (!isPluginRoot && !isAbsoluteDA) return deny('（Bash：只允許 daily-agenda 指令）');

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
  if (command.includes('\n') || command.includes('\r')) return deny('（Monitor：不允許多行指令）');

  const sanitized = command.replace(/\$\{CLAUDE_PLUGIN_ROOT\}/g, '__PLUGIN_ROOT__');
  if (/[;|&`><()\n\r{}*?~]/.test(sanitized)) return deny('（Monitor：不允許 shell 特殊字元）');
  if (/\$/.test(sanitized)) return deny('（Monitor：不允許變數展開，$CLAUDE_PLUGIN_ROOT 除外）');

  const daMatch = command.match(
    /^uv\s+run\s+--quiet\s+--project\s+"([^"]+)"\s+python\s+-m\s+daily_agenda\s+progress\s+watch\b/
  );
  if (!daMatch) return deny('（Monitor：只能追蹤 daily-agenda 進度）');

  const projectPath = daMatch[1];
  const normProject = projectPath.replace(/\\/g, '/');
  const isPluginRoot = normProject === '__PLUGIN_ROOT__/scripts' ||
    normProject === '${CLAUDE_PLUGIN_ROOT}/scripts';
  const isAbsoluteDA = /^([A-Za-z]:[/\\]|\/)[^]*[/\\]plugins[/\\]daily-agenda[/\\]scripts$/i.test(projectPath);
  if (!isPluginRoot && !isAbsoluteDA) return deny('（Monitor：只允許 daily-agenda 進度指令）');

  // Validate --file in progress watch
  const argsAfterWatch = command.slice(command.indexOf('watch') + 5).trim();
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
