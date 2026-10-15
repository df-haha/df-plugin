import { createHash } from 'node:crypto';
import { LineToolError } from './line-runtime.mjs';
import { readLocalLineMessages } from './line-local-reader.mjs';

const CHAT_REF = /^chat:[0-9a-f]{24}$/u;
const MESSAGE_REF = /^message:[0-9a-f]{24}$/u;
const SENDER_REF = /^sender:[0-9a-f]{24}$/u;
const SUPPORTED_TYPES = new Set([0, 1, 2, 3, 14]);
const MAX_SNAPSHOT_AGE_MS = 60_000;
const TAIPEI_OFFSET_MS = 8 * 60 * 60 * 1000;

const fail = (code, message) => { throw new LineToolError(code, message, { sendDispatched: false }); };
const day = instant => new Date(instant + TAIPEI_OFFSET_MS).toISOString().slice(0, 10);
const record = value => value !== null && typeof value === 'object' && !Array.isArray(value);
const digest = value => createHash('sha256').update(JSON.stringify(value)).digest('hex');

function instant(value) {
  const time = value instanceof Date ? value.valueOf() : value;
  if (!Number.isSafeInteger(time) || time < 0) fail('LINE_INVALID_ARGUMENT', 'now must be a valid time.');
  return time;
}

function date(value) {
  const parsed = typeof value === 'string' && /^\d{4}-\d{2}-\d{2}$/u.test(value)
    ? Date.parse(`${value}T00:00:00Z`) : NaN;
  if (typeof value !== 'string' || !/^\d{4}-\d{2}-\d{2}$/u.test(value)
    || !Number.isFinite(parsed) || new Date(parsed).toISOString().slice(0, 10) !== value) {
    fail('LINE_INVALID_ARGUMENT', 'source.date must be a real YYYY-MM-DD date.');
  }
  return value;
}

function chat(value, name) {
  if (!record(value) || typeof value.chatName !== 'string' || !value.chatName
    || value.chatName !== value.chatName.trim() || value.chatName.length > 200
    || /[\x00-\x1f]/u.test(value.chatName)
    || !['direct', 'group'].includes(value.chatType) || !CHAT_REF.test(value.chatRef ?? '')) {
    fail('LINE_INVALID_ARGUMENT', `${name} needs an exact chat name, direct/group type and opaque chatRef.`);
  }
  return { chatName: value.chatName, chatType: value.chatType, chatRef: value.chatRef };
}

function validateResult(result, expected, readAt, { requireComplete = true } = {}) {
  const capturedAt = Date.parse(result?.freshness?.snapshotCapturedAt);
  if (result?.ok !== true || result.chatName !== expected.chatName
    || result.chatRef !== expected.chatRef || result.chatIdentity?.kind !== expected.chatType
    || result.scope?.kind !== 'local_database' || !Array.isArray(result.messages)
    || (requireComplete && (result.scope.truncated !== false || result.pagination?.hasMore !== false))
    || result.freshness?.clockOrderValid !== true || !Number.isFinite(capturedAt)
    || capturedAt > readAt + 5_000 || readAt - capturedAt > MAX_SNAPSHOT_AGE_MS
    || !SENDER_REF.test(result.ownSenderRef ?? '')) {
    fail('LINE_FORWARD_LOCAL_UNVERIFIED', 'The bounded local read did not prove a fresh chat and sender identity.');
  }
  return capturedAt;
}

function attachmentMetadata(message) {
  const media = message.media;
  if (!record(media)) return null;
  // fileName is exposed only after the reader validates contentInfo.fileName as a basename.
  const result = {};
  if (typeof media.fileName === 'string' && media.fileName.length >= 1
    && media.fileName.length <= 255 && media.fileName === media.fileName.trim()
    && !/[\\/:\x00-\x1f\x7f]/u.test(media.fileName) && !['.', '..'].includes(media.fileName))
    result.fileName = media.fileName;
  if (Number.isSafeInteger(media.declaredFileBytes) && media.declaredFileBytes > 0)
    result.declaredFileBytes = media.declaredFileBytes;
  if (record(media.fileNameShape) && typeof media.fileNameShape.suffix === 'string'
    && media.fileNameShape.suffix.length <= 24)
    result.fileSuffix = media.fileNameShape.suffix;
  if (Array.isArray(media.declaredDimensions) && media.declaredDimensions.length === 2
    && media.declaredDimensions.every(n => Number.isSafeInteger(n) && n > 0))
    result.declaredDimensions = [...media.declaredDimensions];
  if (typeof media.mimeType === 'string' && media.mimeType.length <= 100)
    result.mimeType = media.mimeType;
  return Object.keys(result).length ? result : null;
}

function sourceSummary(message) {
  if (!MESSAGE_REF.test(message?.sourceRef ?? '') || !SUPPORTED_TYPES.has(message.contentType)
    || !Number.isSafeInteger(message.sourceTimestamp) || !SENDER_REF.test(message.senderRef ?? '')) {
    fail('LINE_FORWARD_SOURCE_UNVERIFIED', 'The source record lacks an exact local identity or supported content type.');
  }
  if (message.contentType === 0 && typeof message.text !== 'string')
    fail('LINE_FORWARD_SOURCE_UNVERIFIED', 'The text source is incomplete.');
  const summary = {
    sourceRef: message.sourceRef,
    sourceMessageId: message.sourceMessageId ?? null,
    senderRef: message.senderRef,
    sender: typeof message.sender === 'string' ? message.sender : null,
    date: message.date,
    time: message.time,
    sourceTimestamp: message.sourceTimestamp,
    contentType: message.contentType,
    text: message.contentType === 0 ? message.text : null,
    attachment: message.contentType === 0 ? null : attachmentMetadata(message),
  };
  return { ...summary, digest: digest(summary) };
}

function visuallySame(a, b) {
  if (a.senderRef !== b.senderRef || a.date !== b.date || a.time?.slice(0, 5) !== b.time?.slice(0, 5)
    || a.contentType !== b.contentType) return false;
  if (a.contentType === 0) return a.text === b.text;
  const first = attachmentMetadata(a), second = attachmentMetadata(b);
  // Only the requested source receives preview metadata. A different row with
  // metadata-only fields might be visually identical, so absence is ambiguous.
  return !first || !second || JSON.stringify(first) === JSON.stringify(second);
}

/** Bind one caller-supplied opaque source and destination to fresh local records; no UI action. */
export async function resolveForwardBinding({ source, recipient }, {
  readMessages = readLocalLineMessages, readIdentity = readLocalLineMessages, now = Date.now,
} = {}) {
  const startedAt = instant(now());
  const sourceChat = chat(source, 'source');
  const recipientChat = chat(recipient, 'recipient');
  if (!MESSAGE_REF.test(source.sourceRef ?? '') || !SUPPORTED_TYPES.has(source.contentType))
    fail('LINE_INVALID_ARGUMENT', 'source needs an opaque message ref and supported content type.');
  const sourceDate = date(source.date);
  const currentDate = day(startedAt);
  const includesLatest = sourceDate <= currentDate
    && Date.parse(currentDate) - Date.parse(sourceDate) <= 30 * 86_400_000;
  const sourceScope = { chatName: sourceChat.chatName, chatType: sourceChat.chatType,
    expectedChatRef: sourceChat.chatRef, requireUniqueName: true,
    dateFrom: sourceDate, dateTo: includesLatest ? currentDate : sourceDate, messageLimit: 1000,
    mediaMode: 'preview', mediaSourceRefs: [source.sourceRef] };
  const sourceResult = await readMessages(sourceScope);
  validateResult(sourceResult, sourceChat, instant(now()));
  const matches = sourceResult.messages.filter(message => message.sourceRef === source.sourceRef);
  if (matches.length !== 1) fail('LINE_FORWARD_SOURCE_UNVERIFIED', 'The exact source ref was absent or repeated.');
  const selected = matches[0];
  if (selected.date !== sourceDate || selected.contentType !== source.contentType)
    fail('LINE_FORWARD_SOURCE_CHANGED', 'The source date or content type differs from the requested record.');
  if (sourceResult.messages.filter(message => visuallySame(selected, message)).length !== 1)
    fail('LINE_FORWARD_SOURCE_AMBIGUOUS', 'More than one source looks identical in the same minute.');
  const boundSource = sourceSummary(selected);

  // An identity-only read omits ownSenderRef in this reader. A one-row normal
  // read returns the profile sender without needing all recipient-day records.
  const recipientDate = day(startedAt);
  const recipientScope = { chatName: recipientChat.chatName, chatType: recipientChat.chatType,
    expectedChatRef: recipientChat.chatRef, requireUniqueName: true,
    dateFrom: recipientDate, dateTo: recipientDate, messageLimit: 1, mediaMode: 'metadata' };
  const recipientResult = await readIdentity(recipientScope);
  validateResult(recipientResult, recipientChat, instant(now()), { requireComplete: false });
  if (sourceResult.ownSenderRef !== recipientResult.ownSenderRef)
    fail('LINE_FORWARD_ACCOUNT_CHANGED', 'The signed-in LINE sender changed between source and recipient reads.');
  return {
    source: { ...sourceChat, ...boundSource },
    recipient: recipientChat,
    ownSenderRef: sourceResult.ownSenderRef,
    accountRef: sourceResult.ownSenderRef,
    uiAnchors: { latestSourceWindowComplete: includesLatest,
      messages: sourceResult.messages.slice(-3).map(message => ({
        sourceRef: message.sourceRef, senderRef: message.senderRef,
        date: message.date, time: message.time, sourceTimestamp: message.sourceTimestamp,
        contentType: message.contentType, text: message.contentType === 0 ? message.text : null,
        attachment: message.contentType === 0 ? null : attachmentMetadata(message),
      })) },
    scope: { source: sourceScope, recipient: recipientScope, boundAt: new Date(instant(now())).toISOString(),
      localSourceRefIsUiProof: false, attachmentByteIdentityVerified: false },
  };
}

function sameAttachment(source, candidate) {
  const expected = source.attachment;
  const observed = attachmentMetadata(candidate);
  if (!expected || !observed || !Number.isSafeInteger(expected.declaredFileBytes)
    || !Number.isSafeInteger(observed.declaredFileBytes)) return false;
  if (source.contentType === 14 && (!expected.fileName || !observed.fileName)) return false;
  const discriminator = Boolean(expected.fileName) || Object.hasOwn(expected, 'declaredDimensions');
  if (!discriminator || JSON.stringify(expected) !== JSON.stringify(observed)) return false;
  return true;
}

/** Pure local post-dispatch match. null means no verified receipt; never delivery proof. */
export function matchForwardReceipt(binding, before, after, { dispatchedAt, now = Date.now } = {}) {
  if (!record(binding) || !record(before) || !record(after)
    || !Number.isSafeInteger(dispatchedAt) || !Number.isSafeInteger(instant(now()))) return null;
  const recipient = binding.recipient;
  if (!record(recipient) || !SENDER_REF.test(binding.ownSenderRef ?? '')
    || before.chatRef !== recipient.chatRef || after.chatRef !== recipient.chatRef
    || before.chatIdentity?.kind !== recipient.chatType || after.chatIdentity?.kind !== recipient.chatType
    || before.chatName !== recipient.chatName || after.chatName !== recipient.chatName
    || before.ownSenderRef !== binding.ownSenderRef || after.ownSenderRef !== binding.ownSenderRef
    || before.scope?.kind !== 'local_database' || after.scope?.kind !== 'local_database'
    || !Array.isArray(before.messages) || !Array.isArray(after.messages)
    || after.freshness?.clockOrderValid !== true) return null;
  const latest = before.messages.at(-1);
  if (after.pagination?.hasMore !== false && after.pagination?.hasMore !== true) return null;
  if (before.pagination?.hasMore !== false && before.pagination?.hasMore !== true) return null;
  if (before.scope.truncated !== before.pagination.hasMore
    || after.scope.truncated !== after.pagination.hasMore) return null;
  if (after.pagination.hasMore && (!latest || !after.messages.some(message => message.sourceRef === latest.sourceRef)))
    return null;
  const beforeCapturedAt = Date.parse(before.freshness?.snapshotCapturedAt);
  const afterCapturedAt = Date.parse(after.freshness?.snapshotCapturedAt);
  if (!Number.isFinite(beforeCapturedAt) || !Number.isFinite(afterCapturedAt)
    || beforeCapturedAt > dispatchedAt || afterCapturedAt < dispatchedAt
    || dispatchedAt - beforeCapturedAt > MAX_SNAPSHOT_AGE_MS
    || instant(now()) - afterCapturedAt > MAX_SNAPSHOT_AGE_MS) return null;
  const baseline = new Set(before.messages.map(message => message.sourceRef));
  const matches = after.messages.filter(message => MESSAGE_REF.test(message?.sourceRef ?? '')
    && !baseline.has(message.sourceRef) && message.senderRef === binding.ownSenderRef
    && message.contentType === binding.source?.contentType
    && Number.isSafeInteger(message.sourceTimestamp)
    && message.sourceTimestamp >= dispatchedAt - 5_000
    && message.sourceTimestamp <= instant(now()) + 5_000
    && (message.contentType === 0
      ? message.text === binding.source.text
      : sameAttachment(binding.source, message)));
  if (matches.length !== 1) return null;
  const match = matches[0];
  return {
    sourceRef: match.sourceRef, sourceMessageId: match.sourceMessageId ?? null,
    sourceTimestamp: match.sourceTimestamp, contentType: match.contentType,
    strength: match.contentType === 0 ? 'exact_text_local_record' : 'attachment_metadata_local_record',
    localRecordVerified: true, deliveryVerified: false, attachmentByteIdentityVerified: false,
    limitation: match.contentType === 0 ? 'Local record presence does not prove delivery.'
      : 'Matching attachment metadata and local record presence do not prove byte identity or delivery.',
  };
}
