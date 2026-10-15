import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import {
  canonicalize, checkBashTaint, checkReadTaint, checkTaintedAllow,
  checkTaintedBash, checkTaintedWrite, checkMonitorCommand,
  sanitizeSessionId, hasShellMetachars, markerPath, loadRegistry,
} from './lock-core.mjs';

const DA_HOME = '/home/test/.daily-agenda';
const CTX = { daHome: DA_HOME };
const da = (sub, args = '') =>
  `uv run --quiet --project "\${CLAUDE_PLUGIN_ROOT}/scripts" python -m daily_agenda ${sub}${args ? ' ' + args : ''}`;
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
  it('no taint on config.json', () => assert.ok(!checkReadTaint(DA_HOME + '/config.json', DA_HOME).taint));
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
