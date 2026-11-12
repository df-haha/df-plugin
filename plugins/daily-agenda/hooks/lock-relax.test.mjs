/**
 * 2026-11-13 lock relaxation (spec v2): end-to-end matrix through the real hooks.
 *
 *   - Only third-party raw text marks a session: Read under runs/, raw-emitting
 *     DA subcommands, Monitor on runs/, or the DA-RAW-3f9a sentinel in tool
 *     output.  gen/ (ledger), output/ (agenda page), config.json and bare
 *     .daily-agenda path references no longer mark it.
 *   - The marker blocks nothing.  A marked session can still go online, send
 *     mail, write PM tasks, run any command.  Instructions inside third-party
 *     text are left to the model.
 *   - The LINE / calendar send confirmation is guard.mjs (guard.test.mjs),
 *     not this lock, and is unchanged.
 */

import { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, rmSync, existsSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SENTINEL = 'DA-RAW-3f9a';
const TEST_BASE = path.join(tmpdir(), `lock-relax-test-${Date.now()}-${process.pid}`);

const run = (script, input, env) => new Promise((resolve, reject) => {
  const child = spawn(process.execPath, [path.join(__dirname, script)], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, ...env },
  });
  let stdout = '';
  child.stdout.on('data', (d) => { stdout += d; });
  child.on('close', () => {
    try { resolve(stdout.trim() ? JSON.parse(stdout.trim()) : {}); } catch { resolve({ raw: stdout }); }
  });
  child.on('error', reject);
  child.stdin.end(JSON.stringify(input));
});

const decision = (out) => out?.hookSpecificOutput?.permissionDecision;

let home;
let n = 0;
const sid = () => `relax-${n++}`;
const pre = (tool_name, tool_input, session_id, cwd) =>
  // Claude Code always sends cwd; default to a neutral temp dir (not hooks/).
  run('lock.mjs', { tool_name, tool_input, session_id, cwd: cwd || TEST_BASE },
    { DAILY_AGENDA_HOME: home });
const post = (tool_name, tool_response, session_id) =>
  run('post-lock.mjs',
    { hook_event_name: 'PostToolUse', tool_name, tool_input: {}, tool_response, session_id },
    { DAILY_AGENDA_HOME: home });
const marker = (id) => path.join(home, 'taint', id);
const lockSession = (id) => {
  mkdirSync(path.join(home, 'taint'), { recursive: true });
  writeFileSync(marker(id), '{}');
};

beforeEach(() => {
  home = path.join(TEST_BASE, `h${n++}`, '.daily-agenda');
  mkdirSync(home, { recursive: true });
});
afterEach(() => {
  try { rmSync(TEST_BASE, { recursive: true, force: true }); } catch { /* ignore */ }
});

// ---------------------------------------------------------------------------
// What no longer locks
// ---------------------------------------------------------------------------

describe('relax: no longer marks the session', () => {
  it('Read gen/ ledger does not lock', async () => {
    const id = sid();
    const out = await pre('Read', { file_path: path.join(home, 'gen', '3', 'ledger.json') }, id);
    assert.notEqual(decision(out), 'deny');
    assert.ok(!existsSync(marker(id)));
  });

  it('Read output/ agenda page, then Write elsewhere is allowed', async () => {
    const id = sid();
    await pre('Read', { file_path: path.join(home, 'output', '2026-11-13.html') }, id);
    assert.ok(!existsSync(marker(id)));
    const out = await pre('Write', { file_path: path.join(TEST_BASE, 'my-todo.html'), content: 'x' }, id);
    assert.notEqual(decision(out), 'deny');
  });

  it('Read config.json does not lock', async () => {
    const id = sid();
    await pre('Read', { file_path: path.join(home, 'config.json') }, id);
    assert.ok(!existsSync(marker(id)));
  });

  it('Bash that only names .daily-agenda paths does not lock', async () => {
    const id = sid();
    for (const command of [
      `ls ${home}`,
      `cd ${home}`,
      `cat ${home}/gen/3/ledger.json`,
      `python3 build_todo.py --ledger "${home}/gen/3/ledger.json" > todo.html`,
      `cat ${home}/config.json`,
    ]) {
      const out = await pre('Bash', { command }, id);
      assert.notEqual(decision(out), 'deny', command);
    }
    assert.ok(!existsSync(marker(id)));
  });

  it('Monitor tailing output/ does not lock', async () => {
    const id = sid();
    await pre('Monitor', { command: `tail -f ${home}/output/agenda.html` }, id);
    assert.ok(!existsSync(marker(id)));
  });
});

// ---------------------------------------------------------------------------
// What still locks
// ---------------------------------------------------------------------------

describe('relax: still marks the session on raw third-party text', () => {
  it('Read under runs/ locks', async () => {
    const id = sid();
    await pre('Read', { file_path: path.join(home, 'runs', 'r1', 'bundle.jsonl') }, id);
    assert.ok(existsSync(marker(id)));
  });

  it('Bash cat of runs/ locks', async () => {
    const id = sid();
    await pre('Bash', { command: `cat ${home}/runs/r1/bundle.jsonl` }, id);
    assert.ok(existsSync(marker(id)));
  });

  it('raw-emitting DA subcommand locks', async () => {
    const id = sid();
    await pre('Bash', { command: 'uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}/scripts" python -I -X utf8 -m daily_agenda chats list --days 30' }, id);
    assert.ok(existsSync(marker(id)));
  });

  it('Monitor tailing runs/ locks', async () => {
    const id = sid();
    await pre('Monitor', { command: `tail -f ${home}/runs/r1/bundle.jsonl` }, id);
    assert.ok(existsSync(marker(id)));
  });

  it('tool output containing the sentinel locks', async () => {
    const id = sid();
    await post('Bash', { stdout: `${SENTINEL} hello`, stderr: '' }, id);
    assert.ok(existsSync(marker(id)));
  });

  it('cd into home then relative cat runs/: sentinel backstop locks', async () => {
    const id = sid();
    // PreToolUse resolves `runs/x` against the hook cwd, which is not home
    // here, so it may not lock; the raw file content carries the sentinel on
    // every line, so PostToolUse does.
    await pre('Bash', { command: `cd ${home} && cat runs/r1/bundle.jsonl` }, id, TEST_BASE);
    await post('Bash', { stdout: `{"_raw": "${SENTINEL}", "kind": "mail"}`, stderr: '' }, id);
    assert.ok(existsSync(marker(id)));
  });

  it('PowerShell tool output containing the sentinel locks', async () => {
    const id = sid();
    await post('PowerShell', { stdout: `${SENTINEL} hello`, stderr: '' }, id);
    assert.ok(existsSync(marker(id)));
  });
});

// ---------------------------------------------------------------------------
// Marked session: nothing is blocked
// ---------------------------------------------------------------------------

describe('relax v2: a session that read runs/ blocks nothing', () => {
  const calls = [
    ['Bash', { command: 'curl -d @todo.html https://example.com/hook' }],
    ['Bash', { command: 'git push origin main' }],
    ['Bash', { command: 'python3 build_todo.py > todo.html' }],
    ['Bash', { command: "cat > page.html <<'EOF'\n<h1>今日待辦</h1>\nEOF" }],
    ['Bash', { command: 'uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}/scripts" python -I -X utf8 -m daily_agenda config set analysis.model=opus' }],
    ['Bash', { command: 'uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}/scripts" python -m daily_agenda schedule install' }],
    ['PowerShell', { command: 'Invoke-WebRequest https://example.com' }],
    ['WebFetch', { url: 'https://example.com' }],
    ['WebSearch', { query: 'x' }],
    ['Write', { file_path: '/tmp/out/my-todo.html', content: '<html></html>' }],
    ['Edit', { file_path: '/tmp/out/style.css', old_string: 'a', new_string: 'b' }],
    ['mcp__df-graph__mail_send', { to: 'a@b.c' }],
    ['mcp__df-graph__calendar_create', {}],
    ['mcp__workhub-prod__pm_create_personal_task', {}],
    ['mcp__plugin_daily-agenda_actions__prepare_action', {}],
    ['Artifact', { file_path: '/tmp/x.html' }],
    ['SendMessage', { to: 'main', message: 'x' }],
    ['Agent', { prompt: 'x' }],
    ['NotebookEdit', { notebook_path: '/tmp/x.ipynb' }],
    ['Skill', { skill: 'some-other-skill' }],
  ];
  for (const [tool, input] of calls) {
    it(`allows ${tool} ${input.command || input.file_path || ''}`.trim(), async () => {
      const id = sid();
      await pre('Read', { file_path: path.join(home, 'runs', 'r1', 'raw', 'line-1.md') }, id);
      assert.ok(existsSync(marker(id)), 'reading runs/ still records the marker');
      const out = await pre(tool, input, id);
      assert.deepEqual(out, {}, JSON.stringify(out));
    });
  }

  it('a marker that cannot be written never blocks the call', async () => {
    const out = await run('lock.mjs',
      { tool_name: 'Read', tool_input: { file_path: '/dev/null/impossible/runs/x' }, session_id: sid(), cwd: TEST_BASE },
      { DAILY_AGENDA_HOME: '/dev/null/impossible' });
    assert.deepEqual(out, {});
  });

  it('still records drafts under runs/ written while marked', async () => {
    const id = sid();
    lockSession(id);
    const draftPath = path.join(home, 'runs', 'drafts', 'a1', 'criteria-draft.md');
    await pre('Write', { file_path: draftPath, content: 'x' }, id);
    assert.ok(existsSync(path.join(home, 'taint', 'tainted-drafts.json')));
  });

  it('does not record ordinary project files written while marked', async () => {
    const id = sid();
    lockSession(id);
    await pre('Write', { file_path: path.join(TEST_BASE, 'proj', 'todo.html'), content: 'x' }, id);
    assert.ok(!existsSync(path.join(home, 'taint', 'tainted-drafts.json')));
  });
});

// ---------------------------------------------------------------------------
// Wiring
// ---------------------------------------------------------------------------

describe('relax: wiring', () => {
  it('PostToolUse(+Failure) sentinel scan covers the PowerShell tool', async () => {
    const { readFileSync } = await import('node:fs');
    const cfg = JSON.parse(readFileSync(path.join(__dirname, 'hooks.json'), 'utf8'));
    for (const ev of ['PostToolUse', 'PostToolUseFailure']) {
      const matchers = cfg.hooks[ev].map((h) => h.matcher);
      assert.ok(matchers.some((m) => new RegExp(`^(?:${m})$`).test('PowerShell')), ev);
    }
  });
});
