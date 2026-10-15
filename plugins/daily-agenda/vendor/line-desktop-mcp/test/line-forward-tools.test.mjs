import test from 'node:test';
import assert from 'node:assert/strict';
import { createLineExtensions } from '../src/extensions/line-extensions.mjs';

const ref = prefix => `${prefix}:${'a'.repeat(24)}`;
const operationId = `forward_${'b'.repeat(32)}`;
const source = { chatName: 'Source', chatType: 'direct', chatRef: ref('chat'),
  sourceRef: ref('message'), date: '2026-09-24', contentType: 14 };
const prepare = { accountRef: ref('sender'), source,
  recipient: { chatName: 'Recipient', chatType: 'group', chatRef: `chat:${'c'.repeat(24)}` },
  idempotencyKey: 'fixture-job' };
const confirmation = { operationId, preparationId: 'token', reviewDigest: 'd'.repeat(64), confirmed: true };

test('forward tools route exact immutable contract and preserve uncertain evidence', async () => {
  const calls = [];
  const forwardTransaction = Object.fromEntries(['prepare', 'confirm', 'verify', 'cancel'].map(method =>
    [method, async args => { calls.push([method, args]); return { status: 'UNCERTAIN',
      operationId, sendDispatched: 'uncertain', localRecordVerified: false, deliveryVerified: false }; }]));
  const extension = createLineExtensions({}, { ui: {}, forwardTransaction });
  for (const [method, args] of [['prepare', prepare], ['confirm', confirmation],
    ['verify', { operationId }], ['cancel', { operationId }]]) {
    const result = await extension.call(`${method}_line_forward`, args);
    assert.equal(result.isError, undefined);
    const body = JSON.parse(result.content[0].text);
    assert.equal(body.status, 'UNCERTAIN');
    assert.equal(body.localRecordVerified, false);
    assert.equal(body.deliveryVerified, false);
    assert.deepEqual(calls.at(-1), [method, args]);
  }
});

test('forward schemas reject arbitrary UI coordinates, changed confirm target and missing approval before effects', async () => {
  let calls = 0;
  const action = async () => { calls++; return {}; };
  const extension = createLineExtensions({}, { ui: {}, forwardTransaction: {
    prepare: action, confirm: action, verify: action, cancel: action } });
  for (const [tool, args] of [
    ['prepare', { ...prepare, point: { x: 1, y: 1 } }],
    ['prepare', { ...prepare, source: { ...source, sourceRef: 'raw-id' } }],
    ['prepare', { ...prepare, source: { ...source, text: '' } }],
    ['confirm', { ...confirmation, confirmed: false }],
    ['confirm', { ...confirmation, recipient: prepare.recipient }],
    ['verify', { operationId: '../other' }],
  ]) assert.equal((await extension.call(`${tool}_line_forward`, args)).isError, true);
  assert.equal(calls, 0);
});
