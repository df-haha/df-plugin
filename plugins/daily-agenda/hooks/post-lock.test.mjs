/**
 * Integration tests for post-lock.mjs (PostToolUse sentinel detection).
 */

import { describe, it, beforeEach, afterEach } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import { mkdirSync, writeFileSync, rmSync, existsSync, readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { tmpdir } from 'node:os';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SENTINEL = 'DA-RAW-3f9a';
const TEST_BASE = path.join(tmpdir(), `post-lock-test-${Date.now()}-${process.pid}`);

const runHook = (input, env = {}) => new Promise((resolve, reject) => {
  const child = spawn(process.execPath, [path.join(__dirname, 'post-lock.mjs')], {
    stdio: ['pipe', 'pipe', 'pipe'],
    env: { ...process.env, ...env },
  });

  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (d) => { stdout += d; });
  child.stderr.on('data', (d) => { stderr += d; });

  child.on('close', (code) => {
    try {
      resolve({ code, stdout: stdout.trim() ? JSON.parse(stdout.trim()) : {}, stderr });
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

describe('PostToolUse sentinel detection', () => {
  let testHome;
  let testCounter = 0;

  beforeEach(() => {
    testCounter++;
    testHome = path.join(TEST_BASE, `home-${testCounter}`);
    mkdirSync(testHome, { recursive: true });
  });

  afterEach(() => {
    try { rmSync(TEST_BASE, { recursive: true, force: true }); } catch { /* ignore */ }
  });

  // ── Bash stdout with sentinel → taint ──

  it('taints on Bash stdout containing sentinel', async () => {
    const sessionId = 'sess-post-bash';
    const result = await runHook(
      {
        tool_name: 'Bash',
        tool_response: { stdout: `some output ${SENTINEL} more`, stderr: '', interrupted: false },
        session_id: sessionId,
        transcript_path: '/tmp/fake-transcript.jsonl',
      },
      { DAILY_AGENDA_HOME: testHome },
    );

    assert.ok(existsSync(path.join(testHome, 'taint', sessionId)),
      'Bash stdout with sentinel should taint');
    const marker = JSON.parse(readFileSync(path.join(testHome, 'taint', sessionId), 'utf8'));
    assert.equal(marker.transcript_path, '/tmp/fake-transcript.jsonl');
  });

  it('taints on Bash stderr containing sentinel', async () => {
    const sessionId = 'sess-post-bash-stderr';
    await runHook(
      {
        tool_name: 'Bash',
        tool_response: { stdout: 'clean', stderr: `error ${SENTINEL}`, interrupted: false },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(existsSync(path.join(testHome, 'taint', sessionId)));
  });

  // ── Read with sentinel → taint ──

  it('taints on Read result containing sentinel', async () => {
    const sessionId = 'sess-post-read';
    await runHook(
      {
        tool_name: 'Read',
        tool_response: `line 1\n${SENTINEL} line 2\nline 3`,
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(existsSync(path.join(testHome, 'taint', sessionId)));
  });

  // ── Grep with sentinel → taint ──

  it('taints on Grep result containing sentinel', async () => {
    const sessionId = 'sess-post-grep';
    await runHook(
      {
        tool_name: 'Grep',
        tool_response: { matches: [{ line: `match ${SENTINEL}` }] },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(existsSync(path.join(testHome, 'taint', sessionId)));
  });

  // ── No sentinel → no taint ──

  it('does not taint on clean Bash output', async () => {
    const sessionId = 'sess-post-clean-bash';
    await runHook(
      {
        tool_name: 'Bash',
        tool_response: { stdout: 'hello world', stderr: '' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!existsSync(path.join(testHome, 'taint', sessionId)));
  });

  it('does not taint on clean Read output', async () => {
    const sessionId = 'sess-post-clean-read';
    await runHook(
      {
        tool_name: 'Read',
        tool_response: 'just normal file content',
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!existsSync(path.join(testHome, 'taint', sessionId)));
  });

  // ── Model-derived output (no sentinel) → no taint ──

  it('does not taint on criteria show output (model-derived fence)', async () => {
    const sessionId = 'sess-post-model-derived';
    // Simulates: criteria show output with fence but NO sentinel
    const fencedOutput = '<<<UNTRUSTED-aabbccdd11223344\nSome criteria text\nUNTRUSTED-aabbccdd11223344>>>>';
    await runHook(
      {
        tool_name: 'Bash',
        tool_response: { stdout: `{"ok":true,"text":"${fencedOutput}"}`, stderr: '' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!existsSync(path.join(testHome, 'taint', sessionId)),
      'Model-derived fenced text (no sentinel) must NOT taint');
  });

  // ── Already tainted → no-op ──

  it('does nothing when already tainted', async () => {
    const sessionId = 'sess-post-already-tainted';
    mkdirSync(path.join(testHome, 'taint'), { recursive: true });
    writeFileSync(path.join(testHome, 'taint', sessionId), '{"already":true}');

    await runHook(
      {
        tool_name: 'Bash',
        tool_response: { stdout: `${SENTINEL}`, stderr: '' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );

    // Marker should not be overwritten
    const marker = JSON.parse(readFileSync(path.join(testHome, 'taint', sessionId), 'utf8'));
    assert.ok(marker.already, 'Original marker should not be overwritten');
  });

  // ── Fail-safe: malformed JSON → no crash ──

  it('handles malformed input gracefully', async () => {
    const result = await runHook('not json', { DAILY_AGENDA_HOME: testHome });
    assert.equal(result.code, 0, 'Should exit 0 even on bad input');
  });

  it('handles missing tool_response gracefully', async () => {
    const result = await runHook(
      { tool_name: 'Bash', session_id: 'sess-missing-resp' },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.equal(result.code, 0);
    assert.ok(!existsSync(path.join(testHome, 'taint', 'sess-missing-resp')));
  });

  // ── Shell bypass strings + sentinel in output → taint ──
  // These are the Codex R2 bypass forms; the PreToolUse hook can't catch them,
  // but the PostToolUse hook catches the sentinel in the output.

  it('bypass: pyt\'\'hon output with sentinel → taint', async () => {
    const sessionId = 'sess-bypass-1';
    await runHook(
      {
        tool_name: 'Bash',
        tool_response: { stdout: `{"ok":true,"chats":[{"name":"${SENTINEL} chat"}]}`, stderr: '' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(existsSync(path.join(testHome, 'taint', sessionId)),
      'Shell bypass with sentinel in output must taint');
  });

  it('bypass: cat with shell expansion + sentinel in output → taint', async () => {
    const sessionId = 'sess-bypass-2';
    await runHook(
      {
        tool_name: 'Read',
        tool_response: `{"kind":"line_message","_raw":"${SENTINEL}","untrusted":{"text":"hello"}}`,
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(existsSync(path.join(testHome, 'taint', sessionId)));
  });

  // ── Object-shaped Read tool_response ──

  it('taints on object-shaped Read tool_response with sentinel', async () => {
    const sessionId = 'sess-post-read-obj';
    await runHook(
      {
        tool_name: 'Read',
        tool_response: { type: 'text', file: { filePath: '/x', content: `line ${SENTINEL}` } },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(existsSync(path.join(testHome, 'taint', sessionId)),
      'Object-shaped Read with sentinel in nested content must taint');
  });

  it('does not taint on object-shaped Read without sentinel', async () => {
    const sessionId = 'sess-post-read-obj-clean';
    await runHook(
      {
        tool_name: 'Read',
        tool_response: { type: 'text', file: { filePath: '/x', content: 'clean content' } },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!existsSync(path.join(testHome, 'taint', sessionId)));
  });

  // ── Monitor tool_response with sentinel ──

  it('taints on Monitor tool_response with sentinel', async () => {
    const sessionId = 'sess-post-monitor';
    await runHook(
      {
        tool_name: 'Monitor',
        tool_response: { events: [`progress: ${SENTINEL} reading`] },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(existsSync(path.join(testHome, 'taint', sessionId)),
      'Monitor output with sentinel must taint');
  });

  // ── run output must NOT contain sentinel ──

  it('run summary JSON (simulated) does not trigger taint', async () => {
    const sessionId = 'sess-post-run-clean';
    // Simulates the output of `daily_agenda run` — counts, refs, no sentinel
    const runOutput = '{"ok":true,"code":null,"report_code":"R-7K2","sources":{"line":{"status":"ok","messages":340}},"counts":{"new":3,"updated":1},"new_chats":["<<<UNTRUSTED-aabbccdd11223344\\nchat name\\nUNTRUSTED-aabbccdd11223344>>>>"]}';
    await runHook(
      {
        tool_name: 'Bash',
        tool_response: { stdout: runOutput, stderr: '' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!existsSync(path.join(testHome, 'taint', sessionId)),
      'run output (model-derived fenced names, no sentinel) must NOT taint');
  });
});
