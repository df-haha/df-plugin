import { createHash, randomBytes } from 'node:crypto';
import { constants as fsConstants, promises as fs } from 'node:fs';
import { homedir } from 'node:os';
import { isAbsolute, join, parse, relative, sep } from 'node:path';

import { withLineOperation } from '../automation/line-operation-lock.mjs';
import { resolveForwardBinding, matchForwardReceipt } from './line-forward-binding.mjs';
import { readLocalLineMessages } from './line-local-reader.mjs';
import { LineToolError } from './line-runtime.mjs';

const OPERATION = /^forward_[0-9a-f]{32}$/u;
const SENDER = /^sender:[0-9a-f]{24}$/u;
const CHAT = /^chat:[0-9a-f]{24}$/u;
const MESSAGE = /^message:[0-9a-f]{24}$/u;
const DIGEST = /^[0-9a-f]{64}$/u;
const KEY_FILE = /^key_[0-9a-f]{64}\.json$/u;
const PREPARATION_MS = 5 * 60_000;
const FRESH_MS = 60_000;
const VERIFY_MAX_MS = 5_000;
const MAX_RECORD_BYTES = 65_536;
const TAIPEI_OFFSET_MS = 8 * 60 * 60_000;
const DISPATCHED = new Set(['DISPATCH_INTENT', 'UNCERTAIN', 'RECORDED_LOCAL']);
const STATES = new Set(['PREPARING', 'PREPARE_NOT_READY', 'PREPARED', 'EXPIRED',
  'CANCELLED', ...DISPATCHED]);
const CONTENT_TYPES = new Set([0, 1, 2, 3, 14]);

export const DEFAULT_FORWARD_STORE_ROOT = join(
  process.env.LOCALAPPDATA || join(homedir(), 'AppData', 'Local'),
  'line-desktop-mcp', 'forwards',
);

const object = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const hash = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');
const fail = (code, message, details = {}) => { throw new LineToolError(code, message, details); };
const day = time => new Date(time + TAIPEI_OFFSET_MS).toISOString().slice(0, 10);
const name = value => typeof value === 'string' && value.length > 0
  && value.length <= 200 && value === value.trim() && !/[\x00-\x1f]/u.test(value);
const date = value => typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/u.test(value)
  && Number.isFinite(Date.parse(`${value}T00:00:00Z`))
  && new Date(`${value}T00:00:00Z`).toISOString().slice(0, 10) === value;

function timeValue(now) {
  const value = now();
  const time = value instanceof Date ? value.valueOf() : value;
  if (!Number.isSafeInteger(time) || time < 0) fail('LINE_FORWARD_CLOCK_INVALID', 'The forward clock was invalid.');
  return time;
}

function token(randomToken) {
  const value = randomToken();
  if (typeof value !== 'string' || !/^[0-9a-f]{32}$/u.test(value))
    fail('LINE_FORWARD_TOKEN_INVALID', 'The forward token generator was invalid.');
  return value;
}

function request(args) {
  if (!object(args) || !SENDER.test(args.accountRef ?? '')
    || typeof args.idempotencyKey !== 'string' || !args.idempotencyKey
    || args.idempotencyKey.length > 200 || /[\x00-\x1f]/u.test(args.idempotencyKey)
    || !object(args.source) || !object(args.recipient)) {
    fail('LINE_INVALID_ARGUMENT', 'Forward preparation needs an account, source, recipient and stable key.');
  }
  const input = { accountRef: args.accountRef, source: args.source, recipient: args.recipient };
  return { input, inputDigest: hash(input), keyHash: hash([args.accountRef, args.idempotencyKey]) };
}

function locator(binding) {
  const source = binding.source;
  return { chatName: source.chatName, chatType: source.chatType,
    chatRef: source.chatRef, sourceRef: source.sourceRef, date: source.date,
    contentType: source.contentType, attachment: source.attachment ?? null };
}

function sameBinding(binding, record) {
  return binding?.accountRef === record.accountRef
    && binding?.ownSenderRef === record.accountRef
    && binding?.source?.digest === record.sourceDigest
    && hash({ source: locator(binding), recipient: binding.recipient }) === record.bindingLocatorDigest;
}

function checkBinding(binding, accountRef) {
  if (!object(binding) || binding.accountRef !== accountRef
    || binding.ownSenderRef !== accountRef || !SENDER.test(binding.ownSenderRef ?? '')
    || !object(binding.source) || !object(binding.recipient)
    || !CHAT.test(binding.source.chatRef ?? '') || !CHAT.test(binding.recipient.chatRef ?? '')
    || !MESSAGE.test(binding.source.sourceRef ?? '') || !DIGEST.test(binding.source.digest ?? '')
    || binding.source.chatRef === binding.recipient.chatRef) {
    fail('LINE_FORWARD_BINDING_INVALID', 'The current account, source or recipient binding is invalid.');
  }
}

function reviewOf(binding) {
  const source = binding.source;
  return { accountRef: binding.accountRef,
    source: { chatName: source.chatName, chatType: source.chatType,
      chatRef: source.chatRef, sourceRef: source.sourceRef, date: source.date,
      time: source.time, sender: source.sender, senderRef: source.senderRef,
      contentType: source.contentType, text: source.text,
      attachment: source.attachment, digest: source.digest },
    recipient: binding.recipient, method: 'LINE_NATIVE_FORWARD', recipientCount: 1 };
}

function validateRecord(record, keyHash) {
  if (!object(record) || record.version !== 1 || record.keyHash !== keyHash
    || !DIGEST.test(record.keyHash ?? '') || !OPERATION.test(record.operationId ?? '')
    || !SENDER.test(record.accountRef ?? '') || !DIGEST.test(record.inputDigest ?? '')
    || !DIGEST.test(record.sourceDigest ?? '') || !DIGEST.test(record.bindingLocatorDigest ?? '')
    || !DIGEST.test(record.intentDigest ?? '') || !object(record.source)
    || !CHAT.test(record.source.chatRef ?? '') || !MESSAGE.test(record.source.sourceRef ?? '')
    || !name(record.source.chatName) || !['direct', 'group'].includes(record.source.chatType)
    || !date(record.source.date) || !CONTENT_TYPES.has(record.source.contentType)
    || !object(record.recipient) || !CHAT.test(record.recipient.chatRef ?? '')
    || !name(record.recipient.chatName) || !['direct', 'group'].includes(record.recipient.chatType)
    || record.source.chatRef === record.recipient.chatRef
    || !STATES.has(record.status)
    || !Number.isSafeInteger(record.createdAt) || record.createdAt < 0
    || !Number.isSafeInteger(record.updatedAt) || record.updatedAt < 0) {
    fail('LINE_FORWARD_JOURNAL_INVALID', 'A forward journal record is invalid.');
  }
  if (DISPATCHED.has(record.status)
    && (!Number.isSafeInteger(record.dispatchedAt) || !object(record.baseline)
      || record.baseline.chatRef !== record.recipient.chatRef
      || record.baseline.chatName !== record.recipient.chatName
      || record.baseline.chatIdentity?.kind !== record.recipient.chatType
      || record.baseline.ownSenderRef !== record.accountRef
      || record.baseline.scope?.kind !== 'local_database'
      || !date(record.baseline.scope?.dateFrom)
      || !date(record.baseline.scope?.dateTo)
      || !Array.isArray(record.baseline.messages)
      || record.baseline.messages.length > 50
      || record.baseline.messages.some(row => !MESSAGE.test(row?.sourceRef ?? ''))
      || typeof record.baseline.pagination?.hasMore !== 'boolean'
      || record.baseline.scope.truncated !== record.baseline.pagination.hasMore
      || !Number.isFinite(Date.parse(record.baseline.freshness?.snapshotCapturedAt)))) {
    fail('LINE_FORWARD_JOURNAL_INVALID', 'A dispatched forward record lacks its baseline.');
  }
  if (record.status === 'PREPARED'
    && (!DIGEST.test(record.reviewDigest ?? '')
      || !DIGEST.test(record.preparationTokenHash ?? '')
      || !Number.isSafeInteger(record.expiresAt))) {
    fail('LINE_FORWARD_JOURNAL_INVALID', 'A prepared forward record lacks its active review.');
  }
  if (record.status === 'RECORDED_LOCAL'
    && (!object(record.receipt) || !MESSAGE.test(record.receipt.sourceRef ?? '')
      || !Number.isSafeInteger(record.receipt.sourceTimestamp)
      || record.receipt.contentType !== record.source.contentType
      || record.receipt.limits?.localRecordVerified !== true
      || record.receipt.limits?.deliveryVerified !== false)) {
    fail('LINE_FORWARD_JOURNAL_INVALID', 'A recorded forward lacks a valid local receipt.');
  }
  return record;
}

async function ensureSafeRoot(root) {
  if (typeof root !== 'string' || !isAbsolute(root))
    fail('LINE_FORWARD_STORE_INVALID', 'The forward journal root must be absolute.');
  const base = parse(root).root;
  let current = base;
  for (const part of relative(base, root).split(sep).filter(Boolean)) {
    current = join(current, part);
    let stat;
    try { stat = await fs.lstat(current); }
    catch (error) {
      if (error?.code !== 'ENOENT') throw error;
      try { await fs.mkdir(current, { mode: 0o700 }); }
      catch (mkdirError) { if (mkdirError?.code !== 'EEXIST') throw mkdirError; }
      stat = await fs.lstat(current);
    }
    if (!stat.isDirectory() || stat.isSymbolicLink())
      fail('LINE_FORWARD_STORE_UNSAFE', 'The forward journal path contains a link or non-directory.');
  }
}

async function readRecord(root, keyHash) {
  const path = join(root, `key_${keyHash}.json`);
  let stat;
  try { stat = await fs.lstat(path); }
  catch (error) { if (error?.code === 'ENOENT') return null; throw error; }
  if (!stat.isFile() || stat.isSymbolicLink() || stat.size > MAX_RECORD_BYTES)
    fail('LINE_FORWARD_JOURNAL_UNSAFE', 'The forward journal record is unsafe.');
  const flags = fsConstants.O_RDONLY | (fsConstants.O_NOFOLLOW || 0);
  const handle = await fs.open(path, flags);
  try {
    const opened = await handle.stat();
    if (!opened.isFile() || opened.dev !== stat.dev || opened.ino !== stat.ino
      || opened.size > MAX_RECORD_BYTES) {
      fail('LINE_FORWARD_JOURNAL_UNSAFE', 'The forward journal record changed while opening.');
    }
    let parsed;
    try { parsed = JSON.parse(await handle.readFile('utf8')); }
    catch { fail('LINE_FORWARD_JOURNAL_INVALID', 'The forward journal record could not be parsed.'); }
    return validateRecord(parsed, keyHash);
  } finally { await handle.close(); }
}

async function syncDirectory(root) {
  try {
    const handle = await fs.open(root, 'r');
    try { await handle.sync(); } finally { await handle.close(); }
  } catch (error) {
    // Windows often refuses directory handles. The temp file itself was synced
    // before the atomic rename; a failed directory sync cannot permit a click.
    if (!['EACCES', 'EPERM', 'EINVAL', 'ENOTSUP', 'EISDIR'].includes(error?.code)) throw error;
  }
}

async function readWithinDeadline(read, deadline) {
  const remaining = deadline - Date.now();
  if (remaining <= 0) fail('LINE_FORWARD_RECEIPT_READ_TIMEOUT', 'The bounded local receipt read timed out.');
  let timer;
  try {
    return await Promise.race([read(), new Promise((_, reject) => {
      timer = setTimeout(() => reject(new LineToolError('LINE_FORWARD_RECEIPT_READ_TIMEOUT',
        'The bounded local receipt read timed out.')), remaining);
    })]);
  } finally { clearTimeout(timer); }
}

async function writeRecord(root, record) {
  validateRecord(record, record.keyHash);
  await ensureSafeRoot(root);
  const path = join(root, `key_${record.keyHash}.json`);
  const temp = join(root, `.forward_${randomBytes(16).toString('hex')}.tmp`);
  let handle;
  try {
    handle = await fs.open(temp, 'wx', 0o600);
    await handle.writeFile(`${JSON.stringify(record)}\n`, 'utf8');
    await handle.sync();
    await handle.close();
    handle = null;
    await ensureSafeRoot(root);
    try {
      const current = await fs.lstat(path);
      if (!current.isFile() || current.isSymbolicLink())
        fail('LINE_FORWARD_JOURNAL_UNSAFE', 'The forward journal target is unsafe.');
    } catch (error) { if (error?.code !== 'ENOENT') throw error; }
    await fs.rename(temp, path);
    await syncDirectory(root);
  } catch (error) {
    if (handle) await handle.close().catch(() => {});
    await fs.unlink(temp).catch(() => {});
    throw error;
  }
}

async function records(root) {
  const entries = await fs.readdir(root);
  const result = [];
  for (const name of entries) {
    if (!KEY_FILE.test(name)) continue;
    const record = await readRecord(root, name.slice(4, -5));
    if (record) result.push(record);
  }
  return result;
}

function recipientScope(record, from, to) {
  return { chatName: record.recipient.chatName, chatType: record.recipient.chatType,
    expectedChatRef: record.recipient.chatRef, requireUniqueName: true,
    dateFrom: from, dateTo: to, messageLimit: 50, mediaMode: 'metadata' };
}

function receiptSummary(receipt, binding) {
  if (!object(receipt) || !MESSAGE.test(receipt.sourceRef ?? '')
    || !Number.isSafeInteger(receipt.sourceTimestamp)
    || receipt.contentType !== binding.source.contentType
    || !['exact_text_local_record', 'attachment_metadata_local_record'].includes(receipt.strength)
    || receipt.localRecordVerified !== true || receipt.deliveryVerified !== false) return null;
  return { sourceRef: receipt.sourceRef, sourceTimestamp: receipt.sourceTimestamp,
    time: new Date(receipt.sourceTimestamp).toISOString(), contentType: receipt.contentType,
    attachment: binding.source.attachment ?? null, matchStrength: receipt.strength,
    limits: { localRecordVerified: true, deliveryVerified: false,
      attachmentByteIdentityVerified: false, description: receipt.limitation } };
}

function minimalBaseline(page, recipient, accountRef, readAt) {
  const capturedAt = Date.parse(page?.freshness?.snapshotCapturedAt);
  if (page?.ok !== true || page.chatName !== recipient.chatName
    || page.chatRef !== recipient.chatRef || page.chatIdentity?.kind !== recipient.chatType
    || page.ownSenderRef !== accountRef || page.scope?.kind !== 'local_database'
    || page.scope.truncated !== page.pagination?.hasMore
    || !Array.isArray(page.messages) || page.messages.length > 50
    || typeof page.pagination?.hasMore !== 'boolean'
    || page.freshness?.clockOrderValid !== true || !Number.isFinite(capturedAt)
    || capturedAt > readAt + 5_000 || readAt - capturedAt > FRESH_MS
    || page.messages.some(row => !MESSAGE.test(row?.sourceRef ?? ''))) {
    fail('LINE_FORWARD_RECIPIENT_LOCAL_UNVERIFIED', 'A fresh exact recipient baseline was unavailable.');
  }
  return { chatName: page.chatName, chatRef: page.chatRef,
    chatIdentity: { kind: page.chatIdentity.kind }, ownSenderRef: page.ownSenderRef,
    messages: page.messages.map(row => ({ sourceRef: row.sourceRef })),
    pagination: { hasMore: page.pagination.hasMore },
    freshness: { snapshotCapturedAt: page.freshness.snapshotCapturedAt },
    scope: { kind: 'local_database', truncated: page.scope.truncated } };
}

function statusResult(record, extra = {}) {
  return { status: record.status, operationId: record.operationId,
    accountRef: record.accountRef,
    source: { chatRef: record.source.chatRef, sourceRef: record.source.sourceRef,
      contentType: record.source.contentType, attachment: record.source.attachment ?? null },
    recipient: { chatRef: record.recipient.chatRef, chatName: record.recipient.chatName,
      chatType: record.recipient.chatType },
    localRecordVerified: record.status === 'RECORDED_LOCAL', deliveryVerified: false,
    sendDispatched: DISPATCHED.has(record.status) ? 'uncertain' : false,
    ...(record.status === 'RECORDED_LOCAL' ? { receipt: record.receipt,
      sendDispatched: true } : {}), ...extra };
}

function priorBlocksFreshIntent(prior, createdAt) {
  if (prior.status === 'DISPATCH_INTENT' || prior.status === 'UNCERTAIN') return true;
  // A completed prior forward permits a separately reviewed repeat only if
  // that receipt was already recorded before this operation was created.
  // This also revokes a second key that was prepared before the first click.
  return prior.status === 'RECORDED_LOCAL'
    && (prior.dispatchedAt >= createdAt || prior.updatedAt >= createdAt);
}

/** One process owns prepared UI authority; only the idempotency/dispatch journal survives restart. */
export function createForwardTransaction({
  ui, readMessages = readLocalLineMessages, readIdentity = readLocalLineMessages,
  resolveBinding = resolveForwardBinding, matchReceipt = matchForwardReceipt,
  runOperation = withLineOperation, storeRoot = DEFAULT_FORWARD_STORE_ROOT,
  now = Date.now, randomToken = () => randomBytes(16).toString('hex'),
} = {}) {
  if (!object(ui) || ['prepare', 'assertReady', 'dispatch'].some(name => typeof ui[name] !== 'function')
    || typeof readMessages !== 'function' || typeof readIdentity !== 'function'
    || typeof resolveBinding !== 'function' || typeof matchReceipt !== 'function'
    || typeof runOperation !== 'function' || typeof now !== 'function'
    || typeof randomToken !== 'function') {
    throw new TypeError('Forward transaction dependencies are incomplete.');
  }
  const prepared = new Map();
  const locked = fn => runOperation('forward-transaction', fn);

  async function getById(operationId) {
    if (!OPERATION.test(operationId ?? ''))
      fail('LINE_INVALID_ARGUMENT', 'A valid forward operationId is required.');
    await ensureSafeRoot(storeRoot);
    const found = (await records(storeRoot)).filter(record => record.operationId === operationId);
    if (found.length !== 1) fail('LINE_FORWARD_NOT_FOUND', 'The forward operation was not found.');
    return found[0];
  }

  async function bindingFor(record) {
    const binding = await resolveBinding({ source: record.source, recipient: record.recipient },
      { readMessages, readIdentity, now });
    checkBinding(binding, record.accountRef);
    if (!sameBinding(binding, record))
      fail('LINE_FORWARD_SOURCE_CHANGED', 'The bound forward source or recipient changed.');
    return binding;
  }

  async function verifyInside(record) {
    if (record.status === 'RECORDED_LOCAL') return statusResult(record);
    if (!DISPATCHED.has(record.status)) return statusResult(record);
    const current = timeValue(now);
    const startDay = record.baseline.scope.dateFrom;
    const endDay = day(current);
    if (current - record.dispatchedAt > 31 * 86_400_000
      || (Date.parse(endDay) - Date.parse(startDay)) / 86_400_000 > 30) {
      return statusResult({ ...record, status: 'UNCERTAIN' }, { reason: 'LINE_FORWARD_VERIFY_RANGE_EXPIRED' });
    }
    let binding;
    try { binding = await bindingFor(record); }
    catch (error) {
      return statusResult({ ...record, status: 'UNCERTAIN' },
        { reason: error?.code || 'LINE_FORWARD_SOURCE_UNVERIFIED' });
    }
    let receipt = null;
    let reason = 'LINE_FORWARD_RECEIPT_NOT_FOUND';
    const deadline = Date.now() + VERIFY_MAX_MS;
    for (let attempt = 0; attempt < 3 && Date.now() < deadline; attempt += 1) {
      try {
        const scope = recipientScope(record, startDay, endDay);
        if (binding.source.contentType !== 0) scope.mediaMode = 'preview';
        const after = await readWithinDeadline(() => readMessages(scope), deadline);
        const capturedAt = Date.parse(after?.freshness?.snapshotCapturedAt);
        const readAt = timeValue(now);
        if (!Number.isFinite(capturedAt) || capturedAt > readAt + 5_000
          || readAt - capturedAt > FRESH_MS) {
          reason = 'LINE_FORWARD_RECIPIENT_LOCAL_UNVERIFIED';
          continue;
        }
        // The matcher independently checks chat, account, freshness, baseline,
        // uniqueness and content. Bound its candidate time to the dispatch
        // window, so a later independent send cannot appear as this receipt.
        receipt = receiptSummary(matchReceipt(binding, record.baseline, after,
          { dispatchedAt: record.dispatchedAt,
            now: () => Math.min(timeValue(now), record.dispatchedAt + PREPARATION_MS) }), binding);
        if (receipt) break;
      } catch (error) { reason = error?.code || 'LINE_FORWARD_RECIPIENT_LOCAL_UNVERIFIED'; }
      if (attempt < 2 && Date.now() < deadline - 250)
        await new Promise(resolve => setTimeout(resolve, 150));
    }
    if (receipt) {
      const next = { ...record, status: 'RECORDED_LOCAL', receipt,
        updatedAt: timeValue(now) };
      await writeRecord(storeRoot, next);
      return statusResult(next);
    }
    if (record.status !== 'UNCERTAIN') {
      record = { ...record, status: 'UNCERTAIN', updatedAt: timeValue(now) };
      await writeRecord(storeRoot, record);
    }
    return statusResult(record, { reason });
  }

  return {
    async prepare(args) {
      return locked(async () => {
        const { input, inputDigest, keyHash } = request(args);
        await ensureSafeRoot(storeRoot);
        let record = await readRecord(storeRoot, keyHash);
        if (record && record.inputDigest !== inputDigest)
          fail('LINE_FORWARD_KEY_CONFLICT', 'The idempotency key belongs to a different forward intent.');
        if (record?.status === 'CANCELLED') return statusResult(record);
        if (record && DISPATCHED.has(record.status)) return verifyInside(record);
        const binding = await resolveBinding({ source: input.source, recipient: input.recipient },
          { readMessages, readIdentity, now });
        checkBinding(binding, input.accountRef);
        if (record && !sameBinding(binding, record))
          fail('LINE_FORWARD_KEY_CONFLICT', 'The idempotency key source or recipient changed.');
        const source = locator(binding);
        // A new key cannot replay the same original message to the same chat,
        // even if its later metadata digest changes.
        const intentDigest = hash({ accountRef: input.accountRef,
          sourceChatRef: source.chatRef, sourceRef: source.sourceRef,
          recipientChatRef: binding.recipient.chatRef });
        if (!record) {
          const createdAt = timeValue(now);
          for (const prior of await records(storeRoot)) {
            if (prior.intentDigest === intentDigest
              && priorBlocksFreshIntent(prior, createdAt))
              fail('LINE_FORWARD_ALREADY_DISPATCHED', 'This source and recipient already have dispatch intent; use verify.',
                { operationId: prior.operationId, operationMayHaveCompleted: true });
          }
          record = { version: 1, keyHash, operationId: `forward_${token(randomToken)}`,
            accountRef: input.accountRef, inputDigest, sourceDigest: binding.source.digest,
            bindingLocatorDigest: hash({ source, recipient: binding.recipient }), intentDigest,
            source, recipient: binding.recipient, status: 'PREPARING', createdAt,
            updatedAt: createdAt };
          await writeRecord(storeRoot, record);
        }
        const previous = prepared.get(record.operationId);
        if (previous && previous.expiresAt > timeValue(now)
          && previous.reviewDigest === record.reviewDigest) {
          try {
            await ui.assertReady(binding, previous.session);
            return previous.result;
          } catch { prepared.delete(record.operationId); }
        }
        const preparing = { ...record, status: 'PREPARING', updatedAt: timeValue(now),
          reviewDigest: null, preparationTokenHash: null, expiresAt: null };
        await writeRecord(storeRoot, preparing);
        record = preparing;
        const state = await ui.prepare(binding);
        if (state?.stage !== 'PREPARED' || !object(state.session)) {
          record = { ...record, status: 'PREPARE_NOT_READY', updatedAt: timeValue(now) };
          await writeRecord(storeRoot, record);
          return statusResult(record, { reason: state?.reason || 'LINE_FORWARD_UI_UNVERIFIED',
            currentStage: state?.currentStage || 'UI_EVIDENCE_UNAVAILABLE',
            evidence: state?.evidence,
            images: state?.images });
        }
        const issuedAt = timeValue(now);
        const uiExpiry = state.session.expiresAt;
        if (!Number.isSafeInteger(uiExpiry) || uiExpiry <= issuedAt) {
          record = { ...record, status: 'PREPARE_NOT_READY', updatedAt: issuedAt };
          await writeRecord(storeRoot, record);
          return statusResult(record, { reason: 'LINE_FORWARD_UI_EVIDENCE_EXPIRED' });
        }
        const expiresAt = Math.min(uiExpiry, issuedAt + PREPARATION_MS);
        const review = reviewOf(binding);
        const reviewDigest = hash(review);
        const preparationId = `prep_${token(randomToken)}`;
        record = { ...record, status: 'PREPARED', reviewDigest,
          preparationTokenHash: hash(preparationId), expiresAt,
          updatedAt: issuedAt };
        await writeRecord(storeRoot, record);
        const result = { status: 'PREPARED', operationId: record.operationId,
          preparationId, reviewDigest, expiresAt: new Date(expiresAt).toISOString(),
          review, evidence: state.evidence, images: state.images, sendDispatched: false };
        prepared.set(record.operationId, { binding, session: state.session, preparationId,
          reviewDigest, expiresAt, result });
        return result;
      });
    },

    async confirm(args) {
      return locked(async () => {
        const record = await getById(args?.operationId);
        if (DISPATCHED.has(record.status)) return verifyInside(record);
        if (args?.confirmed !== true)
          fail('LINE_FORWARD_CONFIRMATION_REQUIRED', 'The reviewed forward must be explicitly confirmed.');
        if (record.status !== 'PREPARED')
          fail('LINE_FORWARD_NOT_PREPARED', 'This forward is not prepared for dispatch.');
        const memory = prepared.get(record.operationId);
        if (!memory || args.preparationId !== memory.preparationId
          || args.reviewDigest !== memory.reviewDigest
          || args.reviewDigest !== record.reviewDigest
          || hash(args.preparationId) !== record.preparationTokenHash)
          fail('LINE_FORWARD_PREPARATION_NOT_ACTIVE', 'The current process has no matching preparation.');
        if (timeValue(now) >= memory.expiresAt || timeValue(now) >= record.expiresAt) {
          prepared.delete(record.operationId);
          const expired = { ...record, status: 'EXPIRED', updatedAt: timeValue(now) };
          await writeRecord(storeRoot, expired);
          return statusResult(expired);
        }
        const binding = await bindingFor(record);
        if (hash(reviewOf(binding)) !== memory.reviewDigest)
          fail('LINE_FORWARD_REVIEW_CHANGED', 'The reviewed source or recipient changed.');
        for (const prior of await records(storeRoot)) {
          if (prior.operationId !== record.operationId
            && prior.intentDigest === record.intentDigest
            && priorBlocksFreshIntent(prior, record.createdAt)) {
            fail('LINE_FORWARD_ALREADY_DISPATCHED',
              'This source and recipient already have dispatch intent; use verify.',
              { operationId: prior.operationId, operationMayHaveCompleted: true });
          }
        }
        await ui.assertReady(binding, memory.session);
        const readAt = timeValue(now);
        const from = day(readAt - 86_400_000);
        const to = day(readAt);
        const baselinePage = await readMessages(recipientScope(record, from, to));
        const baseline = minimalBaseline(baselinePage, record.recipient, record.accountRef, timeValue(now));
        baseline.scope = { ...baseline.scope, dateFrom: from, dateTo: to };
        let intentCommitted = false;
        let callbackCalls = 0;
        try {
          await ui.dispatch(binding, memory.session, async () => {
            callbackCalls += 1;
            if (callbackCalls !== 1)
              fail('LINE_FORWARD_DISPATCH_PROTOCOL_VIOLATION',
                'The final Share authorization callback was invoked more than once.',
                { operationMayHaveCompleted: true });
            const dispatchedAt = timeValue(now);
            if (dispatchedAt - Date.parse(baseline.freshness.snapshotCapturedAt) > FRESH_MS)
              fail('LINE_FORWARD_BASELINE_STALE', 'The recipient baseline aged before the final Share action.');
            const intent = { ...record, status: 'DISPATCH_INTENT', baseline,
              dispatchedAt, updatedAt: dispatchedAt };
            await writeRecord(storeRoot, intent);
            intentCommitted = true;
            record.status = 'DISPATCH_INTENT';
            record.baseline = baseline;
            record.dispatchedAt = dispatchedAt;
            prepared.delete(record.operationId);
          });
        } catch (error) {
          prepared.delete(record.operationId);
          if (!intentCommitted) throw error;
          // The final click may have happened even when CUA reports a timeout.
          return verifyInside({ ...record, status: 'DISPATCH_INTENT' });
        }
        if (!intentCommitted) {
          prepared.delete(record.operationId);
          const dispatchedAt = timeValue(now);
          await writeRecord(storeRoot, { ...record, status: 'DISPATCH_INTENT',
            baseline, dispatchedAt, updatedAt: dispatchedAt });
          fail('LINE_FORWARD_DISPATCH_PROTOCOL_VIOLATION',
            'The UI adapter returned without the required durable dispatch callback.',
            { operationMayHaveCompleted: true, operationId: record.operationId });
        }
        return verifyInside({ ...record, status: 'DISPATCH_INTENT' });
      });
    },

    async verify(args) {
      return locked(async () => verifyInside(await getById(args?.operationId)));
    },

    async cancel(args) {
      return locked(async () => {
        const record = await getById(args?.operationId);
        if (DISPATCHED.has(record.status))
          fail('LINE_FORWARD_ALREADY_DISPATCHED', 'Dispatch intent cannot be cancelled or reset.',
            { operationId: record.operationId, operationMayHaveCompleted: true });
        if (record.status === 'CANCELLED')
          return statusResult(record, { guiMayRemainOpen: record.guiMayRemainOpen !== false });
        const memory = prepared.get(record.operationId);
        prepared.delete(record.operationId);
        const cancelled = { ...record, status: 'CANCELLED', updatedAt: timeValue(now) };
        await writeRecord(storeRoot, cancelled);
        let guiMayRemainOpen = true;
        if (memory && typeof ui.cancel === 'function') {
          try {
            const result = await ui.cancel(memory.binding, memory.session);
            guiMayRemainOpen = result?.closed !== true;
          } catch { /* Journal cancellation remains authoritative. */ }
        }
        if (!guiMayRemainOpen) {
          cancelled.guiMayRemainOpen = false;
          cancelled.updatedAt = timeValue(now);
          await writeRecord(storeRoot, cancelled);
        }
        return statusResult(cancelled, { guiMayRemainOpen });
      });
    },
  };
}
