import { describe, it } from 'node:test';
import assert from 'node:assert/strict';
import { isBlockedLineTool } from './guard-core.mjs';
import { spawn } from 'node:child_process';
import { readFileSync } from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));

// ── Unit tests for isBlockedLineTool ──

describe('isBlockedLineTool', () => {
  // Tools that SHOULD be blocked
  it('blocks send_message_auto', () => {
    assert.equal(isBlockedLineTool('send_message_auto'), true);
  });

  it('blocks send_message_manual', () => {
    assert.equal(isBlockedLineTool('send_message_manual'), true);
  });

  it('blocks send_file_manual', () => {
    assert.equal(isBlockedLineTool('send_file_manual'), true);
  });

  it('blocks stage_line_reply', () => {
    assert.equal(isBlockedLineTool('stage_line_reply'), true);
  });

  it('blocks stage_line_forward', () => {
    assert.equal(isBlockedLineTool('stage_line_forward'), true);
  });

  it('blocks prepare_line_forward', () => {
    assert.equal(isBlockedLineTool('prepare_line_forward'), true);
  });

  it('blocks confirm_line_forward', () => {
    assert.equal(isBlockedLineTool('confirm_line_forward'), true);
  });

  it('blocks MCP line send tools', () => {
    assert.equal(isBlockedLineTool('mcp__line-desktop-mcp__send_message_auto'), true);
  });

  it('blocks MCP line reply tools', () => {
    assert.equal(isBlockedLineTool('mcp__line-desktop-mcp__stage_line_reply'), true);
  });

  it('blocks MCP line forward tools', () => {
    assert.equal(isBlockedLineTool('mcp__line-desktop-mcp__prepare_line_forward'), true);
  });

  it('blocks MCP plugin line send tools', () => {
    assert.equal(isBlockedLineTool('mcp__plugin_some-line-thing__send_message_auto'), true);
  });

  // Tools that SHOULD NOT be blocked
  it('allows our own proxy path', () => {
    assert.equal(isBlockedLineTool('mcp__plugin_daily-agenda_actions__prepare_action'), false);
    assert.equal(isBlockedLineTool('mcp__plugin_daily-agenda_actions__commit_action'), false);
    assert.equal(isBlockedLineTool('mcp__plugin_daily-agenda_actions__action_status'), false);
  });

  it('allows LINE read-only tools', () => {
    assert.equal(isBlockedLineTool('get_line_local_messages'), false);
    assert.equal(isBlockedLineTool('list_line_recent_chats'), false);
    assert.equal(isBlockedLineTool('mcp__line-desktop-mcp__get_line_local_messages'), false);
    assert.equal(isBlockedLineTool('mcp__line-desktop-mcp__check_line_send_target'), false);
  });

  it('allows df-graph tools (not blocked by design)', () => {
    assert.equal(isBlockedLineTool('mcp__df-graph__calendar_create'), false);
    assert.equal(isBlockedLineTool('mcp__df-graph__mail_send'), false);
    assert.equal(isBlockedLineTool('mcp__df-graph__calendar_update'), false);
  });

  it('does not false-positive on timeline/pipeline/online servers', () => {
    assert.equal(isBlockedLineTool('mcp__timeline-service__send_message'), false);
    assert.equal(isBlockedLineTool('mcp__pipeline-runner__send_data'), false);
    assert.equal(isBlockedLineTool('mcp__online-checker__reply_status'), false);
  });

  it('handles null/undefined/non-string gracefully', () => {
    assert.equal(isBlockedLineTool(null), false);
    assert.equal(isBlockedLineTool(undefined), false);
    assert.equal(isBlockedLineTool(42), false);
  });
});

// ── hooks.json matcher tests ──

describe('hooks.json matcher', () => {
  it('matcher regex matches LINE send-class tools', () => {
    const hooksJson = JSON.parse(readFileSync(path.join(__dirname, 'hooks.json'), 'utf8'));
    const matcherStr = hooksJson.hooks.PreToolUse[0].matcher;
    const matcher = new RegExp(matcherStr);

    // Should match
    assert.ok(matcher.test('send_message_auto'));
    assert.ok(matcher.test('send_message_manual'));
    assert.ok(matcher.test('send_file_manual'));
    assert.ok(matcher.test('stage_line_reply'));
    assert.ok(matcher.test('stage_line_forward'));
    assert.ok(matcher.test('prepare_line_forward'));
    assert.ok(matcher.test('confirm_line_forward'));
    assert.ok(matcher.test('mcp__line-desktop-mcp__send_message_auto'));
    assert.ok(matcher.test('mcp__plugin_some-line__stage_line_reply'));
  });

  it('matcher regex does NOT match non-LINE/non-send tools', () => {
    const hooksJson = JSON.parse(readFileSync(path.join(__dirname, 'hooks.json'), 'utf8'));
    const matcherStr = hooksJson.hooks.PreToolUse[0].matcher;
    const matcher = new RegExp(matcherStr);

    assert.ok(!matcher.test('Read'));
    assert.ok(!matcher.test('Bash'));
    assert.ok(!matcher.test('Edit'));
    assert.ok(!matcher.test('mcp__plugin_daily-agenda_actions__commit_action'));
    assert.ok(!matcher.test('mcp__df-graph__calendar_create'));
    assert.ok(!matcher.test('mcp__df-graph__mail_send'));
    assert.ok(!matcher.test('get_line_local_messages'));
  });
});

// ── Integration tests: hook stdin/stdout ──

const runHook = (input) => new Promise((resolve, reject) => {
  const child = spawn(process.execPath, [path.join(__dirname, 'guard.mjs')], {
    stdio: ['pipe', 'pipe', 'pipe'],
  });

  let stdout = '';
  let stderr = '';
  child.stdout.on('data', (d) => { stdout += d; });
  child.stderr.on('data', (d) => { stderr += d; });

  child.on('close', (code) => {
    try {
      resolve({ code, stdout: stdout.trim() ? JSON.parse(stdout.trim()) : {}, stderr });
    } catch {
      resolve({ code, stdout: {}, stderr });
    }
  });

  child.on('error', reject);

  if (input !== undefined) {
    child.stdin.write(typeof input === 'string' ? input : JSON.stringify(input));
  }
  child.stdin.end();
});

describe('guard hook integration', () => {
  it('denies LINE send tool via stdin', async () => {
    const result = await runHook({ tool_name: 'send_message_auto' });
    assert.equal(result.stdout.hookSpecificOutput?.permissionDecision, 'deny');
    assert.ok(result.stdout.hookSpecificOutput?.permissionDecisionReason?.includes('agenda-reply'));
  });

  it('allows non-LINE tools via stdin', async () => {
    const result = await runHook({ tool_name: 'Read' });
    assert.ok(!result.stdout.hookSpecificOutput?.permissionDecision || result.stdout.hookSpecificOutput?.permissionDecision !== 'deny');
  });

  it('allows proxy path tools via stdin', async () => {
    const result = await runHook({ tool_name: 'mcp__plugin_daily-agenda_actions__commit_action' });
    assert.ok(!result.stdout.hookSpecificOutput?.permissionDecision || result.stdout.hookSpecificOutput?.permissionDecision !== 'deny');
  });

  it('denies on broken JSON input (fail-closed for LINE class)', async () => {
    const result = await runHook('this is not json');
    assert.equal(result.stdout.hookSpecificOutput?.permissionDecision, 'deny');
  });

  it('denies on missing tool_name (fail-closed for LINE class)', async () => {
    const result = await runHook({ something: 'else' });
    assert.equal(result.stdout.hookSpecificOutput?.permissionDecision, 'deny');
  });
});
