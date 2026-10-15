import test from 'node:test';
import assert from 'node:assert/strict';
import { createLineExtensions, LINE_TOOL_DESCRIPTORS } from '../src/extensions/line-extensions.mjs';
import { readLocalLineChatIdentity } from '../src/extensions/line-local-reader.mjs';
import { LineToolError } from '../src/extensions/line-runtime.mjs';

const chatRef = `chat:${'a'.repeat(24)}`;
const args = { chatName: 'Example', chatType: 'direct' };
const checkedAt = '2026-09-24T06:00:00.000Z';
const flags = { readOnly: true, guiVerified: false, sendDispatched: false };
const identity = (kind = 'direct') => ({ chatRef, chatIdentity: { kind } });
const body = result => JSON.parse(result.content[0].text);
function extension(localIdentityReader) {
  const forbidden = new Proxy({}, { get() { assert.fail('No GUI or send access is allowed.'); } });
  return createLineExtensions(forbidden, { ui: forbidden, localIdentityReader,
    localReader: async () => assert.fail('No message reader is allowed.'),
    now: () => new Date(checkedAt) });
}

test('public target check exposes only unique opaque identity and completion time, with no GUI or messages', async () => {
  for (const kind of ['direct', 'group']) {
    let calls = 0;
    const ext = extension(async scope => {
      calls++;
      assert.equal(scope.chatName, 'Example');
      assert.equal(scope.chatType, 'auto');
      assert.equal(scope.requireUniqueName, true);
      return { ...identity(kind), privateData: 'must not escape' };
    });
    const result = await ext.call('check_line_send_target', { ...args, chatType: kind, expectedChatRef: chatRef });
    assert.equal(result.isError, undefined);
    assert.deepEqual(body(result), { status: 'IDENTITY_UNIQUE', chatRef, kind, checkedAt, ...flags });
    assert.equal(calls, 1);
  }
});

test('target check reaches the identity-only reader boundary with the send uniqueness flag', async () => {
  let calls = 0;
  const ext = extension(scope => readLocalLineChatIdentity(scope, { runProcess: async request => {
    calls++;
    assert.equal(request.identityOnly, true);
    assert.equal(request.requireUniqueName, true);
    assert.equal(request.chatType, 'auto');
    return { code: 0, stdout: JSON.stringify({ ok: true, chatName: 'Example', chatRef,
      chatIdentity: { kind: 'direct', displayName: 'Example', uiIdentityVerified: false },
      count: 0, messages: [], pagination: { hasMore: false, nextCursor: null },
      scope: { kind: 'local_chat_identity', truncated: false, requested: request } }) };
  } }));
  const result = await ext.call('check_line_send_target', args);
  assert.equal(result.isError, undefined);
  assert.equal(body(result).status, 'IDENTITY_UNIQUE');
  assert.equal(calls, 1);
});

test('expected identity refusals stay blocked without disclosing candidates or reader diagnostics', async () => {
  for (const code of ['CHAT_AMBIGUOUS', 'CHAT_NOT_FOUND', 'CHAT_IDENTITY_CHANGED', 'CHAT_TYPE_MISMATCH']) {
    const result = await extension(async () => { throw new LineToolError(code, 'private diagnostic',
      { chatRef, candidates: ['private candidate'], operationMayHaveCompleted: true });
    }).call('check_line_send_target', args);
    assert.equal(result.isError, undefined);
    assert.deepEqual(body(result), { status: 'BLOCKED', code, checkedAt, ...flags });
  }
});

test('requested kind and optional opaque ref must match, with no fallback or automatic retry', async () => {
  for (const [request, resolved, code] of [
    [args, identity('group'), 'CHAT_TYPE_MISMATCH'],
    [{ ...args, expectedChatRef: `chat:${'b'.repeat(24)}` }, identity(), 'CHAT_IDENTITY_CHANGED'],
  ]) {
    let calls = 0;
    const result = await extension(async () => { calls++; return resolved; }).call('check_line_send_target', request);
    assert.deepEqual(body(result), { status: 'BLOCKED', code, checkedAt, ...flags });
    assert.equal(calls, 1);
  }
});

test('invalid target arguments are refused before all readers and UI access', async () => {
  const ext = extension(async () => assert.fail('Invalid arguments must not reach the reader.'));
  for (const request of [
    { chatName: 'Example' }, { ...args, chatType: 'auto' }, { ...args, chatName: ' Example' },
    { ...args, chatName: 'Example\n' }, { ...args, expectedChatRef: 'raw-id' },
    { ...args, message: 'send this' }, { ...args, confirmed: true }, { ...args, point: { x: 1, y: 2 } },
  ]) {
    const result = await ext.call('check_line_send_target', request);
    assert.equal(result.isError, true);
    assert.equal(body(result).code, 'LINE_INVALID_ARGUMENT');
  }
});

test('unavailable or invalid identity is an error distinct from a name collision and leaks no diagnostics', async () => {
  for (const reader of [
    async () => { throw new LineToolError('LOCAL_READER_UNAVAILABLE', 'private path', { account: 'private' }); },
    async () => { throw new Error('private unexpected error'); },
    async () => ({ ...identity(), chatRef: 'not-opaque' }),
  ]) {
    const result = await extension(reader).call('check_line_send_target', args);
    assert.equal(result.isError, true);
    assert.deepEqual(body(result), { success: false, code: 'LINE_IDENTITY_CHECK_FAILED',
      message: 'Local send-target identity could not be checked.', operationMayHaveCompleted: false, ...flags });
  }
});

test('target check is a closed readonly public MCP contract', () => {
  const descriptor = LINE_TOOL_DESCRIPTORS.find(item => item.name === 'check_line_send_target');
  assert.ok(descriptor);
  assert.equal(descriptor.annotations.readOnlyHint, true);
  assert.equal(descriptor.annotations.destructiveHint, false);
  assert.equal(descriptor.annotations.idempotentHint, true);
  assert.equal(descriptor.inputSchema.additionalProperties, false);
  assert.deepEqual(descriptor.inputSchema.required, ['chatName', 'chatType']);
});

test('each check refreshes identity instead of reusing an earlier successful preflight', async () => {
  let calls = 0;
  const ext = extension(async () => {
    calls++;
    if (calls > 1) throw new LineToolError('CHAT_AMBIGUOUS', 'collision');
    return identity();
  });
  assert.equal(body(await ext.call('check_line_send_target', args)).status, 'IDENTITY_UNIQUE');
  assert.equal(body(await ext.call('check_line_send_target', args)).code, 'CHAT_AMBIGUOUS');
  assert.equal(calls, 2);
});
