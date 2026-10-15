import assert from 'node:assert/strict';
import { mkdtemp, readFile, readdir, rename, rm, writeFile } from 'node:fs/promises';
import { tmpdir } from 'node:os';
import { join } from 'node:path';
import test from 'node:test';

import { createForwardTransaction } from '../src/extensions/line-forward-transaction.mjs';

const SOURCE_CHAT = `chat:${'a'.repeat(24)}`;
const TARGET_CHAT = `chat:${'b'.repeat(24)}`;
const SELF = `sender:${'c'.repeat(24)}`;
const SOURCE_REF = `message:${'d'.repeat(24)}`;
const SENT_REF = `message:${'e'.repeat(24)}`;
const initialTime = Date.parse('2026-09-24T04:00:00Z');
const source = () => ({ chatName: '來源', chatType: 'direct', chatRef: SOURCE_CHAT,
  sourceRef: SOURCE_REF, date: '2026-09-24', contentType: 0 });
const recipient = () => ({ chatName: '收件', chatType: 'group', chatRef: TARGET_CHAT });
const args = (idempotencyKey = 'panel-1') => ({ accountRef: SELF, source: source(),
  recipient: recipient(), idempotencyKey });

function binding() {
  return { accountRef: SELF, ownSenderRef: SELF,
    source: { ...source(), sender: '原作者', senderRef: `sender:${'f'.repeat(24)}`,
      time: '12:00:00', sourceTimestamp: initialTime - 30_000, text: 'exact text',
      attachment: null, digest: '1'.repeat(64) }, recipient: recipient() };
}

function page(time, messages = [], changes = {}) {
  return { ok: true, chatName: '收件', chatRef: TARGET_CHAT,
    chatIdentity: { kind: 'group' }, ownSenderRef: SELF, messages,
    scope: { kind: 'local_database', truncated: false },
    pagination: { hasMore: false, nextCursor: null },
    freshness: { snapshotCapturedAt: new Date(time - 10).toISOString(), clockOrderValid: true },
    ...changes };
}

async function fixture(t, options = {}) {
  const parent = await mkdtemp(join(tmpdir(), 'line-forward-transaction-'));
  t.after(async () => rm(parent, { recursive: true, force: true }));
  const state = { time: initialTime, currentBinding: binding(), sent: [], clicks: 0,
    prepares: 0, ready: 0, scopes: [], token: 0 };
  if (options.attachment) {
    state.currentBinding.source = { ...state.currentBinding.source, contentType: 14,
      text: null, attachment: { fileName: 'sample.txt', declaredFileBytes: 68,
        fileSuffix: '.txt' } };
  }
  const storeRoot = join(parent, 'journal');
  const ui = {
    async prepare() {
      state.prepares += 1;
      if (options.notReady) return { stage: 'PREPARE_NOT_READY',
        reason: 'LINE_FORWARD_SOURCE_UI_UNVERIFIED', currentStage: 'SOURCE_SELECTION_REQUIRED',
        evidence: { sendDispatched: false } };
      return { stage: 'PREPARED', session: { id: `session-${state.prepares}`,
        expiresAt: state.time + 120_000 }, evidence: { selectedCount: 1 } };
    },
    async assertReady() { state.ready += 1; },
    async dispatch(_binding, _session, beforeDispatch) {
      if (options.beforeIntent) await options.beforeIntent({ state, storeRoot, parent });
      if (options.skipCallback) { state.clicks += 1; return { dispatched: true }; }
      await beforeDispatch();
      state.clicks += 1;
      state.time += 1_000;
      if (options.crashAfterIntent) throw new Error('CUA timeout after click');
      if (!options.noReceipt) state.sent.push({ sourceRef: state.clicks === 1
        ? SENT_REF : `message:${String(state.clicks).repeat(24)}`,
        senderRef: SELF, sourceTimestamp: state.time,
        contentType: options.attachment ? 14 : 0,
        text: options.attachment ? null : 'exact text',
        ...(options.attachment ? { media: { fileName: 'sample.txt',
          declaredFileBytes: 68, fileNameShape: { suffix: '.txt' } } } : {}) });
      return { dispatched: true };
    },
    async cancel() { return { closed: options.cancelClosed === true }; },
  };
  const deps = { ui, storeRoot, now: () => state.time,
    randomToken: () => (++state.token).toString(16).padStart(32, '0'),
    runOperation: async (_kind, callback) => callback(),
    resolveBinding: async () => structuredClone(state.currentBinding),
    readMessages: async scope => { state.scopes.push(scope);
      return page(state.time, state.sent); } };
  return { state, storeRoot, parent, deps,
    transaction: () => createForwardTransaction(deps) };
}

test('prepare requires a review and one confirmed dispatch yields a local receipt', async t => {
  const f = await fixture(t);
  const transaction = f.transaction();
  const prepared = await transaction.prepare(args());
  assert.equal(prepared.status, 'PREPARED');
  assert.match(prepared.operationId, /^forward_[0-9a-f]{32}$/);
  assert.match(prepared.reviewDigest, /^[0-9a-f]{64}$/);
  assert.equal(prepared.review.source.text, 'exact text');
  assert.equal(prepared.sendDispatched, false);
  assert.equal(f.state.clicks, 0);
  const done = await transaction.confirm({ operationId: prepared.operationId,
    preparationId: prepared.preparationId, reviewDigest: prepared.reviewDigest,
    confirmed: true });
  assert.equal(done.status, 'RECORDED_LOCAL');
  assert.equal(done.sendDispatched, true);
  assert.equal(done.localRecordVerified, true);
  assert.equal(done.deliveryVerified, false);
  assert.equal(done.receipt.sourceRef, SENT_REF);
  assert.equal(done.receipt.matchStrength, 'exact_text_local_record');
  assert.equal(f.state.clicks, 1);
  assert.equal((await transaction.verify({ operationId: prepared.operationId })).status, 'RECORDED_LOCAL');
  assert.equal((await transaction.prepare(args())).status, 'RECORDED_LOCAL');
  assert.equal(f.state.clicks, 1);
  const files = (await readdir(f.storeRoot)).filter(name => name.endsWith('.json'));
  assert.equal(files.length, 1);
  const journal = await readFile(join(f.storeRoot, files[0]), 'utf8');
  assert.equal(journal.includes('exact text'), false);
  assert.equal(journal.includes('panel-1'), false);
});

test('not-ready UI evidence never exposes a confirmation token or dispatches', async t => {
  const f = await fixture(t, { notReady: true });
  const transaction = f.transaction();
  const result = await transaction.prepare(args());
  assert.equal(result.status, 'PREPARE_NOT_READY');
  assert.equal(result.reason, 'LINE_FORWARD_SOURCE_UI_UNVERIFIED');
  assert.equal(result.preparationId, undefined);
  assert.equal(result.sendDispatched, false);
  await assert.rejects(transaction.confirm({ operationId: result.operationId,
    preparationId: 'x', reviewDigest: 'x', confirmed: true }),
  { code: 'LINE_FORWARD_NOT_PREPARED' });
  assert.equal(f.state.clicks, 0);
});

test('attachment receipt uses preview metadata and labels byte identity unverified', async t => {
  const f = await fixture(t, { attachment: true });
  const transaction = f.transaction();
  const prepared = await transaction.prepare({ ...args(), source: { ...source(), contentType: 14 } });
  const done = await transaction.confirm({ operationId: prepared.operationId,
    preparationId: prepared.preparationId, reviewDigest: prepared.reviewDigest,
    confirmed: true });
  assert.equal(done.status, 'RECORDED_LOCAL');
  assert.equal(done.receipt.matchStrength, 'attachment_metadata_local_record');
  assert.equal(done.receipt.attachment.fileName, 'sample.txt');
  assert.equal(done.receipt.limits.attachmentByteIdentityVerified, false);
  assert.ok(f.state.scopes.some(scope => scope.mediaMode === 'preview'));
});

test('confirmation rejects absent intent, wrong token, key conflict and all binding drift', async t => {
  const f = await fixture(t);
  const transaction = f.transaction();
  const prepared = await transaction.prepare(args());
  const good = { operationId: prepared.operationId,
    preparationId: prepared.preparationId, reviewDigest: prepared.reviewDigest,
    confirmed: true };
  await assert.rejects(transaction.confirm({ ...good, confirmed: false }),
    { code: 'LINE_FORWARD_CONFIRMATION_REQUIRED' });
  await assert.rejects(transaction.confirm({ ...good, preparationId: 'wrong' }),
    { code: 'LINE_FORWARD_PREPARATION_NOT_ACTIVE' });
  await assert.rejects(transaction.confirm({ ...good, reviewDigest: '0'.repeat(64) }),
    { code: 'LINE_FORWARD_PREPARATION_NOT_ACTIVE' });
  await assert.rejects(transaction.prepare({ ...args(), recipient: { ...recipient(),
    chatName: '其他群' } }), { code: 'LINE_FORWARD_KEY_CONFLICT' });
  const original = f.state.currentBinding;
  for (const changed of [
    { accountRef: `sender:${'9'.repeat(24)}`, ownSenderRef: `sender:${'9'.repeat(24)}` },
    { source: { ...original.source, digest: '2'.repeat(64) } },
    { recipient: { ...original.recipient, chatName: '其他群' } },
  ]) {
    f.state.currentBinding = { ...original, ...changed };
    await assert.rejects(transaction.confirm(good), error =>
      ['LINE_FORWARD_BINDING_INVALID', 'LINE_FORWARD_SOURCE_CHANGED'].includes(error?.code));
    assert.equal(f.state.clicks, 0);
  }
  f.state.currentBinding = original;
});

test('expiry and restart revoke process-local confirmation, same key can reprepare', async t => {
  const f = await fixture(t);
  const first = f.transaction();
  const prepared = await first.prepare(args());
  const restarted = f.transaction();
  const confirm = { operationId: prepared.operationId, preparationId: prepared.preparationId,
    reviewDigest: prepared.reviewDigest, confirmed: true };
  await assert.rejects(restarted.confirm(confirm), { code: 'LINE_FORWARD_PREPARATION_NOT_ACTIVE' });
  const second = await restarted.prepare(args());
  assert.equal(second.operationId, prepared.operationId);
  assert.notEqual(second.preparationId, prepared.preparationId);
  await assert.rejects(first.confirm(confirm), { code: 'LINE_FORWARD_PREPARATION_NOT_ACTIVE' });
  f.state.time += 121_000;
  assert.equal((await restarted.confirm({ ...confirm,
    preparationId: second.preparationId })).status, 'EXPIRED');
  assert.equal(f.state.clicks, 0);
  const third = await restarted.prepare(args());
  assert.equal(third.operationId, prepared.operationId);
  assert.equal(third.status, 'PREPARED');
});

test('journal failure before UI input and before final click is fail closed', async t => {
  const f = await fixture(t);
  await writeFile(f.storeRoot, 'not a directory');
  await assert.rejects(f.transaction().prepare(args()), { code: 'LINE_FORWARD_STORE_UNSAFE' });
  assert.equal(f.state.prepares, 0);
  await rm(f.storeRoot);
  const g = await fixture(t, { beforeIntent: async ({ storeRoot, parent }) => {
    await rename(storeRoot, join(parent, 'journal-moved'));
    await writeFile(storeRoot, 'not a directory');
  } });
  const transaction = g.transaction();
  const prepared = await transaction.prepare(args());
  await assert.rejects(transaction.confirm({ operationId: prepared.operationId,
    preparationId: prepared.preparationId, reviewDigest: prepared.reviewDigest,
    confirmed: true }), { code: 'LINE_FORWARD_STORE_UNSAFE' });
  assert.equal(g.state.clicks, 0);
});

test('invalid persisted record is rejected before a new UI action', async t => {
  const f = await fixture(t);
  const transaction = f.transaction();
  await transaction.prepare(args());
  const path = join(f.storeRoot, (await readdir(f.storeRoot))
    .find(name => name.endsWith('.json')));
  const record = JSON.parse(await readFile(path, 'utf8'));
  record.source.chatRef = '../other-directory';
  await writeFile(path, JSON.stringify(record));
  await assert.rejects(f.transaction().prepare(args()),
    { code: 'LINE_FORWARD_JOURNAL_INVALID' });
  assert.equal(f.state.prepares, 1);
  assert.equal(f.state.clicks, 0);
});

test('timeout after durable intent and absent receipt remain uncertain without retry', async t => {
  const f = await fixture(t, { crashAfterIntent: true });
  const transaction = f.transaction();
  const prepared = await transaction.prepare(args());
  const confirm = { operationId: prepared.operationId, preparationId: prepared.preparationId,
    reviewDigest: prepared.reviewDigest, confirmed: true };
  const first = await transaction.confirm(confirm);
  assert.equal(first.status, 'UNCERTAIN');
  assert.equal(first.sendDispatched, 'uncertain');
  assert.equal(f.state.clicks, 1);
  const restarted = f.transaction();
  assert.equal((await restarted.confirm(confirm)).status, 'UNCERTAIN');
  assert.equal((await restarted.verify({ operationId: prepared.operationId })).status, 'UNCERTAIN');
  assert.equal(f.state.clicks, 1);
  await assert.rejects(restarted.prepare(args('new-key')),
    { code: 'LINE_FORWARD_ALREADY_DISPATCHED' });
});

test('successful click with no unique receipt stays uncertain and cancel cannot reset it', async t => {
  const f = await fixture(t, { noReceipt: true });
  const transaction = f.transaction();
  const prepared = await transaction.prepare(args());
  const result = await transaction.confirm({ operationId: prepared.operationId,
    preparationId: prepared.preparationId, reviewDigest: prepared.reviewDigest,
    confirmed: true });
  assert.equal(result.status, 'UNCERTAIN');
  assert.equal(f.state.clicks, 1);
  await assert.rejects(transaction.cancel({ operationId: prepared.operationId }),
    { code: 'LINE_FORWARD_ALREADY_DISPATCHED' });
  assert.equal(f.state.clicks, 1);
});

test('late same-content send cannot replace a missing dispatch-time receipt', async t => {
  const f = await fixture(t, { noReceipt: true });
  const transaction = f.transaction();
  const prepared = await transaction.prepare(args());
  assert.equal((await transaction.confirm({ operationId: prepared.operationId,
    preparationId: prepared.preparationId, reviewDigest: prepared.reviewDigest,
    confirmed: true })).status, 'UNCERTAIN');
  f.state.time += 2 * 86_400_000;
  f.state.sent = [{ sourceRef: SENT_REF, senderRef: SELF,
    sourceTimestamp: f.state.time, contentType: 0, text: 'exact text' }];
  assert.equal((await f.transaction().verify({ operationId: prepared.operationId })).status,
    'UNCERTAIN');
  f.state.sent.push({ sourceRef: `message:${'9'.repeat(24)}`, senderRef: SELF,
    sourceTimestamp: initialTime + 1_000, contentType: 0, text: 'exact text' });
  assert.equal((await f.transaction().verify({ operationId: prepared.operationId })).status,
    'RECORDED_LOCAL');
});

test('verification past the supported date range stays uncertain without a read or retry', async t => {
  const f = await fixture(t, { noReceipt: true });
  const transaction = f.transaction();
  const prepared = await transaction.prepare(args());
  assert.equal((await transaction.confirm({ operationId: prepared.operationId,
    preparationId: prepared.preparationId, reviewDigest: prepared.reviewDigest,
    confirmed: true })).status, 'UNCERTAIN');
  const readCount = f.state.scopes.length;
  f.state.time += 32 * 86_400_000;
  const result = await f.transaction().verify({ operationId: prepared.operationId });
  assert.equal(result.status, 'UNCERTAIN');
  assert.equal(result.reason, 'LINE_FORWARD_VERIFY_RANGE_EXPIRED');
  assert.equal(f.state.scopes.length, readCount);
  assert.equal(f.state.clicks, 1);
});

test('two prepared keys for one original cannot both dispatch', async t => {
  const f = await fixture(t);
  const transaction = f.transaction();
  const first = await transaction.prepare(args('first'));
  const second = await transaction.prepare(args('second'));
  const approved = prepared => ({ operationId: prepared.operationId,
    preparationId: prepared.preparationId, reviewDigest: prepared.reviewDigest,
    confirmed: true });
  assert.equal((await transaction.confirm(approved(first))).status, 'RECORDED_LOCAL');
  await assert.rejects(transaction.confirm(approved(second)),
    { code: 'LINE_FORWARD_ALREADY_DISPATCHED' });
  assert.equal(f.state.clicks, 1);
  await assert.rejects(transaction.prepare(args('same-millisecond')),
    { code: 'LINE_FORWARD_ALREADY_DISPATCHED' });
  f.state.time += 1;
  const third = await transaction.prepare(args('third'));
  assert.notEqual(third.operationId, first.operationId);
  assert.equal((await transaction.confirm(approved(third))).status, 'RECORDED_LOCAL');
  assert.equal(f.state.clicks, 2);
  await assert.rejects(transaction.confirm(approved(second)),
    { code: 'LINE_FORWARD_ALREADY_DISPATCHED' });
  assert.equal((await transaction.confirm(approved(first))).status, 'RECORDED_LOCAL');
  assert.equal(f.state.clicks, 2);
});

test('an adapter that returns without dispatch callback is a durable protocol failure', async t => {
  const f = await fixture(t, { skipCallback: true });
  const transaction = f.transaction();
  const prepared = await transaction.prepare(args());
  const approved = { operationId: prepared.operationId,
    preparationId: prepared.preparationId, reviewDigest: prepared.reviewDigest,
    confirmed: true };
  await assert.rejects(transaction.confirm(approved),
    { code: 'LINE_FORWARD_DISPATCH_PROTOCOL_VIOLATION' });
  assert.equal(f.state.clicks, 1);
  assert.equal((await f.transaction().verify({ operationId: prepared.operationId })).status,
    'UNCERTAIN');
  assert.equal(f.state.clicks, 1);
});

test('cancel durably invalidates a prepared forward and reports GUI uncertainty', async t => {
  const f = await fixture(t);
  const transaction = f.transaction();
  const prepared = await transaction.prepare(args());
  const cancelled = await transaction.cancel({ operationId: prepared.operationId });
  assert.equal(cancelled.status, 'CANCELLED');
  assert.equal(cancelled.sendDispatched, false);
  assert.equal(cancelled.guiMayRemainOpen, true);
  await assert.rejects(transaction.confirm({ operationId: prepared.operationId,
    preparationId: prepared.preparationId, reviewDigest: prepared.reviewDigest,
    confirmed: true }), { code: 'LINE_FORWARD_NOT_PREPARED' });
  assert.equal((await f.transaction().prepare(args())).status, 'CANCELLED');
  assert.equal(f.state.clicks, 0);
});

test('a safely closed selector remains recorded on repeated cancellation', async t => {
  const f = await fixture(t, { cancelClosed: true });
  const transaction = f.transaction();
  const prepared = await transaction.prepare(args());
  assert.equal((await transaction.cancel({ operationId: prepared.operationId })).guiMayRemainOpen,
    false);
  assert.equal((await f.transaction().cancel({ operationId: prepared.operationId })).guiMayRemainOpen,
    false);
});
