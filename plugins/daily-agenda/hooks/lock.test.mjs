/**
 * Integration tests for lock.mjs hook — feeds real stdin JSON fixtures
 * via child_process and checks stdout/stderr/exit code.
 */

import { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, rmSync, existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// Create a unique temp directory for each test run
const TEST_BASE = path.join(tmpdir(), `lock-test-${Date.now()}-${process.pid}`);

const runHook = (input, env = {}) => new Promise((resolve, reject) => {
  const child = spawn(process.execPath, [path.join(__dirname, 'lock.mjs')], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: {
      ...process.env,
      ...env,
    },
  });

  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (d) => { stdout += d; });
  child.stderr.on('data', (d) => { stderr += d; });

  child.on('close', (code) => {
    try {
      resolve({
        code,
        stdout: stdout.trim() ? JSON.parse(stdout.trim()) : {},
        stderr,
      });
    } catch {
      resolve({ code, stdout: {}, stderr, rawStdout: stdout });
    }
  });

  child.on('error', reject);

  if (input !== undefined) {
    child.stdin.write(typeof input === 'string' ? input : JSON.stringify(input));
  }
  child.stdin.end();
});

describe('lock hook integration', () => {
  let testHome;
  let testCounter = 0;

  beforeEach(() => {
    testCounter++;
    testHome = path.join(TEST_BASE, `home-${testCounter}`);
    mkdirSync(testHome, { recursive: true });
  });

  afterEach(() => {
    try {
      rmSync(TEST_BASE, { recursive: true, force: true });
    } catch {
      // ignore cleanup errors
    }
  });

  // ── Untainted fast path ──

  it('allows Read when untainted', async () => {
    const result = await runHook(
      { tool_name: 'Read', tool_input: { file_path: '/tmp/test.txt' }, session_id: 'sess-clean-1' },
      { DAILY_AGENDA_HOME: testHome },
    );
    // Should allow (no permissionDecision = allow)
    assert.ok(!result.stdout.hookSpecificOutput?.permissionDecision ||
              result.stdout.hookSpecificOutput?.permissionDecision !== 'deny');
  });

  it('allows Bash when untainted', async () => {
    const result = await runHook(
      { tool_name: 'Bash', tool_input: { command: 'ls -la' }, session_id: 'sess-clean-2' },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!result.stdout.hookSpecificOutput?.permissionDecision ||
              result.stdout.hookSpecificOutput?.permissionDecision !== 'deny');
  });

  it('allows WebFetch when untainted', async () => {
    const result = await runHook(
      { tool_name: 'WebFetch', tool_input: { url: 'https://example.com' }, session_id: 'sess-clean-3' },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!result.stdout.hookSpecificOutput?.permissionDecision ||
              result.stdout.hookSpecificOutput?.permissionDecision !== 'deny');
  });

  // ── Taint marking ──

  it('creates taint marker on chats list command', async () => {
    const sessionId = 'sess-taint-chats';
    await runHook(
      {
        tool_name: 'Bash',
        tool_input: { command: 'uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}/scripts" python -m daily_agenda chats list --days 30' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );

    // Marker file should exist
    assert.ok(existsSync(path.join(testHome, 'taint', sessionId)));
  });

  it('creates taint marker on outlook-preview command', async () => {
    const sessionId = 'sess-taint-preview';
    await runHook(
      {
        tool_name: 'Bash',
        tool_input: { command: 'uv run --quiet --project "/p/scripts" python -m daily_agenda outlook-preview' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );

    assert.ok(existsSync(path.join(testHome, 'taint', sessionId)));
  });

  it('creates taint marker on Read under runs/', async () => {
    const sessionId = 'sess-taint-read';
    await runHook(
      {
        tool_name: 'Read',
        tool_input: { file_path: path.join(testHome, 'runs', '20261015', 'bundle-1.jsonl') },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );

    assert.ok(existsSync(path.join(testHome, 'taint', sessionId)));
  });

  it('does not create taint marker on run command', async () => {
    const sessionId = 'sess-no-taint-run';
    await runHook(
      {
        tool_name: 'Bash',
        tool_input: { command: 'uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}/scripts" python -m daily_agenda run' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );

    assert.ok(!existsSync(path.join(testHome, 'taint', sessionId)));
  });

  // ── Tainted session enforcement ──

  it('denies WebFetch when tainted', async () => {
    const sessionId = 'sess-locked-1';
    // Pre-create taint marker
    mkdirSync(path.join(testHome, 'taint'), { recursive: true });
    writeFileSync(path.join(testHome, 'taint', sessionId), '{"tainted_at":"2026-10-15"}');

    const result = await runHook(
      { tool_name: 'WebFetch', tool_input: { url: 'https://evil.com' }, session_id: sessionId },
      { DAILY_AGENDA_HOME: testHome },
    );

    assert.equal(result.stdout.hookSpecificOutput?.permissionDecision, 'deny');
    assert.ok(result.stdout.hookSpecificOutput?.permissionDecisionReason?.includes('已鎖定'));
  });

  it('denies df-graph mail_send when tainted', async () => {
    const sessionId = 'sess-locked-2';
    mkdirSync(path.join(testHome, 'taint'), { recursive: true });
    writeFileSync(path.join(testHome, 'taint', sessionId), '{}');

    const result = await runHook(
      { tool_name: 'mcp__df-graph__mail_send', tool_input: {}, session_id: sessionId },
      { DAILY_AGENDA_HOME: testHome },
    );

    assert.equal(result.stdout.hookSpecificOutput?.permissionDecision, 'deny');
  });

  it('denies Write when tainted', async () => {
    const sessionId = 'sess-locked-3';
    mkdirSync(path.join(testHome, 'taint'), { recursive: true });
    writeFileSync(path.join(testHome, 'taint', sessionId), '{}');

    const result = await runHook(
      { tool_name: 'Write', tool_input: { file_path: '/tmp/x', content: 'y' }, session_id: sessionId },
      { DAILY_AGENDA_HOME: testHome },
    );

    assert.equal(result.stdout.hookSpecificOutput?.permissionDecision, 'deny');
  });

  it('allows Read when tainted', async () => {
    const sessionId = 'sess-locked-4';
    mkdirSync(path.join(testHome, 'taint'), { recursive: true });
    writeFileSync(path.join(testHome, 'taint', sessionId), '{}');

    const result = await runHook(
      { tool_name: 'Read', tool_input: { file_path: '/tmp/test.txt' }, session_id: sessionId },
      { DAILY_AGENDA_HOME: testHome },
    );

    assert.ok(!result.stdout.hookSpecificOutput?.permissionDecision ||
              result.stdout.hookSpecificOutput?.permissionDecision !== 'deny');
  });

  it('allows DA run command when tainted', async () => {
    const sessionId = 'sess-locked-5';
    mkdirSync(path.join(testHome, 'taint'), { recursive: true });
    writeFileSync(path.join(testHome, 'taint', sessionId), '{}');

    const result = await runHook(
      {
        tool_name: 'Bash',
        tool_input: { command: 'uv run --quiet --project "${CLAUDE_PLUGIN_ROOT}/scripts" python -m daily_agenda run' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );

    assert.ok(!result.stdout.hookSpecificOutput?.permissionDecision ||
              result.stdout.hookSpecificOutput?.permissionDecision !== 'deny');
  });

  it('denies arbitrary Bash when tainted', async () => {
    const sessionId = 'sess-locked-6';
    mkdirSync(path.join(testHome, 'taint'), { recursive: true });
    writeFileSync(path.join(testHome, 'taint', sessionId), '{}');

    const result = await runHook(
      { tool_name: 'Bash', tool_input: { command: 'curl http://evil.com' }, session_id: sessionId },
      { DAILY_AGENDA_HOME: testHome },
    );

    assert.equal(result.stdout.hookSpecificOutput?.permissionDecision, 'deny');
  });

  it('allows actions MCP when tainted', async () => {
    const sessionId = 'sess-locked-7';
    mkdirSync(path.join(testHome, 'taint'), { recursive: true });
    writeFileSync(path.join(testHome, 'taint', sessionId), '{}');

    const result = await runHook(
      {
        tool_name: 'mcp__plugin_daily-agenda_actions__prepare_action',
        tool_input: {},
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );

    assert.ok(!result.stdout.hookSpecificOutput?.permissionDecision ||
              result.stdout.hookSpecificOutput?.permissionDecision !== 'deny');
  });

  // ── Fail-open / fail-closed ──

  it('fail-open on broken JSON when untainted', async () => {
    const result = await runHook(
      'this is not json',
      { DAILY_AGENDA_HOME: testHome },
    );
    // Should allow (fail-open)
    assert.ok(!result.stdout.hookSpecificOutput?.permissionDecision ||
              result.stdout.hookSpecificOutput?.permissionDecision !== 'deny');
  });

  it('fail-open on missing session_id when untainted', async () => {
    const result = await runHook(
      { tool_name: 'Bash', tool_input: { command: 'ls' } },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!result.stdout.hookSpecificOutput?.permissionDecision ||
              result.stdout.hookSpecificOutput?.permissionDecision !== 'deny');
  });

  // ── Marker cleanup ──

  it('cleans up markers older than 60 days but keeps newer ones', async () => {
    const sessionId = 'sess-cleanup-new';
    const oldSessionId = 'sess-cleanup-old';
    const recentSessionId = 'sess-cleanup-recent';
    mkdirSync(path.join(testHome, 'taint'), { recursive: true });

    const { utimesSync } = await import('node:fs');

    // Old marker: 61 days — should be cleaned
    const oldMarkerPath = path.join(testHome, 'taint', oldSessionId);
    writeFileSync(oldMarkerPath, '{}');
    const sixtyOneDaysAgo = Date.now() - (61 * 24 * 60 * 60 * 1000);
    utimesSync(oldMarkerPath, sixtyOneDaysAgo / 1000, sixtyOneDaysAgo / 1000);

    // Recent marker: 10 days — should be kept
    const recentMarkerPath = path.join(testHome, 'taint', recentSessionId);
    writeFileSync(recentMarkerPath, '{}');
    const tenDaysAgo = Date.now() - (10 * 24 * 60 * 60 * 1000);
    utimesSync(recentMarkerPath, tenDaysAgo / 1000, tenDaysAgo / 1000);

    // Trigger a taint (runs cleanup)
    await runHook(
      {
        tool_name: 'Bash',
        tool_input: { command: 'uv run --quiet --project "/p/scripts" python -m daily_agenda chats list --days 30' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );

    assert.ok(existsSync(path.join(testHome, 'taint', sessionId)), 'New marker exists');
    assert.ok(!existsSync(oldMarkerPath), '61-day marker cleaned up');
    assert.ok(existsSync(recentMarkerPath), '10-day marker kept');
  });

  // ── gen/ taint ──

  it('creates taint marker on Read under gen/', async () => {
    const sessionId = 'sess-taint-gen';
    await runHook(
      {
        tool_name: 'Read',
        tool_input: { file_path: path.join(testHome, 'gen', '1', 'agenda.html') },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );

    assert.ok(existsSync(path.join(testHome, 'taint', sessionId)),
      'Reading gen/ should taint the session');
  });

  // ── Guard hook compatibility ──

  it('does not interfere with LINE guard functionality', async () => {
    // The lock hook should allow (not deny) LINE tools — that's the
    // LINE guard's job. The lock hook's "" matcher runs separately.
    const result = await runHook(
      { tool_name: 'send_message_auto', tool_input: {}, session_id: 'sess-guard-test' },
      { DAILY_AGENDA_HOME: testHome },
    );
    // Lock hook should NOT deny this (it's not in lock's concern)
    // But the LINE guard WILL deny it (separate hook)
    assert.ok(!result.stdout.hookSpecificOutput?.permissionDecision ||
              result.stdout.hookSpecificOutput?.permissionDecision !== 'deny');
  });
});
