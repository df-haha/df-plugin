import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { createLineExtensions, LINE_TOOL_DESCRIPTORS, validateExportPath } from '../src/extensions/line-extensions.mjs';
import { runtimeRequire } from '../src/extensions/line-runtime.mjs';

const history = '2026.09.09 星期三\n09:01 *Alice* first\n09:02 *Bob* 二行\n第二行😀\n2026.09.10 星期四\n10:00 *Alice* exact\n10:01 *Alice* not exact\n10:02 *Bob* 最後';
const body = result => JSON.parse(result.content[0].text);
const { CallToolResultSchema } = runtimeRequire()('@modelcontextprotocol/sdk/types.js');
const PNG_BYTES = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAAAEAAAABCAYAAAAfFcSJAAAADUlEQVQIHWP4z8DwHwAFgAI/ScL9mgAAAABJRU5ErkJggg==', 'base64');
const JPEG_BYTES = Buffer.from([0xff, 0xd8, 0xff, 0xc0, 0, 8, 8, 0, 1, 0, 1, 1, 0xff, 0xd9]);

function wavBytes() {
  const bytes = Buffer.alloc(46);
  bytes.write('RIFF', 0, 'ascii');
  bytes.writeUInt32LE(bytes.length - 8, 4);
  bytes.write('WAVEfmt ', 8, 'ascii');
  bytes.writeUInt32LE(16, 16);
  bytes.writeUInt16LE(1, 20); // PCM
  bytes.writeUInt16LE(1, 22);
  bytes.writeUInt32LE(8000, 24);
  bytes.writeUInt32LE(8000, 28);
  bytes.writeUInt16LE(1, 32);
  bytes.writeUInt16LE(8, 34);
  bytes.write('data', 36, 'ascii');
  bytes.writeUInt32LE(2, 40);
  bytes[44] = 0x80;
  bytes[45] = 0x80;
  return bytes;
}

function mediaPreview(bytes, mimeType, media = {}) {
  return {
    ...media,
    preview: { mimeType, data: bytes.toString('base64') },
    previewInfo: {
      mimeType, decodedBytes: bytes.length,
      decodedSha256: createHash('sha256').update(bytes).digest('hex'),
      ...(media.previewInfo || {}),
    },
  };
}

function fixture(raw = history, options = {}) {
  const calls = [];
  const automation = {
    async getChatHistory(...args) { calls.push(['history', ...args]); return raw; },
    async sendChatMessage(...args) { calls.push(['send', ...args]); return { success: true }; },
    async stageFileManual(...args) { calls.push(['file', ...args]); return { success: true }; },
  };
  const ui = {
    getStatus: async () => ({ available: true }),
    sendText: async args => { calls.push(['safe-send', args]); return {success:true}; },
    stageFile: async args => { calls.push(['safe-file', args]); return {success:true}; },
  };
  return { extension: createLineExtensions(automation, { ui, now: () => new Date('2026-09-10T09:00:00Z'), ...options }), automation, ui, calls };
}

test('tool catalogue is unique, closed-schema and exposes six compatible legacy names', () => {
  const names = LINE_TOOL_DESCRIPTORS.map(item => item.name);
  assert.equal(names.length, new Set(names).size);
  assert.equal(names.length, 38);
  for (const name of ['get_line_chatroom_history_short', 'get_line_chatroom_history_default', 'get_line_chatroom_history_long', 'send_message_manual', 'send_message_auto', 'send_file_manual']) assert.ok(names.includes(name));
  for (const item of LINE_TOOL_DESCRIPTORS) assert.equal(item.inputSchema.additionalProperties, false);
});

test('poll reader binds a private local group identity without reading history', async () => {
  const calls = [];
  const identity = { chatName: '測試群組', chatRef: 'chat:' + 'b'.repeat(24), chatIdentity: { kind: 'group' },
    scope: { kind: 'local_chat_identity' }, count: 0, messages: [] };
  const { extension } = fixture(history, {
    localReader: async () => assert.fail('No history reads'),
    localIdentityReader: async scope => { calls.push(['identity', scope]); return identity; },
    pollReader: async args => { calls.push(['poll', args]); return { state: 'draft', publicationVerified: false }; },
  });
  const result = body(await extension.call('get_line_poll_state', { chatName: identity.chatName }));
  assert.equal(result.state, 'draft');
  assert.equal(calls[0][1].chatType, 'group');
  assert.equal(calls[0][1].messageLimit, 1);
  assert.deepEqual(calls[1][1], { chatName: identity.chatName, chatRef: identity.chatRef, includeScreenshot: false });
  identity.chatIdentity.kind = 'direct';
  const refused = body(await extension.call('get_line_poll_state', { chatName: identity.chatName }));
  assert.equal(refused.code, 'LINE_POLL_CHAT_IDENTITY_UNVERIFIED');
  assert.equal(calls.filter(item => item[0] === 'poll').length, 1);
});

test('status preserves independent client evidence when the GUI backend is unavailable', async () => {
  const local = { ok: true, client: { verified: true }, process: { state: 'running' } };
  const { extension, calls } = fixture(history, { clientStatus: async () => local,
    ui: { getStatus: async () => { throw new Error('synthetic GUI failure'); } } });
  const result = body(await extension.call('get_line_status', {}));
  assert.equal(result.uiStatusUnavailable, true);
  assert.deepEqual(result.localReader, local);
  assert.deepEqual(calls, []);
});

test('quoted visual source requires fresh local identity before any UI selection', async () => {
  const source = { sourceRef: 'message:' + 'a'.repeat(24), text: 'full source\n第二行', sender: 'Alice', date: '2026-09-10', time: '10:54' };
  const calls = [];
  let row = { ...source, time: '10:54:47' };
  const ui = { getReplySourceTarget: async args => { calls.push(['ui', args]); return { replySourceTarget: { token: 'issued' } }; },
    confirmReplySourceTarget: async args => { calls.push(['confirm', args]); return { confirmed: true }; },
    messageAction: async args => { calls.push(['stage', args]); return { sent: false }; } };
  const { extension } = fixture(history, { ui, localReader: async scope => {
    calls.push(['local', scope]);
    return { chatRef: 'chat:' + 'c'.repeat(24), messages: [row], chatIdentity: { kind: 'direct' }, scope: { truncated: false }, freshness: { snapshotCapturedAt: '2026-09-10T08:00:00Z' } };
  } });
  const args = { chatName: 'Alice', chatType: 'direct', source };
  const result = body(await extension.call('get_line_reply_source_target', args));
  assert.equal(result.localSourceVerification.verified, true);
  assert.equal(result.localSourceVerification.timePrecision, 'minute');
  assert.equal(result.localSourceVerification.uiSourceRefVerified, false);
  assert.equal(calls[0][1].dateFrom, source.date);
  assert.equal(calls[0][1].dateTo, source.date);
  assert.equal(calls[0][1].chatType, 'direct');
  assert.equal(calls[0][1].mediaMode, 'metadata');
  assert.equal(calls[1][1].chatType, 'direct');
  assert.equal(calls[1][1].chatRef, 'chat:' + 'c'.repeat(24));
  assert.deepEqual(calls.map(c => c[0]), ['local', 'ui']);
  for (const change of [{sender:'Bob'}, {time:'11:54:47'}, {text:'different'}, {sourceRef:'message:'+'b'.repeat(24)}]) {
    calls.length = 0; row = { ...source, time: '10:54:47', ...change };
    const mismatch = await extension.call('get_line_reply_source_target', args);
    assert.equal(body(mismatch).code, 'LINE_REPLY_SOURCE_LOCAL_MISMATCH');
    assert.deepEqual(calls.map(c => c[0]), ['local']);
  }
  calls.length = 0;
  const missing = await extension.call('stage_line_reply', {chatName:'Alice',messageText:source.text,replyText:'test'});
  assert.equal(missing.isError, true);
  assert.deepEqual(calls, []);
  await extension.call('stage_line_reply', {chatName:'Alice',messageText:source.text,replyText:'test',source,sourceToken:'issued'});
  assert.equal(calls[0][1].sourceToken, 'issued');
  assert.deepEqual(calls[0][1].source, source);
});

test('quote source refuses visually identical minutes and truncated windows before UI', async () => {
  const source = { sourceRef: 'message:' + 'a'.repeat(24), text: 'same full text', sender: 'Alice', date: '2026-09-10', time: '10:54:47' };
  const rows = [{ ...source }];
  let truncated = false, uiCalls = 0;
  const { extension } = fixture(history, { localReader: async () => ({ messages: rows,
    chatIdentity: { kind: 'direct' }, scope: { truncated } }),
    ui: { getReplySourceTarget: async () => { uiCalls++; return {}; } },
  });
  const args = { chatName: 'Alice', source };
  const unique = body(await extension.call('get_line_reply_source_target', args));
  assert.equal(unique.localSourceVerification.localTimePrecision, 'second');
  assert.equal(unique.localSourceVerification.visualTimePrecision, 'minute');
  assert.equal(uiCalls, 1);
  rows.push({ ...source, sourceRef: 'message:' + 'b'.repeat(24), time: '10:54:58' });
  const ambiguous = body(await extension.call('get_line_reply_source_target', args));
  assert.equal(ambiguous.code, 'LINE_REPLY_SOURCE_VISUALLY_AMBIGUOUS');
  assert.equal(uiCalls, 1);
  rows.pop(); truncated = true;
  const incomplete = body(await extension.call('get_line_reply_source_target', args));
  assert.equal(incomplete.code, 'LINE_REPLY_SOURCE_LOCAL_WINDOW_TRUNCATED');
  assert.equal(uiCalls, 1);
});

test('invalid schemas, exact-chat inputs and impossible dates never reach LINE', async () => {
  const { extension, calls } = fixture();
  for (const args of [
    {}, { chatName: 'Test', messageLimit: 0 }, { chatName: 'Test', messageLimit: 1.5 },
    { chatName: 'Test', date: '2026-02-30' }, { chatName: 'Test', dateFrom: '2026-09-11', dateTo: '2026-09-10' },
    { chatName: 'Test', dangerousOption: true }, { chatName: 'Test\nOther' }, { chatName: ' Test' },
  ]) assert.equal((await extension.call('get_line_chat_messages', args)).isError, true);
  assert.deepEqual(calls, []);
});

test('dated history uses one exact local scope while undated legacy history keeps UI paging', async () => {
  const localCalls = [];
  const { extension, calls } = fixture(history, { localReader: async scope => {
    localCalls.push(scope);
    return { ok: true, chatName: scope.chatName, count: 1,
      messages: [{ sourceRef: 'message:' + 'a'.repeat(24), date: scope.dateFrom,
        sender: 'Bob', text: '二行\n第二行😀', contentType: 0 }],
      scope: { kind: 'local_database', requested: scope, totalHistoryKnown: false, truncated: false },
      pagination: { hasMore: false, nextCursor: null }, warnings: ['Local cache is bounded.'] };
  } });
  const result = body(await extension.call('get_line_chat_messages', { chatName: 'Test', date: '2026-09-09', messageLimit: 1 }));
  assert.equal(result.count, 1);
  assert.equal(result.messages[0].text, '二行\n第二行😀');
  assert.equal(result.scope.totalHistoryKnown, false);
  assert.equal(result.scope.kind, 'local_database');
  assert.deepEqual(localCalls[0], { chatName: 'Test', chatType: 'auto', dateFrom: '2026-09-09',
    dateTo: '2026-09-09', messageLimit: 1, mediaMode: 'metadata' });
  assert.deepEqual(calls, []);
  const legacy = body(await extension.call('get_line_chatroom_history_long', { chatName: 'Test', messageLimit: 2 }));
  assert.equal(legacy.count, 2);
  assert.match(legacy.history, /最後/);
  assert.doesNotMatch(legacy.history, /first/);
  assert.equal(calls[0][4], 50);
});

test('scoped history refuses incomplete or broad dates and propagates ambiguity without GUI fallback', async () => {
  const localCalls = [];
  const { extension, calls } = fixture(history, { localReader: async scope => {
    localCalls.push(scope);
    throw Object.assign(new Error('Synthetic ambiguous name'), { code: 'CHAT_AMBIGUOUS' });
  } });
  for (const args of [
    { dateFrom: '2026-09-01' }, { dateTo: '2026-09-01' },
    { date: '2026-09-01', dateFrom: '2026-09-01', dateTo: '2026-09-01' },
    { dateFrom: '2026-08-01', dateTo: '2026-09-01' },
  ]) {
    const result = await extension.call('get_line_chat_messages', { chatName: 'Test', ...args });
    assert.equal(result.isError, true);
    assert.equal(body(result).code, 'LINE_INVALID_ARGUMENT');
  }
  assert.deepEqual(localCalls, []);
  assert.deepEqual(calls, []);
  const ambiguous = await extension.call('get_line_chat_messages', {
    chatName: 'Test', dateFrom: '2026-09-01', dateTo: '2026-09-02', messageLimit: 2,
  });
  assert.equal(ambiguous.isError, true);
  assert.equal(body(ambiguous).code, 'CHAT_AMBIGUOUS');
  assert.equal(localCalls.length, 1);
  assert.deepEqual(calls, []);
});

test('scoped search retains local fields and pagination when sender filtering narrows a page', async () => {
  const localCalls = [];
  const messages = [
    { sourceRef: 'message:' + 'a'.repeat(24), date: '2026-09-23', sender: 'Alice',
      text: 'needle\n第二行', contentType: 0, sourceStatus: 2 },
    { sourceRef: 'message:' + 'b'.repeat(24), date: '2026-09-23', sender: 'Bob',
      text: 'needle', contentType: 0, sourceStatus: 2 },
  ];
  const pagination = { hasMore: true, nextCursor: 'synthetic-older-page', limitedBy: 'messageLimit' };
  const freshness = { snapshotCapturedAt: '2026-09-23T02:00:00Z' };
  const { extension, calls } = fixture(history, { localReader: async scope => {
    localCalls.push(scope);
    return { ok: true, chatName: scope.chatName, messages, count: 2, pagination, freshness,
      scope: { kind: 'local_database', requested: scope, totalHistoryKnown: false, truncated: true },
      warnings: ['Local cached records do not prove complete server history.'] };
  } });
  const result = body(await extension.call('search_line_chat_messages', { chatName: 'Test',
    date: '2026-09-23', messageLimit: 2, query: 'needle', sender: 'ALICE' }));
  assert.equal(result.count, 1);
  assert.deepEqual(result.messages, [messages[0]]);
  assert.deepEqual(result.pagination, pagination);
  assert.deepEqual(result.freshness, freshness);
  assert.equal(result.scope.kind, 'local_database');
  assert.equal(result.scope.pageCountBeforePostFilters, 2);
  assert.equal(result.scope.postFilterPageOnly, true);
  assert.equal(result.scope.truncated, true);
  assert.match(result.warnings.join(' '), /older matching rows may exist/u);
  assert.match(result.warnings.join(' '), /case-sensitive literal/u);
  assert.equal(localCalls[0].query, 'needle');
  assert.equal(localCalls[0].messageLimit, 2);
  assert.deepEqual(calls, []);
  const unsupported = await extension.call('search_line_chat_messages', { chatName: 'Test',
    date: '2026-09-23', query: 'needle', kind: 'message' });
  assert.equal(unsupported.isError, true);
  assert.equal(body(unsupported).code, 'LINE_KIND_FILTER_UNSUPPORTED');
  assert.equal(localCalls.length, 1);
});

test('scoped export and exact verification retain local freshness and disclose TXT projection', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'line-scoped-export-'));
  const messages = [{ sourceRef: 'message:' + 'c'.repeat(24), date: '2026-09-23',
    sender: 'Alice', text: '完整訊息\n第二行', contentType: 0, sourceStatus: 2 }];
  const freshness = { snapshotCapturedAt: '2026-09-23T02:00:00Z' };
  const pagination = { hasMore: false, nextCursor: null };
  const { extension, calls } = fixture(history, { localReader: async scope => ({
    ok: true, chatName: scope.chatName, messages, count: 1, freshness, pagination,
    scope: { kind: 'local_database', requested: scope, totalHistoryKnown: false, truncated: false },
    warnings: ['Local cache is bounded.'],
  }) });
  try {
    for (const format of ['json', 'txt', 'csv']) {
      const outputPath = path.join(directory, `scoped.${format}`);
      const exported = body(await extension.call('export_line_chat_history', { chatName: 'Test',
        date: '2026-09-23', messageLimit: 1, outputPath, format }));
      assert.equal(exported.verified, true);
      assert.equal(exported.scope.kind, 'local_database');
      assert.deepEqual(exported.freshness, freshness);
      assert.deepEqual(exported.pagination, pagination);
      const saved = await fs.readFile(outputPath, 'utf8');
      if (format === 'json') assert.deepEqual(JSON.parse(saved).messages, messages);
      else {
        assert.ok(exported.projection.omittedFields.includes('sourceRef'));
        assert.match(exported.projection.note, /missing kind is blank/u);
        assert.match(saved, /完整訊息/u);
      }
    }
    const verified = body(await extension.call('verify_line_message', { chatName: 'Test',
      date: '2026-09-23', message: '完整訊息\n第二行', sender: 'Alice' }));
    assert.equal(verified.found, true);
    assert.equal(verified.deliveryVerified, false);
    assert.deepEqual(verified.matches, messages);
    assert.deepEqual(verified.freshness, freshness);
    assert.deepEqual(verified.pagination, pagination);
    assert.match(verified.warnings.join(' '), /older matching rows may exist/u);
    assert.deepEqual(calls, []);
  } finally {
    assert.ok(path.resolve(directory).startsWith(path.resolve(os.tmpdir()) + path.sep));
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test('search is literal and bounded; unknown formats are not successful empty results', async () => {
  const { extension } = fixture();
  assert.equal(body(await extension.call('search_line_chat_messages', { chatName: 'Test', query: 'EXACT', messageLimit: 10 })).count, 2);
  assert.equal(body(await extension.call('search_line_chat_messages', { chatName: 'Test', query: '.*' })).count, 0);
  const bad = fixture('unknown UI content');
  const result = await bad.extension.call('get_line_chat_messages', { chatName: 'Test' });
  assert.equal(result.isError, true);
  assert.equal(body(result).code, 'HISTORY_FORMAT_UNRECOGNIZED');
  assert.equal(JSON.stringify(result).includes('unknown UI content'), false);
});

test('verification uses exact full message/sender and never claims new delivery', async () => {
  const { extension } = fixture();
  const result = body(await extension.call('verify_line_message', { chatName: 'Test', message: 'exact', sender: 'Alice' }));
  assert.equal(result.matchCount, 1);
  assert.equal(result.deliveryVerified, false);
  assert.equal(result.mentionVerified, false);
  assert.equal(body(await extension.call('verify_line_message', { chatName: 'Test', message: 'exact', sender: 'Ali' })).found, false);
});

test('chat identity refusal propagates through every UI history alias without exporting', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'line-refused-export-'));
  const outputPath = path.join(directory, 'messages.json');
  try {
    const { extension, automation } = fixture();
    let reads = 0;
    automation.getChatHistory = async () => {
      reads++;
      throw Object.assign(new Error('Synthetic selected chat changed'), { code: 'LINE_CHAT_STALE' });
    };
    const requests = [
      ...['short', 'default', 'long'].map(size => [`get_line_chatroom_history_${size}`, {}]),
      ['get_line_chat_messages', {}],
      ['search_line_chat_messages', { query: 'exact' }],
      ['verify_line_message', { message: 'exact' }],
      ['export_line_chat_history', { outputPath, format: 'json' }],
    ];
    for (const [name, args] of requests) {
      const result = await extension.call(name, { chatName: 'Test', ...args });
      assert.equal(result.isError, true, name);
      assert.equal(body(result).code, 'LINE_CHAT_STALE', name);
      assert.equal(body(result).history, undefined);
      assert.equal(body(result).messages, undefined);
      assert.equal(result.content.some(item => item.type === 'image'), false);
    }
    assert.equal(reads, requests.length, 'each request has only one history attempt');
    await assert.rejects(fs.stat(outputPath), { code: 'ENOENT' });
  } finally {
    assert.ok(path.resolve(directory).startsWith(path.resolve(os.tmpdir()) + path.sep));
    await fs.rm(directory, { recursive: true, force: true });
  }
});

test('identity refusal during optional UI comparison preserves local rows without retry', async () => {
  const messages = [{ sourceRef: 'message:' + 'a'.repeat(24), date: '2026-09-10', time: '10:00', sender: 'Alice', text: 'authorized local row', contentType: 0, media: {} }];
  const { extension, automation } = fixture(history, { localReader: async scope => ({
    ok: true, chatName: scope.chatName, count: 1, messages,
    scope: { kind: 'local_database', requested: { ...scope, messageLimit: 200 } },
  }) });
  let reads = 0;
  automation.getChatHistory = async () => {
    reads++;
    throw Object.assign(new Error('Synthetic chat mismatch'), { code: 'LINE_CHAT_STALE' });
  };
  const result = await extension.call('get_line_local_messages', {
    chatName: 'Test', dateFrom: '2026-09-10', dateTo: '2026-09-10', compareWithUi: true,
  });
  assert.notEqual(result.isError, true);
  assert.equal(body(result).count, 1);
  assert.deepEqual(body(result).messages, messages);
  assert.equal(body(result).crossCheck.status, 'unavailable');
  assert.equal(body(result).crossCheck.uiActionIdentityVerified, false);
  assert.equal(body(result).crossCheck.deliveryVerified, false);
  assert.equal(reads, 1);
});

test('send/stage distinguish dispatch, draft and delivery; failures remain MCP errors', async () => {
  const { extension, ui, calls } = fixture();
  const manual = body(await extension.call('send_message_manual', { chatName: 'Test', message: '@All literal\nnext' }));
  assert.equal(manual.staged, true);
  assert.equal(manual.sendDispatched, false);
  const auto = body(await extension.call('send_message_auto', { chatName: 'Test', message: 'approved' }));
  assert.equal(auto.sendDispatched, true);
  assert.equal(auto.deliveryVerified, false);
  assert.deepEqual(calls[0], ['safe-send', {chatName:'Test', message:'@All literal\nnext', autoSend:false}]);
  assert.equal(calls.some(call=>call[0]==='send'),false);
  ui.sendText = async () => ({ success: false, error: 'typing failed' });
  assert.equal((await extension.call('send_message_auto', { chatName: 'Test', message: 'approved' })).isError, true);
});

test('UI structured refusals and screenshots are preserved without calling legacy sends', async () => {
  const { extension, ui, calls } = fixture();
  ui.getState = async () => ({ verified: true, images: [{ type: 'image', data: 'test', mimeType: 'image/png' }] });
  const result = await extension.call('get_line_ui_state', { chatName: 'Test', includeScreenshot: true });
  assert.equal(result.content[1].type, 'image');
  ui.setDraft = async () => { const e = new Error('Draft changed'); e.code = 'LINE_DRAFT_CHANGED'; throw e; };
  const failed = await extension.call('set_line_draft', { chatName: 'Test', message: 'test' });
  assert.equal(failed.isError, true);
  assert.equal(body(failed).code, 'LINE_DRAFT_CHANGED');
  assert.deepEqual(calls, []);
});

test('exports are exclusive, reconciled by hash, and invalid paths never read chats', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'line-export-test-'));
  assert.ok(path.resolve(directory).startsWith(path.resolve(os.tmpdir()) + path.sep));
  try {
    const { extension, calls } = fixture();
    const outputPath = path.join(directory, 'messages.json');
    const exported = body(await extension.call('export_line_chat_history', { chatName: 'Test', outputPath, format: 'json', messageLimit: 2 }));
    assert.equal(exported.verified, true);
    const bytes = await fs.readFile(outputPath);
    assert.equal(exported.sha256, createHash('sha256').update(bytes).digest('hex'));
    assert.equal(JSON.parse(bytes).count, 2);
    const count = calls.length;
    assert.equal((await extension.call('export_line_chat_history', { chatName: 'Test', outputPath, format: 'json' })).isError, true);
    assert.equal(calls.length, count);
    await assert.rejects(validateExportPath('relative.json', 'json'));
    await assert.rejects(validateExportPath(path.join(directory, 'wrong.exe'), 'json'));
    if (process.platform === 'win32') {
      const junction = path.join(directory, 'junction');
      const target = path.join(directory, 'target');
      await fs.mkdir(target);
      await fs.symlink(target, junction, 'junction');
      await assert.rejects(validateExportPath(path.join(junction, 'file.json').replaceAll('\\', '/'), 'json'), /junctions or symlinks/);
      await assert.rejects(validateExportPath('\\relative-root.json', 'json'), /drive-qualified/);
      const adsPath = `${path.join(directory, 'base.txt')}:stream.json`;
      const beforeAds = calls.length;
      const ads = await extension.call('export_line_chat_history', { chatName: 'Test', outputPath: adsPath, format: 'json' });
      assert.equal(ads.isError, true);
      assert.equal(body(ads).code, 'LINE_INVALID_ARGUMENT');
      assert.equal(calls.length, beforeAds, 'ADS rejection must happen before LINE history is read');
      await assert.rejects(validateExportPath(adsPath, 'json'), /alternate data stream/);
      await fs.unlink(junction);
    }
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});

test('post-create export failures preserve the file and report uncertainty', async () => {
  const directory = await fs.mkdtemp(path.join(os.tmpdir(), 'line-export-unverified-test-'));
  try {
    const outputPath = path.join(directory, 'messages.json');
    const failingFileSystem = {
      ...fs,
      async open(...args) {
        const handle = await fs.open(...args);
        return {
          writeFile: (...writeArgs) => handle.writeFile(...writeArgs),
          sync: async () => { throw new Error('injected sync failure after file creation'); },
          close: () => handle.close(),
        };
      },
    };
    const { extension, calls } = fixture(history, { fileSystem: failingFileSystem });
    const result = await extension.call('export_line_chat_history', { chatName: 'Test', outputPath, format: 'json' });
    const failed = body(result);
    assert.equal(result.isError, true);
    assert.equal(failed.code, 'LINE_EXPORT_UNVERIFIED');
    assert.equal(failed.operationMayHaveCompleted, true);
    assert.equal(failed.outputPathCreated, true);
    assert.doesNotMatch(failed.message, /injected sync failure/);
    assert.equal(calls.filter(call => call[0] === 'history').length, 1);
    const bytes = await fs.readFile(outputPath);
    assert.ok(bytes.length > 0, 'the created file must be preserved for inspection');
  } finally { await fs.rm(directory, { recursive: true, force: true }); }
});

test('capability and visual workflow queries perform no UI work', async () => {
  const { extension, calls } = fixture();
  const capabilities = body(await extension.call('get_line_capabilities', { mode: 'guided_ui' }));
  assert.ok(capabilities.capabilities.every(item => item.mode === 'guided_ui'));
  const workflow = body(await extension.call('get_line_workflow', { workflow: 'polls' }));
  assert.equal(workflow.performedAction, false);
  assert.equal(workflow.execution, 'guidance_only');
  assert.deepEqual(calls, []);
});

test('workflow plans validate through MCP without invoking LINE or implying approval', async () => {
  const noUi = new Proxy({}, { get: () => { throw new Error('Planner accessed UI'); } });
  const { extension, calls } = fixture(history, { ui: noUi });
  const plans = [
    { workflow: 'mentions', chatName: 'Test', message: '測試', mentionTargets: ['All'] },
    { workflow: 'reply', chatName: 'Test', message: '回覆', source: { text: '原文', sender: 'Alice', date: '2026-09-09', time: '09:01' } },
    { workflow: 'polls', chatName: 'Test', poll: { question: '測試', options: ['A', 'B'], multipleChoice: false, anonymous: true, allowAddOptions: false, deadline: '2026-09-12T18:00+08:00' } },
  ];
  for (const args of plans) {
    const result = await extension.call('prepare_line_workflow', args);
    assert.notEqual(result.isError, true);
    const prepared = body(result);
    assert.equal(prepared.execution, 'preparation_only');
    assert.match(prepared.planId, /^[a-f0-9]{64}$/);
    for (const key of ['performedAction', 'sent', 'published', 'permissionVerified', 'uiVerified']) assert.equal(prepared[key], false);
  }
  for (const args of [
    { ...plans[0], poll: plans[2].poll },
    { ...plans[0], mentionTargets: ['@All'] },
    { ...plans[1], source: { ...plans[1].source, date: '2026-02-30' } },
    { ...plans[2], poll: { ...plans[2].poll, deadline: '2026-09-10T16:59+08:00' } },
    { ...plans[2], poll: { ...plans[2].poll, anonymous: 'false' } },
    { ...plans[2], poll: { ...plans[2].poll, options: ['Ａ', 'Ａ '] } },
  ]) assert.equal((await extension.call('prepare_line_workflow', args)).isError, true);
  assert.deepEqual(calls, []);
  assert.equal(body(await extension.call('get_line_workflow', { workflow: 'reply' })).performedAction, false);
});

test('local history avoids GUI by default and only cross-checks when explicitly requested', async () => {
  const localCalls = [];
  const localReader = async args => {
    localCalls.push(args);
    return { ok: true, chatName: args.chatName, retrievedAt: '2026-09-10T09:00:00Z', count: 1,
      scope: { kind: 'local_database', requested: { ...args, messageLimit: args.messageLimit ?? 200 } },
      messages: [{ sourceRef: 'message:fixture', date: '2026-09-10', time: '10:00:05', sender: 'Alice', text: 'exact', contentType: 0 }],
    };
  };
  const args = { chatName: 'Test', dateFrom: '2026-09-09', dateTo: '2026-09-10' };
  const { extension, calls } = fixture(history, { localReader });
  const local = body(await extension.call('get_line_local_messages', args));
  assert.equal(local.count, 1);
  assert.equal(local.crossCheck.status, 'not_requested');
  assert.equal(local.crossCheck.uiReadAttempted, false);
  assert.equal(localCalls[0].mediaMode, 'metadata');
  assert.deepEqual(calls, []);
  const crossed = body(await extension.call('get_line_local_messages', { ...args, compareWithUi: true }));
  assert.equal(crossed.crossCheck.status, 'completed');
  assert.equal(crossed.crossCheck.matches.length, 1);
  assert.equal(crossed.crossCheck.uiActionIdentityVerified, false);
  assert.equal(crossed.crossCheck.deliveryVerified, false);
  assert.equal(calls.length, 1);
  assert.equal(calls[0][3], 200);
  assert.equal(calls[0][4], 5);
  assert.equal(localCalls.length, 2);
  assert.equal(Object.hasOwn(localCalls[1], 'compareWithUi'), false);
  assert.equal((await extension.call('get_line_local_messages', { ...args, compareWithUi: 'true' })).isError, true);
  assert.equal(localCalls.length, 2);
});

test('local media stays opt-in through MCP and exposes validated native images only when requested', async () => {
  const ref = 'message:' + 'a'.repeat(24);
  const args = { chatName: 'Test', dateFrom: '2026-09-09', dateTo: '2026-09-09' };
  const { extension, calls } = fixture(history, { localReader: async scope => ({
    ok: true, chatName: scope.chatName, count: 1,
    scope: { kind: 'local_database', requested: scope },
    messages: [{ sourceRef: ref, media: scope.mediaMode === 'preview'
      ? mediaPreview(PNG_BYTES, 'image/png', { state: 'decoded', mediaType: 'image',
        previewInfo: { width: 1, height: 1 } })
      : { state: 'not_resolved', retrieval: 'metadata_only' } }],
  }) });
  const plain = await extension.call('get_line_local_messages', args);
  assert.equal(plain.content.length, 1);
  const preview = await extension.call('get_line_local_messages', { ...args, mediaMode: 'preview', mediaSourceRefs: [ref] });
  assert.equal(preview.content[1].type, 'image');
  assert.equal(body(preview).messages[0].media.imageContentIndex, 1);
  assert.equal(calls.length, 0);
  assert.equal((await extension.call('get_line_local_messages', { ...args, mediaSourceRefs: [ref] })).isError, true);
});

test('local media emits only verified image and WAV MCP blocks with text-indexed mixed content', async () => {
  const refs = ['a', 'b', 'c'].map(letter => `message:${letter.repeat(24)}`);
  const image = mediaPreview(PNG_BYTES, 'image/png', { state: 'decoded', mediaType: 'image',
    previewInfo: { width: 1, height: 1 } });
  const audio = mediaPreview(wavBytes(), 'audio/wav', { state: 'decoded', mediaType: 'audio', format: 'WAV',
    formatValidation: 'wave_header_and_frames', playbackUnverified: true });
  const jpeg = mediaPreview(JPEG_BYTES, 'image/jpeg', { state: 'decoded', mediaType: 'image',
    previewInfo: { width: 1, height: 1 } });
  const { extension } = fixture(history, { localReader: async scope => ({
    ok: true, chatName: scope.chatName, count: 3, scope: { kind: 'local_database', requested: scope },
    messages: refs.map((sourceRef, index) => ({ sourceRef, media: [image, audio, jpeg][index] })),
  }) });
  const result = await extension.call('get_line_local_messages', {
    chatName: 'Test', dateFrom: '2026-09-09', dateTo: '2026-09-09', mediaMode: 'preview', mediaSourceRefs: refs,
  });
  assert.deepEqual(result.content.map(item => item.type), ['text', 'image', 'audio', 'image']);
  const response = body(result);
  assert.equal(response.messages[0].media.imageContentIndex, 1);
  assert.equal(response.messages[1].media.audioContentIndex, 2);
  assert.equal(response.messages[2].media.imageContentIndex, 3);
  for (const message of response.messages) assert.equal(Object.hasOwn(message.media, 'preview'), false);
  const parsed = CallToolResultSchema.safeParse(result);
  assert.equal(parsed.success, true, parsed.success ? '' : JSON.stringify(parsed.error.issues));
});

test('local media makes rejected preview transport explicit instead of silently dropping it', async () => {
  const ref = letter => `message:${letter.repeat(24)}`;
  const mimeDataMismatch = mediaPreview(JPEG_BYTES, 'image/png', { state: 'decoded', mediaType: 'image',
    previewInfo: { width: 1, height: 1 } });
  const oversized = mediaPreview(Buffer.alloc(256 * 1024 + 1), 'image/png', { state: 'decoded', mediaType: 'image',
    previewInfo: { width: 1, height: 1 } });
  const invalidAudio = mediaPreview(wavBytes(), 'audio/wav', { state: 'decoded', mediaType: 'audio', format: 'WAV',
    formatValidation: 'signature_only', playbackUnverified: true });
  const unsupported = mediaPreview(PNG_BYTES, 'image/webp', { state: 'decoded', mediaType: 'image',
    previewInfo: { width: 1, height: 1 } });
  const noncanonical = mediaPreview(PNG_BYTES, 'image/png', { state: 'decoded', mediaType: 'image',
    previewInfo: { width: 1, height: 1 } });
  noncanonical.preview.data += '\n';
  const messages = [mimeDataMismatch, oversized, invalidAudio, unsupported, noncanonical]
    .map((media, index) => ({ sourceRef: ref(String.fromCharCode(97 + index)), media }));
  const { extension } = fixture(history, { localReader: async scope => ({
    ok: true, chatName: scope.chatName, count: messages.length, scope: { kind: 'local_database', requested: scope }, messages,
  }) });
  const result = await extension.call('get_line_local_messages', {
    chatName: 'Test', dateFrom: '2026-09-09', dateTo: '2026-09-09', mediaMode: 'preview',
    mediaSourceRefs: messages.map(message => message.sourceRef),
  });
  assert.equal(result.content.length, 1);
  const returned = body(result).messages.map(message => message.media);
  assert.deepEqual(returned.map(media => media.previewOmittedReason), [
    'preview_dimensions_invalid', 'preview_size_exceeded', 'preview_audio_not_validated',
    'preview_mime_unsupported', 'preview_data_invalid',
  ]);
  for (const media of returned) assert.equal(Object.hasOwn(media, 'preview'), false);
});

test('small original images wider than derived-preview limits retain the reader pixel contract', async () => {
  const bytes = Buffer.from('iVBORw0KGgoAAAANSUhEUgAAC7gAAAACCAIAAABHIQbHAAAAMklEQVR4nO3OAQ0AMAgDMMC/590FJE+roJ2kAAAAAAAAAADgd3MdAAAAAAAAAACAWvAA/FQDAeYlAUoAAAAASUVORK5CYII=', 'base64');
  let media = mediaPreview(bytes, 'image/png', { mediaType: 'image',
    previewInfo: { width: 3000, height: 2 } });
  const { extension } = fixture(history, { localReader: async () => ({ messages: [{ media }] }) });
  const args = { chatName: 'Test', dateFrom: '2026-09-09', dateTo: '2026-09-09', mediaMode: 'preview' };
  assert.equal((await extension.call('get_line_local_messages', args)).content[1].type, 'image');
  const impossible = Buffer.from(bytes);
  impossible.writeUInt32BE(10000, 16);
  impossible.writeUInt32BE(10000, 20);
  media = mediaPreview(impossible, 'image/png', { mediaType: 'image',
    previewInfo: { width: 10000, height: 10000 } });
  const refused = await extension.call('get_line_local_messages', args);
  assert.equal(refused.content.length, 1);
  assert.equal(body(refused).messages[0].media.previewOmittedReason, 'preview_dimensions_invalid');
});

test('failed UI comparison preserves usable local data without retry; failed local read never falls back', async () => {
  const args = { chatName: 'Test', dateFrom: '2026-09-09', dateTo: '2026-09-10', compareWithUi: true };
  const localReader = async scope => ({ ok: true, chatName: scope.chatName, count: 0, messages: [], scope: { kind: 'local_database', requested: { ...scope, messageLimit: 200 } } });
  const failedUi = fixture('ERROR: fixture history unavailable', { localReader });
  const result = await failedUi.extension.call('get_line_local_messages', args);
  assert.notEqual(result.isError, true);
  assert.equal(body(result).crossCheck.status, 'unavailable');
  assert.equal(body(result).crossCheck.code, 'HISTORY_READ_FAILED');
  assert.equal(body(result).count, 0);
  assert.equal(failedUi.calls.length, 1);
  const failedLocal = fixture(history, { localReader: async () => { throw new Error('Local fixture unavailable'); } });
  assert.equal((await failedLocal.extension.call('get_line_local_messages', args)).isError, true);
  assert.deepEqual(failedLocal.calls, []);
});
