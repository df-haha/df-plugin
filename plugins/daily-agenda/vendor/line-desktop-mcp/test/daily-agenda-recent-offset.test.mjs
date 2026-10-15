import test from 'node:test';
import assert from 'node:assert/strict';
import { readLocalLineRecentChats, validateRecentScope } from '../src/extensions/line-local-reader.mjs';

const chatRef = i => 'chat:' + String(i).padStart(24, 'a');
const ownSenderRef = 'sender:' + 'b'.repeat(24);
const checkedAt = '2026-09-24T12:00:00+08:00';
const checkedMs = Date.parse(checkedAt);

function makeResult(chats, hasMore = false) {
  return {
    ok: true, days: 14, dateFrom: '2026-09-11', dateTo: '2026-09-24',
    checkedAt, ownSenderRef,
    chats, hasMore, warnings: [],
  };
}

function makeChat(index) {
  // Timestamps in +08:00, decreasing, all before checkedAt
  const ts = checkedMs - (index + 1) * 3600000;
  // Format to match the pattern: YYYY-MM-DDTHH:MM:SS+08:00
  const date = new Date(ts);
  // Compute Taipei time components
  const taipeiDate = new Date(ts + 28800000);
  const iso = taipeiDate.toISOString(); // e.g. 2026-09-24T...Z
  const lastMessageAt = iso.slice(0, 19) + '+08:00';
  return {
    chatRef: chatRef(index), chatName: `Chat ${index}`, chatType: 'group',
    lastMessageAt, lastMessageTimestamp: ts,
  };
}

// --- validateRecentScope ---

test('validateRecentScope accepts offset 0..999', () => {
  assert.deepEqual(validateRecentScope({ days: 14, offset: 0 }),
    { mode: 'recentChats', days: 14, limit: 50, offset: 0 });
  assert.deepEqual(validateRecentScope({ days: 14, offset: 999 }),
    { mode: 'recentChats', days: 14, limit: 50, offset: 999 });
});

test('validateRecentScope omits offset when not provided', () => {
  const result = validateRecentScope({ days: 14 });
  assert.deepEqual(result, { mode: 'recentChats', days: 14, limit: 50 });
  assert.ok(!('offset' in result));
});

test('validateRecentScope rejects invalid offset values', () => {
  for (const offset of [-1, 1000, 1.5, '0', null]) {
    assert.throws(() => validateRecentScope({ days: 14, offset }),
      { code: 'LINE_INVALID_ARGUMENT' });
  }
});

// --- readLocalLineRecentChats with offset ---

test('readLocalLineRecentChats passes offset through and validates result', async () => {
  const chats = [makeChat(0), makeChat(1)];
  const run = async scope => {
    assert.equal(scope.offset, 2);
    return { code: 0, stdout: JSON.stringify(makeResult(chats, false)) };
  };
  const result = await readLocalLineRecentChats({ days: 14, offset: 2, limit: 2 },
    { runProcess: run });
  assert.equal(result.chats.length, 2);
  assert.equal(result.hasMore, false);
});

test('readLocalLineRecentChats with offset validates hasMore correctly', async () => {
  const chats = [makeChat(0), makeChat(1)];
  const run = async () => ({ code: 0, stdout: JSON.stringify(makeResult(chats, true)) });
  const result = await readLocalLineRecentChats({ days: 14, offset: 0, limit: 2 },
    { runProcess: run });
  assert.equal(result.hasMore, true);
  assert.equal(result.chats.length, 2);
});

test('readLocalLineRecentChats without offset still works', async () => {
  const chats = [makeChat(0)];
  const run = async () => ({ code: 0, stdout: JSON.stringify(makeResult(chats, false)) });
  const result = await readLocalLineRecentChats({ days: 14 }, { runProcess: run });
  assert.equal(result.chats.length, 1);
  assert.equal(result.hasMore, false);
});

// --- MCP schema: offset is not exposed ---

test('list_line_recent_chats MCP schema does not include offset', async () => {
  const { LINE_TOOL_DESCRIPTORS } = await import('../src/extensions/line-extensions.mjs');
  const descriptor = LINE_TOOL_DESCRIPTORS.find(item => item.name === 'list_line_recent_chats');
  assert.ok(descriptor);
  assert.ok(!('offset' in descriptor.inputSchema.properties));
  // additionalProperties:false means MCP callers cannot pass offset
  assert.equal(descriptor.inputSchema.additionalProperties, false);
});
