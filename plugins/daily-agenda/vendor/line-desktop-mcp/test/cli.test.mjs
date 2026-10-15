import assert from 'node:assert/strict';
import { spawnSync } from 'node:child_process';
import { createHash } from 'node:crypto';
import { mkdtemp, readFile, rm } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  CLI_CAPABILITIES,
  PACKAGE_VERSION,
  executeCliCommand,
  formatHumanSuccess,
  parseCliArguments,
  runCli,
} from '../src/cli/commands.mjs';
import { CLI_EXIT_CODES, errorResult, exitCodeForError } from '../src/cli/result.mjs';
import { readLocalLineMessages } from '../src/extensions/line-local-reader.mjs';

const cliPath = fileURLToPath(new URL('../src/cli.js', import.meta.url));

function cursorFor({ chatName, dateFrom, dateTo, query, chatType, time, id = 'fixture-1' }) {
  const fields = [chatName, dateFrom, dateTo, query ?? null];
  if (chatType && chatType !== 'auto') fields.push(chatType);
  const sourceTime = time ?? Date.parse(`${dateFrom}T00:00:00+08:00`);
  return Buffer.from(JSON.stringify({
    v: 1,
    scope: createHash('sha256').update(JSON.stringify(fields)).digest('hex'),
    chat: `chat:${'a'.repeat(24)}`,
    time: sourceTime,
    id,
  })).toString('base64url');
}

function fixturePage(scope) {
  return {
    chatName: scope.chatName,
    count: 1,
    messages: [{
      sourceRef: `message:${'b'.repeat(24)}`,
      sourceMessageId: 'fixture-message',
      sourceTimestamp: 1_788_854_400_000,
      date: scope.dateFrom,
      time: '10:15',
      sender: '測試者',
      kind: 'message',
      text: '完整第一行\n第二行😀',
    }],
    scope: { kind: 'local_database', truncated: true, requested: scope },
    freshness: { snapshotCreatedAt: '2026-09-22T02:15:00.000Z', sourceStable: true },
    pagination: { hasMore: true, nextCursor: 'fixture-next-cursor' },
    warnings: [{ code: 'FIXTURE_WARNING', detail: 'synthetic page boundary' }],
  };
}

function capture() {
  let stdout = '';
  let stderr = '';
  return {
    stdout: { write: value => { stdout += value; } },
    stderr: { write: value => { stderr += value; } },
    result: () => ({ stdout, stderr }),
  };
}

function runnerOptions(streams, { requestId = 'fixture-request-id' } = {}) {
  const times = [100, 117];
  return {
    ...streams,
    now: () => times.shift() ?? 117,
    requestId,
    packageVersion: PACKAGE_VERSION,
  };
}

test('parser accepts Unicode scope and rejects missing, unknown, and duplicate options before a reader can run', async () => {
  const unicode = parseCliArguments(['messages', 'read', '--chat', '臺灣😀 專案群', '--from', '2026-09-01', '--to', '2026-09-07', '--limit', '200']);
  assert.equal(unicode.command, 'messages.read');
  assert.equal(unicode.scope.chatName, '臺灣😀 專案群');
  assert.equal(unicode.scope.messageLimit, 200);

  for (const [argv, code] of [
    [['messages', 'read', '--chat', '群組', '--from', '2026-09-01'], 'CLI_MISSING_ARGUMENT'],
    [['messages', 'read', '--chat', '群組', '--chat', '另一個', '--from', '2026-09-01', '--to', '2026-09-02'], 'CLI_INVALID_ARGUMENT'],
    [['messages', 'read', '--chat', '群組', '--from', '2026-09-01', '--to', '2026-09-02', '--allchat'], 'CLI_INVALID_ARGUMENT'],
    [['messages', 'scan'], 'CLI_UNKNOWN_COMMAND'],
  ]) {
    assert.throws(() => parseCliArguments(argv), error => error?.code === code);
  }

  let reads = 0;
  const streams = capture();
  const exitCode = await runCli(['messages', 'read', '--chat', '群組', '--from', '2026-09-01', '--json'], {
    readLocalLineMessages: async () => { reads++; return {}; },
  }, runnerOptions(streams));
  assert.equal(exitCode, CLI_EXIT_CODES.input);
  assert.equal(reads, 0);
  assert.equal(JSON.parse(streams.result().stdout).error.code, 'CLI_MISSING_ARGUMENT');
});

test('read command preserves validated scope, freshness, warnings, and pagination without invoking GUI code', async () => {
  const chatName = '測試群組😀';
  const dateFrom = '2026-09-01';
  const dateTo = '2026-09-07';
  const query = '完整';
  const cursor = cursorFor({ chatName, dateFrom, dateTo, query, chatType: 'group' });
  const parsed = parseCliArguments(['messages', 'read', '--chat', chatName, '--from', dateFrom, '--to', dateTo,
    '--chat-type', 'group', '--query', query, '--cursor', cursor]);
  let receivedScope;
  const data = await executeCliCommand(parsed, {
    readLocalLineMessages: async scope => {
      receivedScope = scope;
      return fixturePage(scope);
    },
  });

  assert.deepEqual(receivedScope, {
    chatName, dateFrom, dateTo, chatType: 'group', query, cursor, messageLimit: 200, mediaMode: 'metadata',
  });
  assert.equal(data.pagination.hasMore, true);
  assert.equal(data.pagination.nextCursor, 'fixture-next-cursor');
  assert.equal(data.freshness.sourceStable, true);
  assert.equal(data.warnings[0].code, 'FIXTURE_WARNING');

  const human = formatHumanSuccess('messages.read', data);
  assert.match(human, /date: 2026-09-01\.\.2026-09-07/u);
  assert.match(human, /freshness:/u);
  assert.match(human, /hasMore: true/u);
  assert.match(human, /nextCursor: fixture-next-cursor/u);
  assert.match(human, /FIXTURE_WARNING/u);
  assert.match(human, /完整第一行\n第二行😀/u);
});

test('status accepts valid not_running metadata but makes an unverified build a runtime failure', async () => {
  const notRunning = { ok: true, client: { verified: true }, process: { state: 'not_running' } };
  const streams = capture();
  const complete = await runCli(['status', '--json'], { readLineClientStatus: async () => notRunning }, runnerOptions(streams));
  assert.equal(complete, CLI_EXIT_CODES.success);
  const result = JSON.parse(streams.result().stdout);
  assert.equal(result.ok, true);
  assert.equal(result.data.process.state, 'not_running');

  const failedStreams = capture();
  const failed = await runCli(['status', '--json'], {
    readLineClientStatus: async () => ({ ok: false, code: 'LINE_BUILD_UNVERIFIED', client: { verified: false }, process: { state: 'not_checked' } }),
  }, runnerOptions(failedStreams));
  assert.equal(failed, CLI_EXIT_CODES.runtime);
  const failure = JSON.parse(failedStreams.result().stdout);
  assert.equal(failure.ok, false);
  assert.equal(failure.error.code, 'LINE_BUILD_UNVERIFIED');
});

test('export uses the local export helper with one validated metadata scope and preserves its projection result', async () => {
  const parsed = parseCliArguments(['messages', 'export', '--chat', '匯出群組', '--from', '2026-09-03', '--to', '2026-09-04',
    '--format', 'csv', '--out', 'C:\\exports\\fixture.csv']);
  let request;
  const exported = {
    chatName: '匯出群組', outputPath: 'C:\\exports\\fixture.csv', format: 'csv', exportFormat: 'line-local-history-v1',
    bytes: 42, sha256: 'a'.repeat(64), count: 1,
    scope: { kind: 'local_database', truncated: true, requested: { ...parsed.scope, mediaMode: 'metadata' } },
    freshness: { snapshotCreatedAt: '2026-09-22T02:15:00.000Z' },
    pagination: { hasMore: true, nextCursor: 'fixture-next-cursor' },
    warnings: [{ code: 'FIXTURE_WARNING' }], verified: true,
    projection: { columns: ['date', 'time', 'sender', 'kind', 'text'], omittedFields: ['sourceRef'], note: 'projection fixture' },
  };
  const data = await executeCliCommand(parsed, {
    exportLocalLineMessages: async value => {
      request = value;
      return exported;
    },
  });

  assert.deepEqual(request, {
    outputPath: 'C:\\exports\\fixture.csv', format: 'csv', chatName: '匯出群組',
    dateFrom: '2026-09-03', dateTo: '2026-09-04', messageLimit: 200, mediaMode: 'metadata',
  });
  assert.equal(data, exported);
  const human = formatHumanSuccess('messages.export', data);
  assert.match(human, /date: 2026-09-03\.\.2026-09-04/u);
  assert.match(human, /freshness:/u);
  assert.match(human, /hasMore: true/u);
  assert.match(human, /nextCursor: fixture-next-cursor/u);
  assert.match(human, /projection:/u);
  assert.match(human, /FIXTURE_WARNING/u);

  const streams = capture();
  const exitCode = await runCli(['messages', 'export', '--chat', '匯出群組', '--from', '2026-09-03', '--to', '2026-09-04',
    '--format', 'csv', '--out', 'C:\\exports\\fixture.csv', '--json'], {
    exportLocalLineMessages: async value => {
      assert.equal(value.mediaMode, 'metadata');
      return exported;
    },
  }, runnerOptions(streams));
  assert.equal(exitCode, CLI_EXIT_CODES.success);
  const envelope = JSON.parse(streams.result().stdout);
  assert.equal(envelope.command, 'messages.export');
  assert.deepEqual(envelope.data.pagination, exported.pagination);
  assert.deepEqual(envelope.data.projection, exported.projection);
});

test('JSON envelopes and exit mapping keep safe codes and side-effect uncertainty precedence', async () => {
  assert.equal(exitCodeForError({ code: 'EEXIST' }), CLI_EXIT_CODES.input);
  assert.equal(exitCodeForError({ code: 'ENOENT' }), CLI_EXIT_CODES.input);
  assert.equal(exitCodeForError({ code: 'CHAT_NOT_FOUND' }), CLI_EXIT_CODES.scope);
  assert.equal(exitCodeForError({ code: 'ENOSPC' }), CLI_EXIT_CODES.execution);
  assert.equal(exitCodeForError({ code: 'CHAT_NOT_FOUND', operationMayHaveCompleted: true }), CLI_EXIT_CODES.sideEffectUncertain);
  const uncertain = errorResult({ code: 'LINE_EXPORT_VERIFY_FAILED', details: { operationMayHaveCompleted: true, outputPathCreated: true } });
  assert.equal(uncertain.exitCode, CLI_EXIT_CODES.sideEffectUncertain);
  assert.equal(uncertain.code, 'LINE_EXPORT_VERIFY_FAILED');
  assert.equal(uncertain.details.outputPathCreated, true);

  const streams = capture();
  const exitCode = await runCli(['messages', 'read', '--chat', '群組', '--from', '2026-09-01', '--to', '2026-09-02', '--json'], {
    readLocalLineMessages: async scope => fixturePage(scope),
  }, runnerOptions(streams));
  assert.equal(exitCode, CLI_EXIT_CODES.success);
  const { stdout, stderr } = streams.result();
  assert.equal(stderr, '');
  assert.equal(stdout.trim().split('\n').length, 1);
  const envelope = JSON.parse(stdout);
  assert.equal(envelope.schemaVersion, 1);
  assert.equal(envelope.requestId, 'fixture-request-id');
  assert.equal(envelope.command, 'messages.read');
  assert.equal(envelope.meta.source, 'local-line-reader');
  assert.equal(envelope.meta.durationMs, 17);
});

test('reader data failures retain actionable execution exits and invalid timeout is a runtime failure', async () => {
  for (const code of ['RESULT_TOO_LARGE', 'INVALID_SOURCE_TIME', 'INVALID_SOURCE_ID', 'INVALID_PATH',
    'DATABASE_HEADER_INVALID', 'DATABASE_SIZE_INVALID', 'WAL_HEADER_INVALID', 'WAL_PAGE_SIZE_MISMATCH',
    'WAL_NO_VALID_COMMIT', 'LOCAL_READER_TIMEOUT_INVALID']) {
    const streams = capture();
    const exitCode = await runCli(['messages', 'read', '--chat', 'Synthetic', '--from', '2026-09-01', '--to', '2026-09-02', '--json'], {
      readLocalLineMessages: async () => { throw Object.assign(new Error('private source diagnostics'), { code }); },
    }, runnerOptions(streams));
    assert.equal(exitCode, code === 'LOCAL_READER_TIMEOUT_INVALID' ? 4 : 5);
    const envelope = JSON.parse(streams.result().stdout);
    assert.equal(envelope.ok, false);
    assert.equal(envelope.command, 'messages.read');
    assert.equal(envelope.error.code, code);
    assert.equal(envelope.error.operationMayHaveCompleted, false);
    assert.doesNotMatch(envelope.error.message, /private source diagnostics/u);
    assert.equal(streams.result().stderr, '');
  }
});

test('real CLI subprocess handles offline help, capabilities, invalid input, and absent status runtime from another cwd', async t => {
  const cwd = await mkdtemp(join(tmpdir(), 'line-cli-offline-'));
  t.after(async () => { await rm(cwd, { recursive: true, force: true }); });
  const env = { ...process.env };
  delete env.LINE_MCP_CUA_DRIVER;
  delete env.LINE_MCP_PYTHON;
  delete env.LINE_MCP_SQLITE3MC_DLL;
  const invoke = args => spawnSync(process.execPath, [cliPath, ...args], {
    cwd, env, encoding: 'utf8', windowsHide: true, timeout: 5_000,
  });

  const help = invoke(['--help']);
  assert.ifError(help.error);
  assert.equal(help.status, 0);
  assert.match(help.stdout, /M1 is local-read-only/u);
  assert.equal(help.stderr, '');

  const capabilities = invoke(['capabilities', '--json']);
  assert.ifError(capabilities.error);
  assert.equal(capabilities.status, 0);
  const capabilityEnvelope = JSON.parse(capabilities.stdout);
  assert.equal(capabilityEnvelope.ok, true);
  assert.deepEqual(capabilityEnvelope.data, CLI_CAPABILITIES);

  const invalid = invoke(['messages', 'read', '--chat', '群組', '--from', '2026-09-01', '--json']);
  assert.ifError(invalid.error);
  assert.equal(invalid.status, CLI_EXIT_CODES.input);
  assert.equal(invalid.stderr, '');
  const invalidEnvelope = JSON.parse(invalid.stdout);
  assert.equal(invalidEnvelope.command, 'messages.read');
  assert.equal(invalidEnvelope.error.code, 'CLI_MISSING_ARGUMENT');

  const unavailable = invoke(['status', '--json']);
  assert.ifError(unavailable.error);
  assert.equal(unavailable.status, CLI_EXIT_CODES.runtime);
  assert.equal(unavailable.stderr, '');
  assert.equal(JSON.parse(unavailable.stdout).error.code, 'LINE_CLIENT_STATUS_UNAVAILABLE');
});

test('M1 CLI modules do not import GUI or CUA implementation modules', async () => {
  const source = await readFile(new URL('../src/cli/commands.mjs', import.meta.url), 'utf8');
  const entrypoint = await readFile(new URL('../src/cli.js', import.meta.url), 'utf8');
  assert.doesNotMatch(source, /from ['"][^'"]*(?:line-ui|cua-|line-automation)[^'"]*['"]/u);
  assert.doesNotMatch(entrypoint, /(?:line-ui|cua-|line-automation)/u);
});

test('human output escapes terminal and directional controls after reader validation while JSON preserves source text', async () => {
  const maliciousText = 'before\x1b[2J\x1b[Hforged\x1b]52;c;Zml4dHVyZQ==\x07\rreplace\b\u009b31m\u202efake\u2066hidden\u2069\n第二行😀\tend';
  const sender = 'sender\x1b[31m';
  const read = scope => readLocalLineMessages(scope, { runProcess: async () => ({
    code: 0,
    stdout: JSON.stringify({
      ok: true, chatName: scope.chatName, chatRef: `chat:${'a'.repeat(24)}`,
      chatIdentity: { kind: 'group', displayName: scope.chatName, uiIdentityVerified: false }, ownSenderRef: null,
      count: 1, scope: { kind: 'local_database', requested: scope, truncated: false },
      freshness: { sourceStable: true }, warnings: [],
      messages: [{ sourceRef: `message:${'b'.repeat(24)}`, sourceMessageId: 'synthetic',
        sourceTimestamp: Date.parse('2026-09-01T10:00:00+08:00'), date: '2026-09-01', time: '10:00',
        sender, text: maliciousText }],
      pagination: { hasMore: false, nextCursor: null },
    }),
  }) });
  const args = ['messages', 'read', '--chat', 'Synthetic', '--from', '2026-09-01', '--to', '2026-09-01'];
  const human = capture();
  assert.equal(await runCli(args, { readLocalLineMessages: read }, runnerOptions(human)), 0);
  assert.doesNotMatch(human.result().stdout, /[\x00-\x08\x0b-\x1f\x7f-\x9f\u202a-\u202e\u2066-\u2069]/u);
  assert.ok(human.result().stdout.includes('sender\\u001b[31m'));
  assert.ok(human.result().stdout.includes('before\\u001b[2J'));
  assert.ok(human.result().stdout.includes('\n第二行😀\tend'));
  const json = capture();
  assert.equal(await runCli([...args, '--json'], { readLocalLineMessages: read }, runnerOptions(json)), 0);
  assert.equal(JSON.parse(json.result().stdout).data.messages[0].text, maliciousText);
  assert.equal(JSON.parse(json.result().stdout).data.messages[0].sender, sender);
  assert.equal(json.result().stderr, '');
});
