import assert from 'node:assert/strict';
import test from 'node:test';
import { resolveForwardBinding, matchForwardReceipt } from '../src/extensions/line-forward-binding.mjs';

const NOW = Date.parse('2026-09-24T04:00:00Z');
const SOURCE_CHAT = `chat:${'a'.repeat(24)}`;
const TARGET_CHAT = `chat:${'b'.repeat(24)}`;
const SELF = `sender:${'c'.repeat(24)}`;
const OTHER = `sender:${'d'.repeat(24)}`;
const SOURCE_REF = `message:${'e'.repeat(24)}`;
const NEW_REF = `message:${'f'.repeat(24)}`;
const at = millis => new Date(millis).toISOString();
const sourceInput = (contentType = 0) => ({ chatName: '來源群組', chatType: 'group', chatRef: SOURCE_CHAT,
  sourceRef: SOURCE_REF, date: '2026-09-23', contentType });
const recipientInput = () => ({ chatName: '收件好友', chatType: 'direct', chatRef: TARGET_CHAT });
const sourceMessage = (changes = {}) => ({ sourceRef: SOURCE_REF, sourceMessageId: 'source-id',
  senderRef: OTHER, sender: '原發送者', date: '2026-09-23', time: '11:24:30',
  sourceTimestamp: Date.parse('2026-09-23T03:24:30Z'), contentType: 0, text: 'exact source',
  media: { state: 'not_applicable' }, ...changes });
const local = (chatRef, chatName, chatType, messages, changes = {}) => ({ ok: true, chatRef, chatName,
  chatIdentity: { kind: chatType, displayName: chatName }, ownSenderRef: SELF,
  messages, scope: { kind: 'local_database', truncated: false },
  pagination: { hasMore: false, nextCursor: null },
  freshness: { snapshotCapturedAt: at(NOW - 100), clockOrderValid: true }, ...changes });
const sourcePage = (messages = [sourceMessage()], changes = {}) =>
  local(SOURCE_CHAT, '來源群組', 'group', messages, changes);
const recipientPage = (messages = [], changes = {}) =>
  local(TARGET_CHAT, '收件好友', 'direct', messages, changes);
const bind = (source = sourceInput(), sourceResult = sourcePage(), recipientResult = recipientPage(),
  expectedSourceDateTo = '2026-09-24') =>
  resolveForwardBinding({ source, recipient: recipientInput() }, {
    readMessages: async scope => { assert.equal(scope.dateFrom, source.date);
      assert.equal(scope.dateTo, expectedSourceDateTo); assert.equal(scope.messageLimit, 1000);
      assert.deepEqual(scope.mediaSourceRefs, [SOURCE_REF]); return sourceResult; },
    readIdentity: async scope => { assert.equal(scope.chatName, '收件好友');
      assert.equal(scope.messageLimit, 1); return recipientResult; }, now: () => NOW,
  });

test('binding returns one exact server-derived source and recipient identity', async () => {
  const result = await bind();
  assert.equal(result.source.sourceRef, SOURCE_REF);
  assert.equal(result.source.text, 'exact source');
  assert.equal(result.source.sender, '原發送者');
  assert.equal(result.source.contentType, 0);
  assert.match(result.source.digest, /^[0-9a-f]{64}$/u);
  assert.equal(result.recipient.chatRef, TARGET_CHAT);
  assert.equal(result.ownSenderRef, SELF);
  assert.equal(result.accountRef, SELF);
  assert.equal(result.scope.localSourceRefIsUiProof, false);
  assert.equal(Object.hasOwn(result, 'messages'), false);
});

test('latest source anchors are bounded and do not alter the bound source digest', async () => {
  const baseline = await bind();
  const newer = [
    sourceMessage({ sourceRef: `message:${'1'.repeat(24)}`, senderRef: SELF,
      sender: '*李培正 (Benson Lee)*', date: '2026-09-24', time: '11:55:00',
      sourceTimestamp: Date.parse('2026-09-24T03:55:00Z'), text: 'newer one' }),
    sourceMessage({ sourceRef: `message:${'2'.repeat(24)}`, senderRef: SELF,
      sender: '*李培正 (Benson Lee)*', date: '2026-09-24', time: '11:59:00',
      sourceTimestamp: Date.parse('2026-09-24T03:59:00Z'), text: 'newer two' }),
  ];
  const anchored = await bind(sourceInput(), sourcePage([sourceMessage(), ...newer]));
  assert.equal(anchored.uiAnchors.latestSourceWindowComplete, true);
  assert.deepEqual(anchored.uiAnchors.messages.map(message => message.sourceRef),
    [SOURCE_REF, ...newer.map(message => message.sourceRef)]);
  assert.equal(anchored.uiAnchors.messages[2].senderRef, SELF);
  assert.equal(anchored.source.digest, baseline.source.digest);
  assert.equal(anchored.source.sender, '原發送者');
});

test('a source older than 30 days never claims a latest UI window', async () => {
  const oldDate = '2026-08-23';
  const oldSource = { ...sourceInput(), date: oldDate };
  const oldMessage = sourceMessage({ date: oldDate,
    sourceTimestamp: Date.parse('2026-08-23T03:24:30Z') });
  const binding = await bind(oldSource, sourcePage([oldMessage]), recipientPage(), oldDate);
  assert.equal(binding.scope.source.dateFrom, oldDate);
  assert.equal(binding.scope.source.dateTo, oldDate);
  assert.equal(binding.uiAnchors.latestSourceWindowComplete, false);
  await assert.rejects(bind(oldSource, sourcePage([oldMessage], {
    scope: { kind: 'local_database', truncated: true }, pagination: { hasMore: true },
  }), recipientPage(), oldDate), { code: 'LINE_FORWARD_LOCAL_UNVERIFIED' });
});

test('binding rejects chat, account, date, content type and freshness drift', async () => {
  await assert.rejects(bind(sourceInput(), sourcePage([], { chatRef: TARGET_CHAT })),
    { code: 'LINE_FORWARD_LOCAL_UNVERIFIED' });
  await assert.rejects(bind(sourceInput(), sourcePage(), recipientPage([], { ownSenderRef: OTHER })),
    { code: 'LINE_FORWARD_ACCOUNT_CHANGED' });
  await assert.rejects(bind({ ...sourceInput(), date: '2026-09-22' }),
    { code: 'LINE_FORWARD_SOURCE_CHANGED' });
  await assert.rejects(bind({ ...sourceInput(), contentType: 1 }),
    { code: 'LINE_FORWARD_SOURCE_CHANGED' });
  await assert.rejects(bind(sourceInput(), sourcePage([sourceMessage()],
    { freshness: { snapshotCapturedAt: at(NOW - 120_000), clockOrderValid: true } })),
    { code: 'LINE_FORWARD_LOCAL_UNVERIFIED' });
  await assert.rejects(bind(sourceInput(), sourcePage([sourceMessage()],
    { scope: { kind: 'local_database', truncated: true }, pagination: { hasMore: true } })),
    { code: 'LINE_FORWARD_LOCAL_UNVERIFIED' });
  const recipient = await bind(sourceInput(), sourcePage(), recipientPage([],
    { scope: { kind: 'local_database', truncated: true }, pagination: { hasMore: true } }));
  assert.equal(recipient.recipient.chatRef, TARGET_CHAT);
});

test('visually identical source records in the same minute refuse', async () => {
  const twin = sourceMessage({ sourceRef: `message:${'9'.repeat(24)}`, sourceTimestamp: sourceMessage().sourceTimestamp + 1000,
    time: '11:24:31' });
  await assert.rejects(bind(sourceInput(), sourcePage([sourceMessage(), twin])),
    { code: 'LINE_FORWARD_SOURCE_AMBIGUOUS' });
});

test('empty-text attachment binds only actual reader metadata and refuses same-size twins', async () => {
  const media = { declaredFileBytes: 12_345, fileName: 'same.pdf',
    fileNameShape: { suffix: '.pdf', type: 'str' } };
  const attachment = sourceMessage({ contentType: 14, text: null, media });
  const binding = await bind(sourceInput(14), sourcePage([attachment]));
  assert.equal(binding.source.text, null);
  assert.deepEqual(binding.source.attachment, { fileName: 'same.pdf', declaredFileBytes: 12_345, fileSuffix: '.pdf' });
  assert.equal(binding.source.sender, '原發送者');
  await assert.rejects(bind(sourceInput(14), sourcePage([attachment,
    sourceMessage({ sourceRef: `message:${'8'.repeat(24)}`, contentType: 14, text: '', media })])),
    { code: 'LINE_FORWARD_SOURCE_AMBIGUOUS' });
  await assert.rejects(bind(sourceInput(14), sourcePage([attachment,
    sourceMessage({ sourceRef: `message:${'7'.repeat(24)}`, contentType: 14, text: null,
      media: { state: 'not_resolved', metadata: { format: 'json-object' } } })])),
    { code: 'LINE_FORWARD_SOURCE_AMBIGUOUS' });
  await assert.rejects(bind(sourceInput(7), sourcePage([sourceMessage({ contentType: 7 })])),
    { code: 'LINE_INVALID_ARGUMENT' });
});

test('receipt needs a fresh unique new record from the same chat and own sender', async () => {
  const binding = await bind();
  const dispatch = NOW + 1000;
  const before = recipientPage([], { freshness: { snapshotCapturedAt: at(NOW), clockOrderValid: true } });
  const sent = sourceMessage({ sourceRef: NEW_REF, senderRef: SELF,
    date: '2026-09-24', time: '12:00:02', sourceTimestamp: dispatch + 1000 });
  const after = recipientPage([sent], { freshness: { snapshotCapturedAt: at(dispatch + 2000), clockOrderValid: true } });
  const options = { dispatchedAt: dispatch, now: () => dispatch + 3000 };
  assert.equal(matchForwardReceipt(binding, before, after, options)?.strength, 'exact_text_local_record');
  assert.equal(matchForwardReceipt(binding, before, recipientPage([{ ...sent, senderRef: OTHER }],
    { freshness: after.freshness }), options), null);
  assert.equal(matchForwardReceipt(binding, before, recipientPage([sent, { ...sent,
    sourceRef: `message:${'1'.repeat(24)}` }], { freshness: after.freshness }), options), null);
  assert.equal(matchForwardReceipt(binding, before, recipientPage([{ ...sent, contentType: 1 }],
    { freshness: after.freshness }), options), null);
  assert.equal(matchForwardReceipt(binding, recipientPage([sent], { freshness: before.freshness }), after, options), null);
  assert.equal(matchForwardReceipt(binding, before, recipientPage([sent], { chatRef: SOURCE_CHAT,
    freshness: after.freshness }), options), null);
  const anchor = { ...sent, sourceRef: `message:${'3'.repeat(24)}`, sourceTimestamp: dispatch - 2000 };
  const busyBefore = recipientPage([anchor], { freshness: before.freshness,
    scope: { kind: 'local_database', truncated: true }, pagination: { hasMore: true } });
  const busyAfter = recipientPage([anchor, sent], { freshness: after.freshness,
    scope: { kind: 'local_database', truncated: true }, pagination: { hasMore: true } });
  assert.equal(matchForwardReceipt(binding, busyBefore, busyAfter, options)?.sourceRef, NEW_REF);
  assert.equal(matchForwardReceipt(binding, busyBefore, recipientPage([sent], {
    freshness: after.freshness, scope: { kind: 'local_database', truncated: true },
    pagination: { hasMore: true } }), options), null);
});

test('attachment receipt is metadata-only and absent when metadata is insufficient or ambiguous', async () => {
  const media = { declaredFileBytes: 12_345, fileName: 'same.pdf',
    fileNameShape: { suffix: '.pdf' } };
  const binding = await bind(sourceInput(14), sourcePage([sourceMessage({ contentType: 14, text: null, media })]));
  const dispatch = NOW + 1000;
  const before = recipientPage([], { freshness: { snapshotCapturedAt: at(NOW), clockOrderValid: true } });
  const sent = sourceMessage({ sourceRef: NEW_REF, senderRef: SELF, contentType: 14,
    text: null, media, sourceTimestamp: dispatch + 1000 });
  const fresh = { freshness: { snapshotCapturedAt: at(dispatch + 2000), clockOrderValid: true } };
  const options = { dispatchedAt: dispatch, now: () => dispatch + 3000 };
  const receipt = matchForwardReceipt(binding, before, recipientPage([sent], fresh), options);
  assert.equal(receipt?.strength, 'attachment_metadata_local_record');
  assert.equal(receipt.attachmentByteIdentityVerified, false);
  assert.equal(receipt.deliveryVerified, false);
  assert.equal(matchForwardReceipt(binding, before, recipientPage([{ ...sent,
    media: { state: 'not_resolved' } }], fresh), options), null);
  assert.equal(matchForwardReceipt(binding, before, recipientPage([{ ...sent,
    media: { declaredFileBytes: 12_345, fileNameShape: { suffix: '.pdf' } } }], fresh), options), null);
  assert.equal(matchForwardReceipt(binding, before, recipientPage([{ ...sent,
    media: { ...media, fileName: 'other.pdf' } }], fresh), options), null);
  assert.equal(matchForwardReceipt(binding, before, recipientPage([sent, { ...sent,
    sourceRef: `message:${'2'.repeat(24)}` }], fresh), options), null);
});
