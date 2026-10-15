import test from 'node:test';
import assert from 'node:assert/strict';
import { readLocalLineRecentChats, validateRecentScope, validateLocalScope } from '../src/extensions/line-local-reader.mjs';
import { createLineExtensions, LINE_TOOL_DESCRIPTORS } from '../src/extensions/line-extensions.mjs';

const chatRef = 'chat:' + 'a'.repeat(24);
const ownSenderRef = 'sender:' + 'b'.repeat(24);
const timestamp = Date.parse('2026-09-24T09:00:00+08:00');
const result = {
  ok: true, days: 14, dateFrom: '2026-09-11', dateTo: '2026-09-24',
  checkedAt: '2026-09-24T12:00:00+08:00', ownSenderRef,
  chats: [{ chatRef, chatName: '同名', chatType: 'direct',
    lastMessageAt: '2026-09-24T09:00:00+08:00', lastMessageTimestamp: timestamp }],
  hasMore: false, warnings: [],
};
const run = data => async () => ({ code: 0, stdout: JSON.stringify(data) });

test('recent mode has a closed public schema and cannot carry message flags', async () => {
  const descriptor = LINE_TOOL_DESCRIPTORS.find(item => item.name === 'list_line_recent_chats');
  assert.deepEqual(Object.keys(descriptor.inputSchema.properties).sort(), ['days', 'limit', 'query']);
  assert.equal(descriptor.annotations.readOnlyHint, true);
  assert.deepEqual(validateRecentScope({ days: 14 }), { mode: 'recentChats', days: 14, limit: 50 });
  for (const args of [{ days: 7 }, { days: 14, chatName: 'private' },
    { days: 14, mediaMode: 'preview' }, { days: 14, identityOnly: true },
    { days: 14, query: 'x'.repeat(101) }, { days: 14, limit: 51 }]) {
    let called = false;
    await assert.rejects(readLocalLineRecentChats(args, { runProcess: async () => { called = true; } }),
      { code: 'LINE_INVALID_ARGUMENT' });
    assert.equal(called, false);
  }
});

test('recent result verifies account, window, names and projection', async () => {
  const actual = await readLocalLineRecentChats({ days: 14 }, { runProcess: run({ ...result,
    freshness: { private: true }, readerTiming: { queryMs: 1 } }) });
  assert.deepEqual(actual, result);
  for (const changed of [
    { chats: [{ ...result.chats[0], text: 'secret' }] },
    { chats: [{ ...result.chats[0], lastMessageTimestamp: timestamp + 1 }] },
    { chats: [result.chats[0], result.chats[0]] },
    { dateFrom: '2026-09-10' }, { ownSenderRef: 'raw-id' },
    { hasMore: true }, { warnings: ['SECRET TEXT'] },
  ]) await assert.rejects(readLocalLineRecentChats({ days: 14 },
    { runProcess: run({ ...result, ...changed }) }), { code: 'LOCAL_READER_SCOPE_MISMATCH' });
  const withConflictWarning = await readLocalLineRecentChats({ days: 14 }, { runProcess: run({
    ...result, warnings: ['1 recent chat identities had conflicting records and were excluded.'],
  }) });
  assert.equal(withConflictWarning.warnings.length, 1);
});

test('extension exposes recent picker and bound named-read fields', async () => {
  const local = LINE_TOOL_DESCRIPTORS.find(item => item.name === 'get_line_local_messages');
  const send = LINE_TOOL_DESCRIPTORS.find(item => item.name === 'send_message_auto');
  assert.equal(local.inputSchema.properties.expectedChatRef.pattern, '^chat:[0-9a-f]{24}$');
  assert.equal(local.inputSchema.properties.expectedOwnSenderRef.pattern, '^sender:[0-9a-f]{24}$');
  assert.equal(send.inputSchema.properties.expectedChatRef.pattern, '^chat:[0-9a-f]{24}$');
  assert.equal(send.inputSchema.properties.expectedOwnSenderRef.pattern, '^sender:[0-9a-f]{24}$');
  assert.equal(validateLocalScope({ chatName: '同名', dateFrom: '2026-09-24', dateTo: '2026-09-24',
    expectedChatRef: chatRef, expectedOwnSenderRef: ownSenderRef }).expectedOwnSenderRef, ownSenderRef);
  for (const bad of [{ expectedChatRef: 'raw' }, { expectedOwnSenderRef: 'raw' }]) {
    assert.throws(() => validateLocalScope({ chatName: '同名', dateFrom: '2026-09-24',
      dateTo: '2026-09-24', ...bad }), { code: 'LINE_INVALID_ARGUMENT' });
  }
  let received;
  const extension = createLineExtensions({}, { ui: {}, recentReader: async args => {
    received = args;
    return result;
  } });
  const call = await extension.call('list_line_recent_chats', { days: 14, limit: 1 });
  assert.equal(call.isError, undefined);
  assert.deepEqual(received, { days: 14, limit: 1 });
  assert.deepEqual(JSON.parse(call.content[0].text), result);
  const bad = await extension.call('list_line_recent_chats', { days: 14, mediaMode: 'preview' });
  assert.equal(bad.isError, true);
});
