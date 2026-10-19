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
    const runOutput = '{"ok":true,"code":null,"report_code":"R-7K2","sources":{"line":{"status":"ok","messages":340}},"counts":{"new":3,"updated":1},"new_chats_count":2,"new_chat_refs":["chat:abc","chat:def"]}';
    await runHook(
      {
        tool_name: 'Bash',
        tool_response: { stdout: runOutput, stderr: '' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!existsSync(path.join(testHome, 'taint', sessionId)),
      'run output must NOT taint');
  });

  // ── Item 1: PostToolUseFailure (failed Bash) ──

  it('R6-1: taints on PostToolUseFailure with sentinel in error', async () => {
    const sessionId = 'sess-post-failure-1';
    await runHook(
      {
        hook_event_name: 'PostToolUseFailure',
        tool_name: 'Bash',
        tool_input: { command: "uv run ... daily''_agenda chats list; false" },
        error: `Exit code 1\n{"ok":true,"chats":[{"name":"<<<UNTRUSTED-aabb ${SENTINEL}\\nchat\\n${SENTINEL} UNTRUSTED-aabb>>>>"}]}`,
        session_id: sessionId,
        transcript_path: '/tmp/transcript.jsonl',
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(existsSync(path.join(testHome, 'taint', sessionId)),
      'PostToolUseFailure with sentinel must taint');
  });

  it('R6-1: PostToolUseFailure without sentinel does not taint', async () => {
    const sessionId = 'sess-post-failure-clean';
    await runHook(
      {
        hook_event_name: 'PostToolUseFailure',
        tool_name: 'Bash',
        tool_input: { command: 'npm test' },
        error: 'Exit code 1\nError: test failed',
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!existsSync(path.join(testHome, 'taint', sessionId)));
  });

  // ── Item 3: protected-path detection in Grep content ──

  it('R6-3: Grep files_with_matches mode does NOT taint', async () => {
    const sessionId = 'sess-post-grep-fwm';
    await runHook(
      {
        tool_name: 'Grep',
        tool_input: { output_mode: 'files_with_matches', path: '/work' },
        tool_response: { matches: [`${testHome}/gen/1/agenda.html`] },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!existsSync(path.join(testHome, 'taint', sessionId)),
      'Grep files_with_matches must NOT taint');
  });

  it('R6-3: DA JSON output (draft_path under runs/) does NOT taint', async () => {
    const sessionId = 'sess-post-da-json';
    const output = `{"ok":true,"draft_path":"${testHome}/runs/drafts/abc/criteria-draft.md"}`;
    await runHook(
      {
        tool_name: 'Bash',
        tool_response: { stdout: output, stderr: '' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!existsSync(path.join(testHome, 'taint', sessionId)),
      'DA JSON output with runs/ path in value must NOT taint (not at line start)');
  });

  it('R6-3: progress new output does NOT taint', async () => {
    const sessionId = 'sess-post-progress-new';
    const output = `{"ok":true,"progress_file":"${testHome}/runs/progress/20261016.jsonl"}`;
    await runHook(
      {
        tool_name: 'Bash',
        tool_response: { stdout: output, stderr: '' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!existsSync(path.join(testHome, 'taint', sessionId)));
  });

  // ── Item 5a: marker write failure → decision:block ──

  // ── H3: marker write failure → continue:false (universal hard stop) ──

  it('H3: outputs continue:false when PostToolUse marker write fails', async () => {
    const sessionId = 'sess-post-write-fail';
    const badHome = '/dev/null/impossible';
    const result = await runHook(
      {
        tool_name: 'Bash',
        tool_response: { stdout: `output with ${SENTINEL}`, stderr: '' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: badHome },
    );
    assert.strictEqual(result.stdout?.continue, false,
      'Must emit continue:false when marker write fails');
    assert.ok(typeof result.stdout?.stopReason === 'string',
      'Must include a stopReason');
    assert.ok(result.stdout.stopReason.includes('安全'),
      'stopReason must mention safety');
  });

  it('H3: outputs continue:false when PostToolUseFailure marker write fails', async () => {
    const sessionId = 'sess-post-failure-write-fail';
    const badHome = '/dev/null/impossible';
    const result = await runHook(
      {
        hook_event_name: 'PostToolUseFailure',
        tool_name: 'Bash',
        error: `Exit code 1\n${SENTINEL} leaked data`,
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: badHome },
    );
    assert.strictEqual(result.stdout?.continue, false,
      'PostToolUseFailure must also emit continue:false on marker failure');
    assert.ok(typeof result.stdout?.stopReason === 'string');
  });

  // ── H2: Windows backslash in structured Grep output → taint ──

  // ── H2: MSYS form in Bash stdout → taint ──

  // ── H2: relative path prefix with cwd → taint ──

  // ── M: Grep with no output_mode (default = files_with_matches) → no taint ──

  it('M: Grep with no output_mode does NOT taint (default=files_with_matches)', async () => {
    const sessionId = 'sess-m-grep-default';
    await runHook(
      {
        tool_name: 'Grep',
        tool_input: { path: '/work' },
        tool_response: { matches: [`${testHome}/gen/1/agenda.html`] },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!existsSync(path.join(testHome, 'taint', sessionId)),
      'Grep with no output_mode must be treated as files_with_matches (no content taint)');
  });

  // ── Existing non-taint cases still work ──

  it('DA JSON output with runs/ path still does NOT taint', async () => {
    const sessionId = 'sess-still-no-taint-json';
    const output = `{"ok":true,"draft_path":"${testHome}/runs/drafts/abc/criteria-draft.md"}`;
    await runHook(
      {
        tool_name: 'Bash',
        tool_response: { stdout: output, stderr: '' },
        session_id: sessionId,
      },
      { DAILY_AGENDA_HOME: testHome },
    );
    assert.ok(!existsSync(path.join(testHome, 'taint', sessionId)),
      'DA JSON value paths must NOT taint');
  });
});
