/**
 * 2026-11-13 lock relaxation, third version: a marked session (one that read
 * LINE / mail raw text) blocks exactly three kinds of action; everything else
 * stays allowed.
 *
 *   (a) calendar write tools:  mcp__<server>__calendar_(create|update|delete|forward|rsvp)
 *   (b) shell commands that launch or call the bundled LINE component
 *       (any mention of line-desktop-mcp: vendor/, runtime/, Windows paths, npx bin)
 *   (c) daily-agenda setting subcommands: config set, schedule install|remove,
 *       consent --accept, trust
 *
 * An unmarked session blocks none of them.
 */

import { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, rmSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';
import { checkMarkedSessionDeny } from './lock-core.mjs';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const TEST_BASE = path.join(tmpdir(), `lock-marked-deny-${Date.now()}-${process.pid}`);
const DA = 'uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}/scripts" python -I -X utf8 -m daily_agenda';

const run = (input, env) => new Promise((resolve, reject) => {
  const child = spawn(process.execPath, [path.join(__dirname, 'lock.mjs')], {
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

let home;
let n = 0;
const sid = () => `marked-${n++}`;
const pre = (tool_name, tool_input, session_id) =>
  run({ tool_name, tool_input, session_id, cwd: TEST_BASE }, { DAILY_AGENDA_HOME: home });
const markSession = (id) => {
  mkdirSync(path.join(home, 'taint'), { recursive: true });
  writeFileSync(path.join(home, 'taint', id), '{}');
};
const decision = (out) => out?.hookSpecificOutput?.permissionDecision;
const reason = (out) => out?.hookSpecificOutput?.permissionDecisionReason || '';

beforeEach(() => {
  home = path.join(TEST_BASE, `h${n++}`, '.daily-agenda');
  mkdirSync(home, { recursive: true });
});
afterEach(() => {
  try { rmSync(TEST_BASE, { recursive: true, force: true }); } catch { /* ignore */ }
});

// ---------------------------------------------------------------------------
// The three blocked categories
// ---------------------------------------------------------------------------

const BLOCKED = {
  calendar: [
    ['mcp__df-graph__calendar_create', { subject: 'x' }],
    ['mcp__df-graph__calendar_update', { id: '1' }],
    ['mcp__df-graph__calendar_delete', { id: '1' }],
    ['mcp__df-graph__calendar_forward', { id: '1' }],
    ['mcp__df-graph__calendar_rsvp', { id: '1' }],
    ['mcp__plugin_x_y__calendar_update', { id: '1' }],
    ['mcp__claude_ai_Outlook__calendar_create', {}],
  ],
  line_component: [
    ['Bash', { command: 'node "C:/Users/u/.claude/plugins/daily-agenda/vendor/line-desktop-mcp/src/server.js"' }],
    ['Bash', { command: 'node ~/.daily-agenda/runtime/line-desktop-mcp/src/server.js' }],
    ['Bash', { command: 'cd ~/.daily-agenda/runtime/line-desktop-mcp && npm start' }],
    ['Bash', { command: 'npx line-desktop-mcp' }],
    ['PowerShell', { command: 'node "C:\\Users\\u\\.daily-agenda\\runtime\\line-desktop-mcp\\src\\server.js"' }],
    ['PowerShell', { command: '& node C:\\plugins\\daily-agenda\\vendor\\line-desktop-mcp\\src\\server.js' }],
    ['Monitor', { command: 'node runtime/line-desktop-mcp/src/server.js' }],
    ['Bash', { command: 'node -e "import(\'./vendor/LINE-Desktop-MCP/src/automation/line-automation.js\')"' }],
  ],
  settings: [
    ['Bash', { command: `${DA} config set analysis.model=opus` }],
    ['Bash', { command: `${DA} config --progress-file "x.jsonl" set a=b` }],
    ['Bash', { command: `${DA} --progress-file=x.jsonl config set a=b` }],
    ['Bash', { command: 'python -m daily_agenda schedule install --time 08:30' }],
    ['Bash', { command: `${DA} schedule remove` }],
    ['Bash', { command: `${DA} consent --accept` }],
    ['Bash', { command: `${DA} consent --status --accept` }],
    ['Bash', { command: `${DA} trust probe` }],
    ['Bash', { command: 'cd scripts && uv run python -m daily_agenda trust probe' }],
    ['Bash', { command: `${DA} config get; ${DA} config set tz=UTC` }],
    ['PowerShell', { command: 'uv run --project "C:\\p\\scripts" python -I -X utf8 -m daily_agenda config set tz=UTC' }],
    ['Monitor', { command: `${DA} schedule install --time 09:00` }],
    ['Bash', { command: 'py -mdaily_agenda config set tz=UTC' }],
    ['Bash', { command: 'python -Imdaily_agenda trust probe' }],
    ['Bash', { command: 'python -m daily_agenda.__main__ trust probe' }],
    ['Bash', { command: 'python scripts/daily_agenda/__main__.py config set tz=UTC' }],
    ['Bash', { command: `${DA} \\\n  config set tz=UTC` }],
    ['PowerShell', { command: 'uv run --project "C:\\p\\scripts" python -m daily_agenda `\r\n  trust probe' }],
    ['Bash', { command: `${DA} config $SUB tz=UTC` }],
    ['Bash', { command: `${DA} $(echo config) set tz=UTC` }],
    ['Bash', { command: `${DA} consent \`echo --accept\`` }],
  ],
};

const READ_ONLY_TWINS = [
  ['mcp__df-graph__calendar_list', {}],
  ['mcp__df-graph__calendar_get', { id: '1' }],
  ['mcp__df-graph__calendar_find_times', {}],
  ['mcp__df-graph__calendar_get_schedule', {}],
  ['Bash', { command: `${DA} config get` }],
  ['Bash', { command: `${DA} schedule status` }],
  ['Bash', { command: `${DA} consent --status` }],
  ['Bash', { command: `${DA} consent` }],
  ['Bash', { command: `${DA} run --progress-file "x.jsonl"` }],
  ['Bash', { command: `${DA} runtime status` }],
  ['Bash', { command: `${DA} exclude --add r1 r2` }],
  ['Bash', { command: `${DA} \\\n  config get` }],
  ['Monitor', { command: `${DA} progress watch --file "x.jsonl"` }],
  ['Bash', { command: 'node scripts/line_collect.mjs --vendor some/dir' }],
  ['Bash', { command: 'grep -rn calendar_create docs/' }],
  ['Bash', { command: 'echo configure settings' }],
  ['mcp__df-graph__mail_send', { to: 'a@b.c' }],
  ['mcp__line-desktop-mcp__get_line_local_messages', {}],
];

describe('marked session: the three categories are denied', () => {
  for (const [category, calls] of Object.entries(BLOCKED)) {
    for (const [tool, input] of calls) {
      it(`${category}: denies ${tool} ${input.command || ''}`.trim(), async () => {
        const id = sid();
        markSession(id);
        const out = await pre(tool, input, id);
        assert.equal(decision(out), 'deny', JSON.stringify(out));
        assert.match(reason(out), /讀過 LINE 或信件的原文/);
        assert.match(reason(out), /新的對話/);
      });
    }
  }
});

describe('unmarked session: the same calls are allowed', () => {
  for (const [category, calls] of Object.entries(BLOCKED)) {
    for (const [tool, input] of calls) {
      it(`${category}: allows ${tool} ${input.command || ''}`.trim(), async () => {
        const out = await pre(tool, input, sid());
        assert.deepEqual(out, {}, JSON.stringify(out));
      });
    }
  }
});

describe('marked session: read-only twins and normal agenda steps stay allowed', () => {
  for (const [tool, input] of READ_ONLY_TWINS) {
    it(`allows ${tool} ${input.command || ''}`.trim(), async () => {
      const id = sid();
      markSession(id);
      const out = await pre(tool, input, id);
      assert.deepEqual(out, {}, JSON.stringify(out));
    });
  }
});

describe('deny message wording', () => {
  it('uses plain Chinese without internal terms', () => {
    for (const [tool, input] of [
      ['mcp__df-graph__calendar_create', {}],
      ['Bash', { command: 'node vendor/line-desktop-mcp/src/server.js' }],
      ['Bash', { command: `${DA} config set a=b` }],
    ]) {
      const r = checkMarkedSessionDeny(tool, input);
      assert.equal(r.deny, true, tool);
      assert.doesNotMatch(r.reason, /taint|marker|sentinel|hook|MCP|runs\//i, r.reason);
      assert.match(r.reason, /請使用者自己執行/);
    }
  });
});

describe('fail-open', () => {
  it('a marker home that cannot be read never blocks', async () => {
    const out = await run(
      { tool_name: 'mcp__df-graph__calendar_create', tool_input: {}, session_id: sid(), cwd: TEST_BASE },
      { DAILY_AGENDA_HOME: '/dev/null/impossible' },
    );
    assert.deepEqual(out, {});
  });

  it('malformed tool input in a marked session never throws', () => {
    assert.deepEqual(checkMarkedSessionDeny('Bash', {}), { deny: false });
    assert.deepEqual(checkMarkedSessionDeny('Bash', { command: 42 }), { deny: false });
    assert.deepEqual(checkMarkedSessionDeny(undefined, undefined), { deny: false });
  });
});
