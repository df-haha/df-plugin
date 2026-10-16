import { describe, it, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { mkdirSync, rmSync } from 'node:fs';
import { resolve, dirname, join } from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import {
  canonicalize, checkBashTaint, checkReadTaint, checkTaintedAllow,
  checkTaintedBash, checkTaintedWrite, checkMonitorCommand,
  checkMonitorPreTaint, sanitizeSessionId, hasShellMetachars,
  markerPath, loadRegistry, loadDraftPolicy,
  shellTokenize, _isAllowedProjectPath, _isProgressFilePath,
  _isDraftFilePath, _parseCanonicalDA,
} from './lock-core.mjs';

const __test_dirname = dirname(fileURLToPath(import.meta.url));
const REAL_SCRIPTS = resolve(__test_dirname, '..', 'scripts');

const DA_HOME = '/home/test/.daily-agenda';
const CTX = { daHome: DA_HOME };
const da = (sub, args = '') =>
  `uv run --quiet --project "\${CLAUDE_PLUGIN_ROOT}/scripts" python -I -X utf8 -m daily_agenda ${sub}${args ? ' ' + args : ''}`;
const pf = `"${DA_HOME}/runs/progress/x.jsonl"`;
const draft = `${DA_HOME}/runs/20261015-0900-abc1/criteria-draft.md`;
const profileDraft = `${DA_HOME}/runs/20261015-0900-abc1/profile-draft.md`;
const coverageDraft = `${DA_HOME}/runs/20261015-0900-abc1/coverage-draft.json`;

// ============================================================================
// canonicalize
// ============================================================================

describe('canonicalize', () => {
  it('normalizes backslashes', () => assert.equal(canonicalize('C:\\Users\\test'), 'c:/users/test'));
  it('lowercases', () => assert.equal(canonicalize('/Home/Test'), '/home/test'));
  it('collapses ..', () => assert.ok(canonicalize('/a/b/../c').endsWith('/a/c')));
  it('strips trailing slash', () => assert.ok(!canonicalize('/a/b/').endsWith('/')));
  it('empty to empty', () => assert.equal(canonicalize(''), ''));
  it('resolves relative', () => assert.ok(canonicalize('runs/x', '/home/test/.daily-agenda').includes('/home/test/.daily-agenda/runs/x')));
});

// ============================================================================
// Registry
// ============================================================================

describe('loadRegistry', () => {
  it('loads chats + outlook-preview + raw + scan', () => {
    const r = loadRegistry();
    assert.ok(r.has('chats') && r.has('outlook-preview') && r.has('raw') && r.has('scan'));
  });
});

// ============================================================================
// checkReadTaint
// ============================================================================

describe('checkReadTaint', () => {
  it('empty path no taint', () => assert.ok(!checkReadTaint('', DA_HOME).taint));
  it('taints under runs/', () => assert.ok(checkReadTaint(DA_HOME + '/runs/x/bundle.jsonl', DA_HOME).taint));
  it('taints under gen/', () => assert.ok(checkReadTaint(DA_HOME + '/gen/1/agenda.html', DA_HOME).taint));
  it('taints under output/', () => assert.ok(checkReadTaint(DA_HOME + '/output/2026.html', DA_HOME).taint));
  it('taints under output.dir', () => assert.ok(checkReadTaint('/home/test/docs/da/x.html', DA_HOME, ['/home/test/docs/da']).taint));
  it('taints DA home itself', () => assert.ok(checkReadTaint(DA_HOME, DA_HOME).taint));
  it('no taint on parent', () => assert.ok(!checkReadTaint('/home/test', DA_HOME).taint));
  it('taints on config.json (raw chat names)', () => assert.ok(checkReadTaint(DA_HOME + '/config.json', DA_HOME).taint));
  it('no taint on .', () => assert.ok(!checkReadTaint('.', DA_HOME, [], '/home/test/project').taint));
  it('no taint on runs/../..', () => assert.ok(!checkReadTaint(DA_HOME + '/runs/../../.ssh/id_rsa', DA_HOME).taint));
  it('case-insensitive', () => assert.ok(checkReadTaint('C:\\USERS\\TEST\\.DAILY-AGENDA\\RUNS\\x', 'C:\\Users\\test\\.daily-agenda').taint));
  it('null safe', () => assert.ok(!checkReadTaint(null, DA_HOME).taint));
});

// ============================================================================
// checkBashTaint — N5 false taint fix
// ============================================================================

describe('checkBashTaint', () => {
  // Should taint (real DA invocations)
  it('taints on chats list', () => assert.ok(checkBashTaint('uv run --quiet --project "/p/s" python -m daily_agenda chats list --days 30').taint));
  it('taints on outlook-preview', () => assert.ok(checkBashTaint('uv run --quiet --project "/p/s" python -m daily_agenda outlook-preview').taint));
  it('taints with --progress-file before subcmd', () => assert.ok(checkBashTaint('uv run --quiet --project "/p/s" python -m daily_agenda --progress-file /tmp/x chats list').taint));
  it('taints on scan raw', () => assert.ok(checkBashTaint('uv run --quiet --project "/p/s" python -m daily_agenda scan raw --days 14').taint));
  it('taints on raw show', () => assert.ok(checkBashTaint('uv run --quiet --project "/p/s" python -m daily_agenda raw show --todo T-1').taint));

  // N5: developer commands must NOT taint
  it('N5: grep -rn chats scripts/daily_agenda', () => {
    assert.ok(!checkBashTaint('grep -rn chats scripts/daily_agenda', CTX).taint);
  });
  it('N5: git log -p | grep chats', () => {
    assert.ok(!checkBashTaint('git log -p -- scripts/daily_agenda | grep chats', CTX).taint);
  });
  it('N5: grep -rn outlook-preview plugins/', () => {
    assert.ok(!checkBashTaint('grep -rn "outlook-preview" plugins/', CTX).taint);
  });
  it('N5: grep -rn ".daily-agenda/runs" plugins/', () => {
    assert.ok(!checkBashTaint('grep -rn ".daily-agenda/runs" plugins/', CTX).taint);
  });
  it('N5: echo .daily-agenda/gen', () => {
    assert.ok(!checkBashTaint('echo .daily-agenda/gen', CTX).taint);
  });

  // Round-2 F4 misses: shell-level path tricks must still taint
  it('F4: cd into home then relative cat', () => {
    assert.ok(checkBashTaint(`cd ${DA_HOME} && cat runs/x/bundle.json`, CTX).taint);
  });
  it('F4: quote inside path token', () => {
    assert.ok(checkBashTaint(`cat ${DA_HOME}/"runs"/x/bundle.json`, CTX).taint);
  });
  it('F4: operator glued to path token', () => {
    assert.ok(checkBashTaint(`cat ${DA_HOME}/runs/x/bundle.json|head`, CTX).taint);
  });
  it('F4: ./ segment inside path', () => {
    assert.ok(checkBashTaint(`cat ${DA_HOME}/./runs/x`, CTX).taint);
  });

  // Should NOT taint
  it('no taint: run', () => assert.ok(!checkBashTaint('uv run --quiet --project "/p/s" python -m daily_agenda run').taint));
  it('no taint: ls', () => assert.ok(!checkBashTaint('ls -la', CTX).taint));

  // F4: absolute path reference taint
  it('taints on cat of runs/ (absolute)', () => {
    assert.ok(checkBashTaint(`cat ${DA_HOME}/runs/x/bundle.jsonl`, CTX).taint);
  });

  // Item 4: bash unescape in mechanism-2 path tokens
  it('R5-4: backslash-escaped path component ru\\ns → runs → taint', () => {
    assert.ok(checkBashTaint(`cat ${DA_HOME}/ru\\ns/id/records.jsonl`, CTX).taint,
      'ru\\ns must unescape to runs');
  });
  it("R5-4: ANSI-C $'runs' → runs → taint", () => {
    assert.ok(checkBashTaint(`cat ${DA_HOME}/$'runs'/id/records.jsonl`, CTX).taint,
      "$'runs' must unescape to runs");
  });
  it('R5-4: backslash-escaped on Windows-style path', () => {
    const winCtx = { daHome: 'C:\\Users\\haha\\.daily-agenda' };
    assert.ok(checkBashTaint('cat C:/Users/haha/.daily-agenda/ru\\ns/id/records.jsonl', winCtx).taint);
  });
  it("R5-4: $'runs' on Linux-style path", () => {
    assert.ok(checkBashTaint(`cat /home/test/.daily-agenda/$'runs'/id/records.jsonl`, CTX).taint);
  });

  // Item 6: config.json taint via Bash path reference
  it('R5-6: cat config.json → taint', () => {
    assert.ok(checkBashTaint(`cat ${DA_HOME}/config.json`, CTX).taint,
      'config.json contains raw chat names');
  });
});

// ============================================================================
// sanitizeSessionId
// ============================================================================

describe('sanitizeSessionId', () => {
  it('accepts normal', () => assert.equal(sanitizeSessionId('abc-123'), 'abc-123'));
  it('rejects traversal', () => assert.equal(sanitizeSessionId('../../x'), null));
  it('rejects null', () => assert.equal(sanitizeSessionId(null), null));
});

// ============================================================================
// checkTaintedAllow
// ============================================================================

describe('checkTaintedAllow', () => {
  it('allows Read', () => assert.ok(checkTaintedAllow('Read', {}, CTX).allow));
  it('allows ToolSearch', () => assert.ok(checkTaintedAllow('ToolSearch', {}, CTX).allow));
  it('allows actions MCP', () => assert.ok(checkTaintedAllow('mcp__plugin_daily-agenda_actions__prepare_action', {}, CTX).allow));
  it('allows DA skills', () => assert.ok(checkTaintedAllow('Skill', { skill: 'agenda' }, CTX).allow));
  it('denies non-DA skills', () => assert.ok(!checkTaintedAllow('Skill', { skill: 'x' }, CTX).allow));
  it('denies WebFetch', () => assert.ok(!checkTaintedAllow('WebFetch', {}, CTX).allow));
  it('denies df-graph', () => assert.ok(!checkTaintedAllow('mcp__df-graph__mail_send', {}, CTX).allow));
  it('allows Write criteria-draft', () => assert.ok(checkTaintedAllow('Write', { file_path: draft }, CTX).allow));
  it('allows Write profile-draft', () => assert.ok(checkTaintedAllow('Write', { file_path: profileDraft }, CTX).allow));
  it('allows Write coverage-draft', () => assert.ok(checkTaintedAllow('Write', { file_path: coverageDraft }, CTX).allow));
  it('denies Write arbitrary', () => assert.ok(!checkTaintedAllow('Write', { file_path: '/tmp/x' }, CTX).allow));
});

// ============================================================================
// checkTaintedBash — full matrix
// ============================================================================

describe('checkTaintedBash', () => {
  // Allowed
  it('allows run', () => assert.ok(checkTaintedBash({ command: da('run') }, CTX).allow));
  it('allows show', () => assert.ok(checkTaintedBash({ command: da('show', '--todo T-1') }, CTX).allow));
  it('allows mark-done', () => assert.ok(checkTaintedBash({ command: da('mark-done', '--todo T-1') }, CTX).allow));
  it('allows chats', () => assert.ok(checkTaintedBash({ command: da('chats', 'list --days 30') }, CTX).allow));
  it('allows criteria show', () => assert.ok(checkTaintedBash({ command: da('criteria', 'show') }, CTX).allow));
  it('allows criteria draft', () => assert.ok(checkTaintedBash({ command: da('criteria', 'draft --days 14') }, CTX).allow));
  it('allows criteria restore', () => assert.ok(checkTaintedBash({ command: da('criteria', 'restore --version 2') }, CTX).allow));
  it('allows refine status', () => assert.ok(checkTaintedBash({ command: da('refine', 'status') }, CTX).allow));
  it('allows refine draft', () => assert.ok(checkTaintedBash({ command: da('refine', 'draft') }, CTX).allow));
  it('allows refine skip', () => assert.ok(checkTaintedBash({ command: da('refine', 'skip') }, CTX).allow));
  it('allows criteria save under runs/', () => assert.ok(checkTaintedBash({ command: da('criteria', `save --file "${draft}"`) }, CTX).allow));
  it('allows refine apply under runs/', () => assert.ok(checkTaintedBash({ command: da('refine', `apply --file "${draft}"`) }, CTX).allow));

  // Profile commands
  it('allows profile show', () => assert.ok(checkTaintedBash({ command: da('profile', 'show') }, CTX).allow));
  it('allows profile list', () => assert.ok(checkTaintedBash({ command: da('profile', 'list') }, CTX).allow));
  it('allows profile next-question', () => assert.ok(checkTaintedBash({ command: da('profile', 'next-question') }, CTX).allow));
  it('allows profile restore', () => assert.ok(checkTaintedBash({ command: da('profile', 'restore --version 1') }, CTX).allow));
  it('allows profile cadence', () => assert.ok(checkTaintedBash({ command: da('profile', 'cadence --set now') }, CTX).allow));
  it('allows profile save under runs/', () => assert.ok(checkTaintedBash({ command: da('profile', `save --file "${profileDraft}"`) }, CTX).allow));
  it('allows profile coverage save under runs/', () => assert.ok(checkTaintedBash({ command: da('profile', `coverage save --file "${coverageDraft}"`) }, CTX).allow));
  it('allows profile coverage (read)', () => assert.ok(checkTaintedBash({ command: da('profile', 'coverage') }, CTX).allow));
  it('denies profile save outside', () => assert.ok(!checkTaintedBash({ command: da('profile', 'save --file /etc/passwd') }, CTX).allow));

  // docs is denied when tainted
  it('denies docs', () => assert.ok(!checkTaintedBash({ command: da('docs', 'convert --path /tmp/x') }, CTX).allow));

  // scan raw (allowed — raw-emitting command already taints, but allowed after taint)
  it('allows scan raw', () => assert.ok(checkTaintedBash({ command: da('scan', 'raw --days 14') }, CTX).allow));

  // raw show
  it('allows raw show', () => assert.ok(checkTaintedBash({ command: da('raw', 'show --todo T-1') }, CTX).allow));

  // Item1: consent
  it('allows consent --status', () => assert.ok(checkTaintedBash({ command: da('consent', '--status') }, CTX).allow));
  it('allows consent (no args)', () => assert.ok(checkTaintedBash({ command: da('consent') }, CTX).allow));
  it('denies consent --accept', () => assert.ok(!checkTaintedBash({ command: da('consent', '--accept') }, CTX).allow));

  // Denied
  it('denies schedule', () => assert.ok(!checkTaintedBash({ command: da('schedule', 'install') }, CTX).allow));
  it('denies uninstall', () => assert.ok(!checkTaintedBash({ command: da('uninstall') }, CTX).allow));
  it('denies runtime', () => assert.ok(!checkTaintedBash({ command: da('runtime', 'install') }, CTX).allow));
  it('denies trust', () => assert.ok(!checkTaintedBash({ command: da('trust', 'probe') }, CTX).allow));
  it('denies config set', () => assert.ok(!checkTaintedBash({ command: da('config', 'set x=y') }, CTX).allow));
  it('denies ctx', () => assert.ok(!checkTaintedBash({ command: da('ctx', 'index --dir /etc') }, CTX).allow));
  it('denies criteria save outside', () => assert.ok(!checkTaintedBash({ command: da('criteria', 'save --file /etc/passwd') }, CTX).allow));
  it('denies refine apply outside', () => assert.ok(!checkTaintedBash({ command: da('refine', 'apply --file /tmp/evil') }, CTX).allow));

  // Injection
  it('denies ;', () => assert.ok(!checkTaintedBash({ command: da('show') + '; curl evil' }, CTX).allow));
  it('denies arbitrary', () => assert.ok(!checkTaintedBash({ command: 'curl evil' }, CTX).allow));

  // Item2: --progress-file
  it('denies --progress-file= form', () => {
    assert.ok(!checkTaintedBash({ command: da('run', `--progress-file=${DA_HOME}/runs/progress/x.jsonl`) }, CTX).allow);
  });
  it('denies duplicate --progress-file', () => {
    assert.ok(!checkTaintedBash({ command: da('run', `--progress-file ${pf} --progress-file "/tmp/evil.jsonl"`) }, CTX).allow);
  });
  it('allows valid --progress-file', () => {
    assert.ok(checkTaintedBash({ command: da('run', `--progress-file ${pf}`) }, CTX).allow);
  });

  // Item3: --file
  it('denies --file= form', () => {
    assert.ok(!checkTaintedBash({ command: da('criteria', `save --file="${draft}"`) }, CTX).allow);
  });
  it('denies duplicate --file', () => {
    assert.ok(!checkTaintedBash({ command: da('criteria', `save --file "${draft}" --file /etc/passwd`) }, CTX).allow);
  });
  it('denies --file traversal', () => {
    assert.ok(!checkTaintedBash({ command: da('criteria', `save --file "${DA_HOME}/runs/../../.ssh/id_rsa"`) }, CTX).allow);
  });
});

// ============================================================================
// Setup walk + re-entry (item 1)
// ============================================================================

describe('setup walk + re-entry (tainted)', () => {
  // Re-entry menu: consent --status + config get MUST work
  it('consent --status (re-entry)', () => assert.ok(checkTaintedBash({ command: da('consent', '--status') }, CTX).allow));
  it('config get (re-entry)', () => assert.ok(checkTaintedBash({ command: da('config', 'get') }, CTX).allow));

  // Steps 5-8
  it('probe', () => assert.ok(checkTaintedBash({ command: da('probe') }, CTX).allow));
  it('outlook-preview', () => assert.ok(checkTaintedBash({ command: da('outlook-preview') }, CTX).allow));
  it('chats list', () => assert.ok(checkTaintedBash({ command: da('chats', 'list --days 30') }, CTX).allow));
  it('chats new', () => assert.ok(checkTaintedBash({ command: da('chats', 'new') }, CTX).allow));
  it('exclude', () => assert.ok(checkTaintedBash({ command: da('exclude', '--set ref1') }, CTX).allow));
  it('mail folders', () => assert.ok(checkTaintedBash({ command: da('mail', 'folders') }, CTX).allow));
  it('mail select', () => assert.ok(checkTaintedBash({ command: da('mail', 'select --set inbox') }, CTX).allow));
  it('progress new', () => assert.ok(checkTaintedBash({ command: da('progress', 'new') }, CTX).allow));
  it('criteria draft', () => assert.ok(checkTaintedBash({ command: da('criteria', `draft --days 14 --progress-file ${pf}`) }, CTX).allow));
  it('criteria draft --from-run', () => assert.ok(checkTaintedBash({ command: da('criteria', 'draft --from-run 20261015-0900-abc1') }, CTX).allow));
  it('Read draft', () => assert.ok(checkTaintedAllow('Read', { file_path: draft }, CTX).allow));
  it('Write draft', () => assert.ok(checkTaintedAllow('Write', { file_path: draft }, CTX).allow));
  it('criteria save', () => assert.ok(checkTaintedBash({ command: da('criteria', `save --file "${draft}"`) }, CTX).allow));
  it('Monitor watch', () => assert.ok(checkTaintedAllow('Monitor', { command: da('progress', `watch --file ${pf}`) }, CTX).allow));
  it('show', () => assert.ok(checkTaintedBash({ command: da('show', '--todo T-1') }, CTX).allow));
  it('mark-done', () => assert.ok(checkTaintedBash({ command: da('mark-done', '--todo T-1') }, CTX).allow));
  it('refine status', () => assert.ok(checkTaintedBash({ command: da('refine', 'status') }, CTX).allow));
  it('refine draft', () => assert.ok(checkTaintedBash({ command: da('refine', `draft --progress-file ${pf}`) }, CTX).allow));
  it('refine apply', () => assert.ok(checkTaintedBash({ command: da('refine', `apply --file "${draft}"`) }, CTX).allow));
  it('criteria restore', () => assert.ok(checkTaintedBash({ command: da('criteria', 'restore --version 1') }, CTX).allow));
  it('envcheck', () => assert.ok(checkTaintedBash({ command: da('envcheck') }, CTX).allow));

  // Profile setup steps
  it('profile show', () => assert.ok(checkTaintedBash({ command: da('profile', 'show') }, CTX).allow));
  it('profile list', () => assert.ok(checkTaintedBash({ command: da('profile', 'list') }, CTX).allow));
  it('profile save under runs/', () => assert.ok(checkTaintedBash({ command: da('profile', `save --file "${profileDraft}"`) }, CTX).allow));
  it('profile coverage save under runs/', () => assert.ok(checkTaintedBash({ command: da('profile', `coverage save --file "${coverageDraft}"`) }, CTX).allow));
  it('profile cadence', () => assert.ok(checkTaintedBash({ command: da('profile', 'cadence --set agenda') }, CTX).allow));
  it('profile next-question', () => assert.ok(checkTaintedBash({ command: da('profile', 'next-question') }, CTX).allow));
  it('profile restore', () => assert.ok(checkTaintedBash({ command: da('profile', 'restore --version 1') }, CTX).allow));
  it('Write profile-draft', () => assert.ok(checkTaintedAllow('Write', { file_path: profileDraft }, CTX).allow));
  it('Write coverage-draft', () => assert.ok(checkTaintedAllow('Write', { file_path: coverageDraft }, CTX).allow));
  it('scan raw', () => assert.ok(checkTaintedBash({ command: da('scan', 'raw --days 14') }, CTX).allow));
  it('raw show', () => assert.ok(checkTaintedBash({ command: da('raw', 'show --todo T-1') }, CTX).allow));
});

// ============================================================================
// docs-cache does NOT taint (profile doc reading)
// ============================================================================

describe('docs-cache no taint', () => {
  it('reading docs-cache does not taint', () => {
    assert.ok(!checkReadTaint(DA_HOME + '/docs-cache/abc123/file.txt', DA_HOME).taint);
  });
  it('reading profile/ does not taint', () => {
    assert.ok(!checkReadTaint(DA_HOME + '/profile/v1.md', DA_HOME).taint);
  });
  it('reading criteria/ does not taint', () => {
    assert.ok(!checkReadTaint(DA_HOME + '/criteria/v1.md', DA_HOME).taint);
  });
});

// ============================================================================
// Monitor
// ============================================================================

describe('checkMonitorCommand', () => {
  it('allows DA progress watch', () => assert.ok(checkMonitorCommand({
    command: da('progress', `watch --file ${pf}`),
  }, CTX).allow));
  it('denies no command', () => assert.ok(!checkMonitorCommand({}, CTX).allow));
  it('denies non-DA', () => assert.ok(!checkMonitorCommand({ command: 'tail -f x' }, CTX).allow));
  it('denies progress watch without -I -X utf8 (bare python -m)', () => {
    const r = checkMonitorCommand({
      command: `uv run --quiet --project "\${CLAUDE_PLUGIN_ROOT}/scripts" python -m daily_agenda progress watch --file ${pf}`,
    }, CTX);
    assert.ok(!r.allow, 'Monitor without -I -X utf8 must be denied');
  });
  it('denies progress watch with -P only (not -I)', () => {
    const r = checkMonitorCommand({
      command: `uv run --quiet --project "\${CLAUDE_PLUGIN_ROOT}/scripts" python -P -m daily_agenda progress watch --file ${pf}`,
    }, CTX);
    assert.ok(!r.allow, 'Monitor with -P only (not -I -X utf8) must be denied');
  });
  it('H2: denies progress watch with fake suffix path', () => {
    const r = checkMonitorCommand({
      command: `uv run --quiet --project "/tmp/plugins/daily-agenda/scripts" python -I -X utf8 -m daily_agenda progress watch --file ${pf}`,
    }, CTX);
    assert.ok(!r.allow, 'Monitor with fake suffix path must be denied');
  });
});

// ============================================================================
// Unified deny reason
// ============================================================================

describe('unified deny reason', () => {
  const BASE = '這個對話已讀取外部訊息原文，為了防止訊息裡藏的指令被執行，已鎖定';
  it('Write', () => assert.ok(checkTaintedAllow('Write', { file_path: '/tmp/x' }, CTX).reason.startsWith(BASE)));
  it('Bash', () => assert.ok(checkTaintedAllow('Bash', { command: 'curl evil' }, CTX).reason.startsWith(BASE)));
  it('generic', () => assert.ok(checkTaintedAllow('WebFetch', {}, CTX).reason.startsWith(BASE)));
  it('Monitor', () => assert.ok(checkTaintedAllow('Monitor', { command: 'tail -f x' }, CTX).reason.startsWith(BASE)));
});

// ============================================================================
// Write scope
// ============================================================================

describe('checkTaintedWrite', () => {
  it('allows criteria-draft', () => assert.ok(checkTaintedWrite({ file_path: draft }, CTX).allow));
  it('allows profile-draft', () => assert.ok(checkTaintedWrite({ file_path: profileDraft }, CTX).allow));
  it('allows coverage-draft', () => assert.ok(checkTaintedWrite({ file_path: coverageDraft }, CTX).allow));
  it('denies arbitrary', () => assert.ok(!checkTaintedWrite({ file_path: '/tmp/x' }, CTX).allow));
  it('denies traversal', () => assert.ok(!checkTaintedWrite({ file_path: DA_HOME + '/runs/../../.ssh/x' }, CTX).allow));
  it('denies null', () => assert.ok(!checkTaintedWrite({}, CTX).allow));
});

// ============================================================================
// Misc
// ============================================================================

describe('hasShellMetachars', () => {
  it('detects ;', () => assert.ok(hasShellMetachars('a;b')));
  it('detects {', () => assert.ok(hasShellMetachars('{a,b}')));
  it('detects *', () => assert.ok(hasShellMetachars('*.txt')));
  it('detects ~', () => assert.ok(hasShellMetachars('~/f')));
  it('clean', () => assert.ok(!hasShellMetachars('uv run python -m daily_agenda show')));
});

describe('markerPath', () => {
  it('builds path', () => assert.ok(markerPath(DA_HOME, 's-1').includes('taint')));
});

// ============================================================================
// Item 1: canonical DA invocations must NOT taint (path-reference exemption)
// ============================================================================

const WIN_HOME = 'C:\\Users\\haha.huang\\.daily-agenda';
const WIN_CTX = { daHome: WIN_HOME };

describe('item1: canonical DA with --progress-file must NOT taint (Linux)', () => {
  it('run --progress-file under runs/progress/', () => {
    const cmd = da('run', `--progress-file "${DA_HOME}/runs/progress/20261016-0900-ab12.jsonl"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint, 'canonical run with valid progress-file must not taint');
  });
  it('refine draft --progress-file under runs/progress/', () => {
    const cmd = da('refine', `draft --progress-file "${DA_HOME}/runs/progress/20261016-0900-ab12.jsonl"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint, 'canonical refine draft with valid progress-file must not taint');
  });
  it('criteria draft --days 14 --progress-file under runs/progress/', () => {
    const cmd = da('criteria', `draft --days 14 --progress-file "${DA_HOME}/runs/progress/20261016-0900-ab12.jsonl"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint);
  });
  it('criteria save --file under runs/', () => {
    const cmd = da('criteria', `save --file "${DA_HOME}/runs/20261015-0900-abc1/criteria-draft.md"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint, 'criteria save with draft file must not taint');
  });
  it('draft show --file under runs/', () => {
    const cmd = da('draft', `show --file "${DA_HOME}/runs/drafts/x/criteria-draft.md"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint, 'draft show with draft file must not taint');
  });
  it('profile save --file under runs/', () => {
    const cmd = da('profile', `save --file "${DA_HOME}/runs/20261015-0900-abc1/profile-draft.md"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint);
  });
  it('profile coverage save --file under runs/', () => {
    const cmd = da('profile', `coverage save --file "${DA_HOME}/runs/20261015-0900-abc1/coverage-draft.json"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint);
  });
});

describe('item1: Monitor progress watch must NOT taint (Linux)', () => {
  it('progress watch --file under runs/progress/', () => {
    const cmd = `uv run --quiet --project "\${CLAUDE_PLUGIN_ROOT}/scripts" python -I -X utf8 -m daily_agenda progress watch --file "${DA_HOME}/runs/progress/20261016-0900-ab12.jsonl"`;
    assert.ok(!checkBashTaint(cmd, CTX).taint, 'Monitor progress watch with valid file must not taint');
  });
});

describe('item1: canonical DA with --progress-file must NOT taint (Windows)', () => {
  it('run --progress-file under runs/progress/', () => {
    const cmd = da('run', `--progress-file "${WIN_HOME}\\runs\\progress\\20261016-0900-ab12.jsonl"`);
    assert.ok(!checkBashTaint(cmd, WIN_CTX).taint, 'Windows: canonical run with valid progress-file must not taint');
  });
  it('refine draft --progress-file under runs/progress/', () => {
    const cmd = da('refine', `draft --progress-file "${WIN_HOME}\\runs\\progress\\x.jsonl"`);
    assert.ok(!checkBashTaint(cmd, WIN_CTX).taint);
  });
  it('progress watch --file under runs/progress/ (Monitor)', () => {
    const cmd = `uv run --quiet --project "\${CLAUDE_PLUGIN_ROOT}/scripts" python -I -X utf8 -m daily_agenda progress watch --file "${WIN_HOME}\\runs\\progress\\x.jsonl"`;
    assert.ok(!checkBashTaint(cmd, WIN_CTX).taint, 'Windows: Monitor progress watch must not taint');
  });
  it('criteria save --file draft under runs/', () => {
    const cmd = da('criteria', `save --file "${WIN_HOME}\\runs\\20261015-0900-abc1\\criteria-draft.md"`);
    assert.ok(!checkBashTaint(cmd, WIN_CTX).taint);
  });
});

describe('item1: negative cases — must still taint', () => {
  it('cat of runs/ still taints', () => {
    assert.ok(checkBashTaint(`cat ${DA_HOME}/runs/x/bundle.jsonl`, CTX).taint);
  });
  it('canonical run with progress-file NOT under progress/', () => {
    const cmd = da('run', `--progress-file "${DA_HOME}/runs/x/bundle.jsonl"`);
    assert.ok(checkBashTaint(cmd, CTX).taint, 'progress-file not under runs/progress/ must taint');
  });
  it('--file pointing to raw/ subdir', () => {
    const cmd = da('criteria', `save --file "${DA_HOME}/runs/20261015/raw/criteria-draft.md"`);
    assert.ok(checkBashTaint(cmd, CTX).taint, 'draft file under raw/ must taint');
  });
  it('--file not a draft basename', () => {
    const cmd = da('criteria', `save --file "${DA_HOME}/runs/20261015/bundle.jsonl"`);
    assert.ok(checkBashTaint(cmd, CTX).taint, 'non-draft file must taint');
  });
  it('duplicate --progress-file taints', () => {
    const cmd = da('run', `--progress-file "${DA_HOME}/runs/progress/a.jsonl" --progress-file "${DA_HOME}/runs/progress/b.jsonl"`);
    assert.ok(checkBashTaint(cmd, CTX).taint, 'duplicate progress-file must taint');
  });
  it('non-canonical project path with valid progress-file still taints', () => {
    const cmd = `uv run --quiet --project "/tmp/evil/scripts" python -I -X utf8 -m daily_agenda run --progress-file "${DA_HOME}/runs/progress/x.jsonl"`;
    assert.ok(checkBashTaint(cmd, CTX).taint, 'non-canonical project with valid pf must taint');
  });
  it('non-isolated form (bare -m) with valid progress-file still taints', () => {
    const cmd = `uv run --quiet --project "\${CLAUDE_PLUGIN_ROOT}/scripts" python -m daily_agenda run --progress-file "${DA_HOME}/runs/progress/x.jsonl"`;
    assert.ok(checkBashTaint(cmd, CTX).taint, 'bare python -m with valid pf must taint');
  });
  it('-P only form (not -I) with valid progress-file still taints', () => {
    const cmd = `uv run --quiet --project "\${CLAUDE_PLUGIN_ROOT}/scripts" python -P -m daily_agenda run --progress-file "${DA_HOME}/runs/progress/x.jsonl"`;
    assert.ok(checkBashTaint(cmd, CTX).taint, '-P only form with valid pf must taint');
  });
  it('registry subcmd taints even with valid progress-file', () => {
    const cmd = da('chats', `list --days 30 --progress-file "${DA_HOME}/runs/progress/x.jsonl"`);
    assert.ok(checkBashTaint(cmd, CTX).taint, 'raw subcmd always taints');
  });
});

// ============================================================================
// Item 2 (R3): subcommand-bound exemption — cross-subcommand mismatches taint
// ============================================================================

describe('item2-subcmd: subcommand-bound file exemption', () => {
  // progress show --file: progress path ONLY
  it('progress show --file progress path → no taint', () => {
    const cmd = da('progress', `show --file "${DA_HOME}/runs/progress/x.jsonl"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint);
  });
  it('progress show --file draft path → taint', () => {
    const cmd = da('progress', `show --file "${DA_HOME}/runs/20261015/criteria-draft.md"`);
    assert.ok(checkBashTaint(cmd, CTX).taint, 'progress show + draft path must taint');
  });

  // criteria save --file: criteria-draft.md ONLY
  it('criteria save --file criteria-draft.md → no taint', () => {
    const cmd = da('criteria', `save --file "${DA_HOME}/runs/20261015-0900-abc1/criteria-draft.md"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint);
  });
  it('criteria save --file profile-draft.md → taint', () => {
    const cmd = da('criteria', `save --file "${DA_HOME}/runs/20261015-0900-abc1/profile-draft.md"`);
    assert.ok(checkBashTaint(cmd, CTX).taint, 'criteria save + profile-draft must taint');
  });

  // refine apply --file: criteria-draft.md ONLY
  it('refine apply --file criteria-draft.md → no taint', () => {
    const cmd = da('refine', `apply --file "${DA_HOME}/runs/20261015-0900-abc1/criteria-draft.md"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint);
  });
  it('refine apply --file coverage-draft.json → taint', () => {
    const cmd = da('refine', `apply --file "${DA_HOME}/runs/20261015-0900-abc1/coverage-draft.json"`);
    assert.ok(checkBashTaint(cmd, CTX).taint, 'refine apply + coverage-draft must taint');
  });

  // profile save --file: profile-draft.md ONLY
  it('profile save --file profile-draft.md → no taint', () => {
    const cmd = da('profile', `save --file "${DA_HOME}/runs/20261015-0900-abc1/profile-draft.md"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint);
  });
  it('profile save --file criteria-draft.md → taint', () => {
    const cmd = da('profile', `save --file "${DA_HOME}/runs/20261015-0900-abc1/criteria-draft.md"`);
    assert.ok(checkBashTaint(cmd, CTX).taint, 'profile save + criteria-draft must taint');
  });

  // profile coverage save --file: coverage-draft.json ONLY
  it('profile coverage save --file coverage-draft.json → no taint', () => {
    const cmd = da('profile', `coverage save --file "${DA_HOME}/runs/20261015-0900-abc1/coverage-draft.json"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint);
  });
  it('profile coverage save --file profile-draft.md → taint', () => {
    const cmd = da('profile', `coverage save --file "${DA_HOME}/runs/20261015-0900-abc1/profile-draft.md"`);
    assert.ok(checkBashTaint(cmd, CTX).taint, 'profile coverage save + profile-draft must taint');
  });

  // draft show --file: any of the three basenames
  it('draft show --file criteria-draft.md → no taint', () => {
    const cmd = da('draft', `show --file "${DA_HOME}/runs/drafts/x/criteria-draft.md"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint);
  });
  it('draft show --file profile-draft.md → no taint', () => {
    const cmd = da('draft', `show --file "${DA_HOME}/runs/drafts/x/profile-draft.md"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint);
  });
  it('draft show --file coverage-draft.json → no taint', () => {
    const cmd = da('draft', `show --file "${DA_HOME}/runs/20261015/coverage-draft.json"`);
    assert.ok(!checkBashTaint(cmd, CTX).taint);
  });
  it('draft show --file bundle.jsonl → taint', () => {
    const cmd = da('draft', `show --file "${DA_HOME}/runs/20261015/bundle.jsonl"`);
    assert.ok(checkBashTaint(cmd, CTX).taint, 'draft show + non-draft file must taint');
  });

  // Unknown subcommand with --file: no exemption
  it('unknown-cmd --file draft path → taint', () => {
    const cmd = da('show', `--file "${DA_HOME}/runs/20261015/criteria-draft.md"`);
    assert.ok(checkBashTaint(cmd, CTX).taint, 'unknown subcmd + --file gets no exemption');
  });
});

// ============================================================================
// Draft laundering: tainted-drafts list prevents exempt bypass
// ============================================================================

describe('draft laundering prevention', () => {
  const draftPath = canonicalize(`${DA_HOME}/runs/20261015-0900-abc1/criteria-draft.md`);

  it('draft show of tainted draft → taint', () => {
    const cmd = da('draft', `show --file "${DA_HOME}/runs/20261015-0900-abc1/criteria-draft.md"`);
    const ctx = { ...CTX, taintedDrafts: new Set([draftPath]) };
    assert.ok(checkBashTaint(cmd, ctx).taint, 'draft show of tainted draft must taint');
  });

  it('draft show of clean draft → no taint', () => {
    const cmd = da('draft', `show --file "${DA_HOME}/runs/20261015-0900-abc1/criteria-draft.md"`);
    const ctx = { ...CTX, taintedDrafts: new Set() };
    assert.ok(!checkBashTaint(cmd, ctx).taint, 'clean draft must not taint');
  });

  it('Read of tainted draft path → taint', () => {
    const taintedDrafts = new Set([draftPath]);
    const r = checkReadTaint(
      `${DA_HOME}/runs/20261015-0900-abc1/criteria-draft.md`,
      DA_HOME, [], undefined, taintedDrafts,
    );
    assert.ok(r.taint, 'Read of tainted draft must taint');
  });

  it('Read of clean draft path → normal path-taint (under runs/)', () => {
    const r = checkReadTaint(
      `${DA_HOME}/runs/20261015-0900-abc1/criteria-draft.md`,
      DA_HOME, [], undefined, new Set(),
    );
    // Still taints via path-based check (under runs/)
    assert.ok(r.taint, 'draft under runs/ taints via path check');
  });
});

// ============================================================================
// Item 2 (R2): anchor bypass forms must taint
// ============================================================================

describe('item2: interpreter variant bypasses must taint', () => {
  it('python.exe -P -m daily_agenda outlook-preview', () => {
    assert.ok(checkBashTaint('python.exe -P -m daily_agenda outlook-preview').taint,
      'python.exe must be recognized');
  });
  it('python3.13 -m daily_agenda outlook-preview', () => {
    assert.ok(checkBashTaint('python3.13 -m daily_agenda outlook-preview').taint,
      'python3.13 must be recognized');
  });
  it('python -I -X utf8 -m daily_agenda.__main__ outlook-preview', () => {
    assert.ok(checkBashTaint('python -I -X utf8 -m daily_agenda.__main__ outlook-preview').taint,
      'daily_agenda.__main__ is non-canonical module');
  });
  it('outlook\\-preview (unquoted backslash escape)', () => {
    assert.ok(checkBashTaint('uv run --quiet --project "/p/s" python -I -X utf8 -m daily_agenda outlook\\-preview').taint,
      'bash unquoted backslash must be stripped');
  });
  it('$SUB variable expansion after -m daily_agenda', () => {
    assert.ok(checkBashTaint('SUB=outlook-preview; uv run --quiet --project "/p/s" python -I -X utf8 -m daily_agenda $SUB').taint,
      'unexpanded $VAR must taint');
  });
  it('python -c with runpy daily_agenda', () => {
    assert.ok(checkBashTaint('python -c "import runpy; runpy.run_module(\'daily_agenda\')"').taint,
      '-c form with daily_agenda must taint');
  });
  it('python daily_agenda/__main__.py outlook-preview', () => {
    assert.ok(checkBashTaint('python daily_agenda/__main__.py outlook-preview').taint,
      'script-path form must taint');
  });
  it('python -P daily_agenda/__main__.py outlook-preview', () => {
    assert.ok(checkBashTaint('python -P daily_agenda/__main__.py outlook-preview').taint);
  });
  it('/usr/bin/python3 -m daily_agenda scan raw', () => {
    assert.ok(checkBashTaint('/usr/bin/python3 -m daily_agenda scan raw').taint,
      'absolute path to python must be recognized');
  });
});

describe('item2: dev commands must NOT taint', () => {
  it('grep -rn daily_agenda .', () => {
    assert.ok(!checkBashTaint('grep -rn daily_agenda .', CTX).taint);
  });
  it('cat plugins/daily-agenda/scripts/daily_agenda/cli/_common.py', () => {
    assert.ok(!checkBashTaint('cat plugins/daily-agenda/scripts/daily_agenda/cli/_common.py', CTX).taint);
  });
  it('git diff', () => {
    assert.ok(!checkBashTaint('git diff', CTX).taint);
  });
  it('uv run pytest -q', () => {
    assert.ok(!checkBashTaint('uv run pytest -q', CTX).taint);
  });
  it('uv run pytest tests/test_root_args.py', () => {
    assert.ok(!checkBashTaint('uv run pytest tests/test_root_args.py', CTX).taint);
  });
  it('python -m pytest tests/', () => {
    assert.ok(!checkBashTaint('python -m pytest tests/', CTX).taint);
  });
  it('python -m pytest -k daily_agenda', () => {
    assert.ok(!checkBashTaint('python -m pytest -k daily_agenda', CTX).taint);
  });
  it('cd scripts && python -m pytest -k daily_agenda', () => {
    assert.ok(!checkBashTaint('cd scripts && python -m pytest -k daily_agenda', CTX).taint);
  });
});

// ============================================================================
// H1a: unknown root flag must taint (fail closed)
// ============================================================================

describe('H1a: unknown root flag in _checkRegistryInvocation', () => {
  it('--bogus outlook-preview taints', () => {
    const r = checkBashTaint('uv run --quiet --project "/p/s" python -m daily_agenda --bogus outlook-preview');
    assert.ok(r.taint, 'Unknown flag --bogus must cause taint');
    assert.ok(r.reason.includes('unknown root flag'));
  });
  it('--bogus=val outlook-preview taints', () => {
    const r = checkBashTaint('uv run --quiet --project "/p/s" python -m daily_agenda --bogus=val outlook-preview');
    assert.ok(r.taint, 'Unknown flag --bogus=val must cause taint');
  });
  it('known --progress-file <val> does not taint when subcmd is safe', () => {
    const r = checkBashTaint('uv run --quiet --project "/p/s" python -m daily_agenda --progress-file /tmp/x run');
    assert.ok(!r.taint, '--progress-file is known, run is not a raw subcmd');
  });
  it('known --progress-file <val> taints when subcmd is raw', () => {
    const r = checkBashTaint('uv run --quiet --project "/p/s" python -m daily_agenda --progress-file /tmp/x chats');
    assert.ok(r.taint, '--progress-file is known, chats IS a raw subcmd');
  });
  it('-I -X utf8 flags between python and -m still detects taint', () => {
    const r = checkBashTaint('uv run --quiet --project "/p/s" python -I -X utf8 -m daily_agenda chats list');
    assert.ok(r.taint, '-I -X utf8 form must still detect raw subcommands');
  });
  it('-I -X utf8 flags between python and -m does not taint safe subcmd', () => {
    const r = checkBashTaint('uv run --quiet --project "/p/s" python -I -X utf8 -m daily_agenda run');
    assert.ok(!r.taint, '-I -X utf8 form with safe subcmd must not taint');
  });
});

// ============================================================================
// shellTokenize
// ============================================================================

describe('shellTokenize', () => {
  it('splits simple tokens', () => {
    assert.deepEqual(shellTokenize('a b c'), ['a', 'b', 'c']);
  });
  it('handles double-quoted strings with spaces', () => {
    assert.deepEqual(shellTokenize('--flag "hello world"'), ['--flag', 'hello world']);
  });
  it('handles single-quoted strings', () => {
    assert.deepEqual(shellTokenize("--flag 'hello world'"), ['--flag', 'hello world']);
  });
  it('preserves backslashes inside double quotes (Windows paths)', () => {
    const tokens = shellTokenize('--progress-file "C:\\Users\\a b\\OneDrive - 大豐環保\\runs\\progress\\x.jsonl"');
    assert.equal(tokens.length, 2);
    assert.equal(tokens[0], '--progress-file');
    assert.ok(tokens[1].includes('C:\\Users\\a b\\OneDrive - 大豐環保'));
  });
  it('handles CJK in quoted path', () => {
    const tokens = shellTokenize('"C:\\Users\\大豐\\file.txt"');
    assert.equal(tokens.length, 1);
    assert.ok(tokens[0].includes('大豐'));
  });
  it('strips unquoted backslash escapes (bash behavior)', () => {
    assert.deepEqual(shellTokenize('a\\-b'), ['a-b']);
  });
  it('double-quoted \\\\ → single backslash', () => {
    assert.deepEqual(shellTokenize('"C:\\\\x"'), ['C:\\x']);
  });
  it('double-quoted \\" → literal quote', () => {
    assert.deepEqual(shellTokenize('"say \\"hi\\""'), ['say "hi"']);
  });
  it('double-quoted other \\x → literal \\x', () => {
    // "C:\Users" → C:\Users (\ before U is not a special escape in bash)
    const tokens = shellTokenize('"C:\\Users"');
    assert.equal(tokens[0], 'C:\\Users');
  });
});

// ============================================================================
// H1a: quoted Windows path with spaces + CJK
// ============================================================================

describe('H1a: taint with quoted Windows path', () => {
  it('--progress-file with spaces+CJK in value does not hide subcommand', () => {
    const cmd = 'uv run --quiet --project "/p/s" python -m daily_agenda --progress-file "C:\\Users\\a b\\OneDrive - 大豐環保\\runs\\progress\\x.jsonl" chats list';
    const r = checkBashTaint(cmd);
    assert.ok(r.taint, 'chats must be detected even after quoted path with spaces');
    assert.ok(r.reason.includes('chats'));
  });
  it('--progress-file with spaces+CJK in value, safe subcmd does not taint', () => {
    const cmd = 'uv run --quiet --project "/p/s" python -m daily_agenda --progress-file "C:\\Users\\a b\\OneDrive - 大豐環保\\runs\\progress\\x.jsonl" run';
    const r = checkBashTaint(cmd);
    assert.ok(!r.taint);
  });
});

// ============================================================================
// H2: project path allowlist — realpath equality
// ============================================================================

describe('H2: _isAllowedProjectPath', () => {
  const fakeBase = join(tmpdir(), `lc-test-${process.pid}`);
  afterEach(() => {
    try { rmSync(fakeBase, { recursive: true, force: true }); } catch { /* ignore */ }
  });

  it('accepts $CLAUDE_PLUGIN_ROOT/scripts template', () => {
    assert.ok(_isAllowedProjectPath('${CLAUDE_PLUGIN_ROOT}/scripts'));
  });
  it('accepts __PLUGIN_ROOT__/scripts template', () => {
    assert.ok(_isAllowedProjectPath('__PLUGIN_ROOT__/scripts'));
  });
  it('accepts the real absolute scripts dir', () => {
    assert.ok(_isAllowedProjectPath(REAL_SCRIPTS),
      'The real plugin scripts dir must be accepted via realpath equality');
  });
  it('accepts real abs path via checkTaintedBash', () => {
    const r = checkTaintedBash({
      command: `uv run --quiet --project "${REAL_SCRIPTS}" python -I -X utf8 -m daily_agenda run`,
    }, CTX);
    assert.ok(r.allow, 'Real scripts dir should be allowed in tainted Bash');
  });
  it('rejects nonexistent absolute path', () => {
    assert.ok(!_isAllowedProjectPath('/nonexistent/path/to/scripts'),
      'Nonexistent path must be denied (fail closed)');
  });
  it('rejects an EXISTING fake suffix dir', () => {
    const fake = join(fakeBase, 'plugins', 'daily-agenda', 'scripts');
    mkdirSync(fake, { recursive: true });
    assert.ok(!_isAllowedProjectPath(fake),
      'Existing dir with matching suffix must be rejected — only realpath equality');
  });
});

// ============================================================================
// H2: tainted Bash rejects non-canonical forms and fake paths
// ============================================================================

describe('H2: tainted Bash isolation requirement', () => {
  it('denies bare python -m (no flags) in locked session', () => {
    const r = checkTaintedBash({
      command: 'uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}/scripts" python -m daily_agenda run',
    }, CTX);
    assert.ok(!r.allow, 'bare python -m must be denied in tainted state');
  });
  it('denies python -P -m (no -I) in locked session', () => {
    const r = checkTaintedBash({
      command: 'uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}/scripts" python -P -m daily_agenda run',
    }, CTX);
    assert.ok(!r.allow, 'python -P without -I -X utf8 must be denied');
  });
  it('allows python -I -X utf8 -m in locked session', () => {
    const r = checkTaintedBash({ command: da('run') }, CTX);
    assert.ok(r.allow, 'python -I -X utf8 -m must be allowed');
  });
  it('denies fake suffix path in locked session', () => {
    const r = checkTaintedBash({
      command: 'uv run --quiet --project "/tmp/plugins/daily-agenda/scripts" python -I -X utf8 -m daily_agenda run',
    }, CTX);
    assert.ok(!r.allow, 'Fake suffix path must be denied');
  });
});

// ============================================================================
// R6-2: Monitor pre-taint (checkMonitorPreTaint)
// ============================================================================

describe('R6-2: checkMonitorPreTaint', () => {
  it("daily''_agenda chats list in Monitor → taint", () => {
    const r = checkMonitorPreTaint("daily''_agenda chats list", CTX);
    assert.ok(r.taint, "Monitor with obfuscated daily_agenda must taint");
  });
  it('canonical progress watch → no taint', () => {
    const cmd = da('progress', `watch --file "${DA_HOME}/runs/progress/x.jsonl"`);
    const r = checkMonitorPreTaint(cmd, CTX);
    assert.ok(!r.taint, 'Canonical progress watch must pass');
  });
  it('unrelated Monitor (tail -f) → no taint', () => {
    const r = checkMonitorPreTaint('tail -f /var/log/syslog | grep ERROR', CTX);
    assert.ok(!r.taint, 'Unrelated Monitor must not taint');
  });
  it('non-canonical DA Monitor → taint', () => {
    const r = checkMonitorPreTaint('python -I -X utf8 -m daily_agenda chats list', CTX);
    assert.ok(r.taint, 'Non-canonical DA invocation in Monitor must taint');
  });
  it('mentions .daily-agenda but not canonical → taint', () => {
    const r = checkMonitorPreTaint('tail -f /home/test/.daily-agenda/runs/x', CTX);
    assert.ok(r.taint, 'Monitor mentioning data home must taint unless canonical');
  });
});

// ============================================================================
// R6-4: locked CLI save with non-draft file → deny
// ============================================================================

describe('R6-4: locked save enforces draft policy', () => {
  it('criteria save --file records.jsonl → deny', () => {
    const r = checkTaintedBash({
      command: da('criteria', `save --file "${DA_HOME}/runs/20261015/records.jsonl"`),
    }, CTX);
    assert.ok(!r.allow, 'criteria save with non-draft file must be denied');
  });
  it('criteria save --file criteria-draft.md → allow', () => {
    const r = checkTaintedBash({
      command: da('criteria', `save --file "${DA_HOME}/runs/20261015-0900-abc1/criteria-draft.md"`),
    }, CTX);
    assert.ok(r.allow);
  });
  it('criteria save --file profile-draft.md → deny (wrong basename for criteria)', () => {
    const r = checkTaintedBash({
      command: da('criteria', `save --file "${DA_HOME}/runs/20261015-0900-abc1/profile-draft.md"`),
    }, CTX);
    assert.ok(!r.allow, 'criteria save with profile-draft must be denied');
  });
  it('refine apply --file records.jsonl → deny', () => {
    const r = checkTaintedBash({
      command: da('refine', `apply --file "${DA_HOME}/runs/20261015/records.jsonl"`),
    }, CTX);
    assert.ok(!r.allow);
  });
  it('profile save --file profile-draft.md → allow', () => {
    const r = checkTaintedBash({
      command: da('profile', `save --file "${DA_HOME}/runs/20261015-0900-abc1/profile-draft.md"`),
    }, CTX);
    assert.ok(r.allow);
  });
  it('profile save --file records.jsonl → deny', () => {
    const r = checkTaintedBash({
      command: da('profile', `save --file "${DA_HOME}/runs/20261015/records.jsonl"`),
    }, CTX);
    assert.ok(!r.allow);
  });
  it('criteria save --file raw/criteria-draft.md → deny (raw segment)', () => {
    const r = checkTaintedBash({
      command: da('criteria', `save --file "${DA_HOME}/runs/20261015/raw/criteria-draft.md"`),
    }, CTX);
    assert.ok(!r.allow, 'File under raw/ must be denied');
  });
});

// ============================================================================
// R6-4: loadDraftPolicy consistency
// ============================================================================

describe('R6-4: loadDraftPolicy', () => {
  it('loads draft_policy from raw-commands.json', () => {
    const policy = loadDraftPolicy();
    assert.ok(policy['criteria save']);
    assert.ok(policy['criteria save'].has('criteria-draft.md'));
    assert.ok(!policy['criteria save'].has('profile-draft.md'));
    assert.ok(policy['draft show'].has('criteria-draft.md'));
    assert.ok(policy['draft show'].has('profile-draft.md'));
    assert.ok(policy['draft show'].has('coverage-draft.json'));
  });
});
