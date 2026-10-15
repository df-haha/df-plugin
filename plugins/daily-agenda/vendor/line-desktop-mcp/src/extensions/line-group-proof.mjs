/** One-use, bounded evidence for a named group LINE chat. No UI input or send. */
import { createHash, randomBytes } from 'node:crypto';
import { LineToolError, requireChat } from './line-runtime.mjs';

const TOKEN_TTL_MS = 120_000;
const RECEIPT_TTL_MS = 300_000;
const MAX_RECORDS = 32;
const MAX_CAPTURE_AGE_MS = 30_000;
const MAX_IMAGE_BYTES = 4 * 1024 * 1024;
const REF = /^chat:[0-9a-f]{24}$/u;
const MESSAGE_REF = /^message:[0-9a-f]{24}$/u;
const SENDER_REF = /^sender:[0-9a-f]{24}$/u;
const SHA256 = /^[0-9a-f]{64}$/u;
const DATE = /^\d{4}-\d{2}-\d{2}$/u;
const CLOCK = /^([01]?\d|2[0-3]):([0-5]\d)$/u;
const SOURCE_CLOCK = /^([01]\d|2[0-3]):([0-5]\d):([0-5]\d)$/u;

const fail = (code, message = 'The bounded LINE direct-chat proof was unavailable.') =>
  new LineToolError(code, message);
const isRecord = value => value !== null && typeof value === 'object' && !Array.isArray(value);

function exactName(value) {
  requireChat(value);
  if (/\p{Cc}/u.test(value)) throw fail('LINE_INVALID_ARGUMENT');
  return value;
}

function parseDate(value) {
  if (typeof value !== 'string' || !DATE.test(value)) throw fail('LINE_GROUP_PROOF_SCOPE');
  const parsed = Date.parse(`${value}T00:00:00Z`);
  if (!Number.isFinite(parsed) || new Date(parsed).toISOString().slice(0, 10) !== value) {
    throw fail('LINE_GROUP_PROOF_SCOPE');
  }
  return parsed;
}

function scopedRequest({ chatName, dateFrom, dateTo, messageLimit }) {
  const name = exactName(chatName);
  const first = parseDate(dateFrom);
  const last = parseDate(dateTo);
  if (last < first || last - first > 2 * 86_400_000
      || !Number.isInteger(messageLimit) || messageLimit < 2 || messageLimit > 30) {
    throw fail('LINE_GROUP_PROOF_SCOPE');
  }
  return Object.freeze({ chatName: name, chatType: 'group', dateFrom, dateTo,
    messageLimit, mediaMode: 'metadata' });
}

function sameScope(left, right) {
  return left?.chatName === right?.chatName && left?.chatType === right?.chatType
    && left?.dateFrom === right?.dateFrom && left?.dateTo === right?.dateTo
    && left?.messageLimit === right?.messageLimit && left?.mediaMode === right?.mediaMode;
}

function candidateIdentity(value, chatName) {
  const identity = value?.chatIdentity;
  const requested = value?.scope?.requested;
  if (!isRecord(value) || value.ok !== true || value.chatName !== chatName || !REF.test(value.chatRef ?? '')
      || value.count !== 0 || !Array.isArray(value.messages) || value.messages.length !== 0
      || value.scope?.kind !== 'local_gui_group_candidate_identity' || value.scope?.truncated !== false
      || !isRecord(requested) || requested.chatName !== chatName || requested.chatType !== 'group'
      || requested.identityOnly !== true || requested.groupCandidateOnly !== true
      || !isRecord(identity) || Object.keys(identity).sort().join(',') !==
        'displayName,guiDisplayNameUnique,kind,knownNameUnique,uiIdentityVerified'
      || identity.kind !== 'group' || identity.displayName !== chatName
      || identity.uiIdentityVerified !== false || identity.guiDisplayNameUnique !== false
      || identity.knownNameUnique !== true) {
    throw fail('LINE_GROUP_PROOF_CANDIDATE');
  }
  return Object.freeze({ chatRef: value.chatRef, kind: 'group', knownNameUnique: true,
    guiDisplayNameUnique: false });
}

function target(value) {
  if (!isRecord(value) || !Number.isInteger(value.pid) || value.pid <= 0
      || !Number.isInteger(value.window_id) || value.window_id <= 0) {
    throw fail('LINE_GROUP_PROOF_TARGET');
  }
  return Object.freeze({ pid: value.pid, window_id: value.window_id });
}
const sameTarget = (a, b) => a?.pid === b?.pid && a?.window_id === b?.window_id;

function windowSnapshot(value, expectedTarget) {
  const bounds = value?.bounds;
  if (!isRecord(value) || value.app_name !== 'LINE.exe' || value.title !== 'LINE'
      || value.is_on_screen !== true || value.minimized === true
      || !sameTarget(value, expectedTarget) || !isRecord(bounds)
      || ![bounds.x, bounds.y, bounds.width, bounds.height].every(Number.isFinite)
      || bounds.width < 1 || bounds.height < 1) throw fail('LINE_GROUP_PROOF_WINDOW');
  return Object.freeze({ app_name: 'LINE.exe', title: 'LINE', pid: value.pid,
    window_id: value.window_id, is_on_screen: true, minimized: false,
    bounds: Object.freeze({ x: bounds.x, y: bounds.y, width: bounds.width, height: bounds.height }) });
}

function sameWindow(a, b) {
  return sameTarget(a, b) && a?.title === b?.title && a?.app_name === b?.app_name
    && ['x', 'y', 'width', 'height'].every(key => a?.bounds?.[key] === b?.bounds?.[key]);
}

function fingerprint(value) {
  const area = value?.region;
  if (!isRecord(value) || !SHA256.test(value.sha256 ?? '') || !isRecord(area)
      || ![area.x, area.y, area.width, area.height].every(Number.isSafeInteger)
      || area.x < 0 || area.y < 0 || area.width < 1 || area.height < 1
      || value.width !== area.width || value.height !== area.height) {
    throw fail('LINE_GROUP_PROOF_FINGERPRINT');
  }
  return Object.freeze({ sha256: value.sha256, width: value.width, height: value.height,
    region: Object.freeze({ x: area.x, y: area.y, width: area.width, height: area.height }) });
}

function sameFingerprint(a, b) {
  return a?.sha256 === b?.sha256 && a?.width === b?.width && a?.height === b?.height
    && ['x', 'y', 'width', 'height'].every(key => a?.region?.[key] === b?.region?.[key]);
}

function image(value) {
  if (!isRecord(value) || value.type !== 'image' || value.mimeType !== 'image/png'
      || typeof value.data !== 'string' || !/^[A-Za-z0-9+/]+={0,2}$/u.test(value.data)
      || value.data.length > Math.ceil(MAX_IMAGE_BYTES * 4 / 3) + 4) {
    throw fail('LINE_GROUP_PROOF_IMAGE');
  }
  const bytes = Buffer.from(value.data, 'base64');
  if (bytes.length < 24 || bytes.length > MAX_IMAGE_BYTES
      || !bytes.subarray(0, 8).equals(Buffer.from([137, 80, 78, 71, 13, 10, 26, 10]))
      || bytes.readUInt32BE(8) !== 13 || bytes.toString('ascii', 12, 16) !== 'IHDR') {
    throw fail('LINE_GROUP_PROOF_IMAGE');
  }
  return value;
}

function point(value, region) {
  if (!isRecord(value) || !Number.isFinite(value.x) || !Number.isFinite(value.y)
      || value.x < region.x || value.y < region.y
      || value.x >= region.x + region.width || value.y >= region.y + region.height) {
    throw fail('LINE_GROUP_PROOF_SELECTOR');
  }
  return Object.freeze({ x: value.x, y: value.y });
}

function capturedAt(value, nowMs) {
  const timestamp = value instanceof Date ? value.valueOf()
    : typeof value === 'number' ? value : typeof value === 'string' ? Date.parse(value) : NaN;
  if (!Number.isFinite(timestamp) || timestamp > nowMs + 5_000
      || nowMs - timestamp > MAX_CAPTURE_AGE_MS) throw fail('LINE_GROUP_PROOF_STALE');
  return timestamp;
}

function currentTime(now) {
  const value = now();
  const ms = value instanceof Date ? value.valueOf() : value;
  if (!Number.isFinite(ms)) throw fail('LINE_GROUP_PROOF_CLOCK');
  return ms;
}

function confidence(observation) {
  if (!isRecord(observation) || observation.confidence !== 'high') {
    throw fail('LINE_GROUP_PROOF_OBSERVATION');
  }
}

function exactSearchObservation(observation, chatName) {
  confidence(observation);
  if (observation.chatResultCount !== 1 || !Array.isArray(observation.resultTitles)
      || observation.resultTitles.length !== 1 || observation.resultTitles[0] !== chatName) {
    throw fail('LINE_GROUP_PROOF_SEARCH');
  }
}

function exactHeaderObservation(observation, chatName) {
  confidence(observation);
  if (observation.headerTitle !== chatName || !Number.isSafeInteger(observation.memberCount)
      || observation.memberCount < 1 || observation.memberCount > 100_000) {
    throw fail('LINE_GROUP_PROOF_HEADER');
  }
  return observation.memberCount;
}

function taipeiDate(timestamp) {
  return new Date(timestamp + 28_800_000).toISOString().slice(0, 10);
}

function resolveDateLabel(label, observedAt) {
  if (label === '今天') return taipeiDate(observedAt);
  if (label === '昨天') return taipeiDate(observedAt - 86_400_000);
  parseDate(label);
  return label;
}

function normalizedText(value) {
  if (typeof value !== 'string' || value.length > 1_000_000) throw fail('LINE_GROUP_PROOF_HISTORY');
  return value.replace(/\r\n?/gu, '\n').normalize('NFC');
}

function meaningfulLength(value) {
  return [...value].filter(char => !/\s/u.test(char)).length;
}

function canonicalMessage(value, scope) {
  const sourceDate = Number.isSafeInteger(value?.sourceTimestamp)
    ? new Date(value.sourceTimestamp + 28_800_000) : null;
  if (!isRecord(value) || !MESSAGE_REF.test(value.sourceRef ?? '')
      || !Number.isSafeInteger(value.sourceTimestamp)
      || !sourceDate || !Number.isFinite(sourceDate.valueOf())
      || typeof value.date !== 'string' || !DATE.test(value.date)
      || !SOURCE_CLOCK.test(value.time ?? '')
      || sourceDate.toISOString().slice(0, 10) !== value.date
      || sourceDate.toISOString().slice(11, 19) !== value.time
      || value.date < scope.dateFrom || value.date > scope.dateTo
      || (value.senderRef !== null && !SENDER_REF.test(value.senderRef ?? ''))
      || (value.sender !== null && (typeof value.sender !== 'string' || value.sender.length > 200))
      || !Number.isInteger(value.contentType) || value.contentType < 0
      || (value.text !== null && typeof value.text !== 'string')) {
    throw fail('LINE_GROUP_PROOF_HISTORY');
  }
  return {
    sourceRef: value.sourceRef, senderRef: value.senderRef, sender: value.sender,
    text: value.text === null ? null : normalizedText(value.text),
    date: value.date, time: value.time, sourceTimestamp: value.sourceTimestamp,
    contentType: value.contentType,
    sourceStatus: value.sourceStatus ?? null, sourceRevision: value.sourceRevision ?? null,
  };
}

function scopedHistory(local, expectedScope, expectedChatRef) {
  if (!isRecord(local) || local.chatName !== expectedScope.chatName || local.chatRef !== expectedChatRef
      || local.chatIdentity?.kind !== 'group' || !SENDER_REF.test(local.ownSenderRef ?? '')
      || local.scope?.kind !== 'local_database'
      || !isRecord(local.scope.requested) || !sameScope(local.scope.requested, expectedScope)
      || local.scope.requested.query !== undefined || local.scope.requested.cursor !== undefined
      || local.scope.requested.mediaSourceRefs !== undefined
      || local.scope.requested.identityOnly !== undefined
      || local.scope.requested.guiIdentityOnly !== undefined
      || local.scope.requested.guiCandidateOnly !== undefined
      || local.scope.requested.groupCandidateOnly !== undefined
      || !Array.isArray(local.messages) || local.count !== local.messages.length
      || local.messages.length > expectedScope.messageLimit
      || !isRecord(local.pagination) || typeof local.pagination.hasMore !== 'boolean') {
    throw fail('LINE_GROUP_PROOF_HISTORY');
  }
  const records = local.messages.map(message => canonicalMessage(message, expectedScope));
  if (records.some((item, index) => index > 0 && item.sourceTimestamp < records[index - 1].sourceTimestamp)
      || new Set(records.map(item => item.sourceRef)).size !== records.length) {
    throw fail('LINE_GROUP_PROOF_HISTORY');
  }
  return records;
}

/** Stable digest over only authorized scoped local records, excluding snapshot times. */
export function fingerprintScopedHistory(local) {
  const scope = scopedRequest(local?.scope?.requested ?? {});
  if (!REF.test(local?.chatRef ?? '')) throw fail('LINE_GROUP_PROOF_HISTORY');
  const messages = scopedHistory(local, scope, local.chatRef);
  return createHash('sha256').update(JSON.stringify({ chatRef: local.chatRef, ownSenderRef: local.ownSenderRef,
    scope, messages })).digest('hex');
}

/** Compare both newest text anchors and every later local record in visible order. */
export function validateContextObservation(observation, {
  chatName, chatRef, ownSenderRef, messages, capturedAt: observedAt, memberCount,
}) {
  const observedMemberCount = exactHeaderObservation(observation, chatName);
  if (!REF.test(chatRef ?? '') || !SENDER_REF.test(ownSenderRef ?? '')
      || (memberCount !== undefined && memberCount !== observedMemberCount)
      || !Array.isArray(messages) || messages.length < 2 || messages.length > 30
      || !Number.isFinite(observedAt) || !Array.isArray(observation.entries)
      || observation.entries.length < 2 || observation.entries.length > 30) {
    throw fail('LINE_GROUP_PROOF_CONTEXT');
  }
  const textIndexes = messages.flatMap((item, index) => item.contentType === 0 ? [index] : []);
  if (textIndexes.length < 2) throw fail('LINE_GROUP_PROOF_CONTEXT');
  const anchorIndexes = textIndexes.slice(-2);
  const anchorsLocal = anchorIndexes.map(index => messages[index]);
  if (anchorsLocal.some(item => typeof item.text !== 'string' || meaningfulLength(item.text) < 8)
      || anchorsLocal[0].text === anchorsLocal[1].text
      || anchorsLocal[0].sourceRef === anchorsLocal[1].sourceRef) {
    throw fail('LINE_GROUP_PROOF_CONTEXT');
  }
  const suffix = messages.slice(anchorIndexes[0]);
  if (observation.entries.length < suffix.length) throw fail('LINE_GROUP_PROOF_CONTEXT');
  const visible = observation.entries.map(item => {
    if (!isRecord(item) || Object.keys(item).sort().join(',') !==
        'dateLabel,direction,kind,senderName,text,time'
        || !['text', 'nontext'].includes(item.kind)
        || !['incoming', 'outgoing', 'unknown'].includes(item.direction)
        || typeof item.text !== 'string' || item.text.length > 12_000
        || (item.senderName !== null && (typeof item.senderName !== 'string'
          || item.senderName.length > 200))) throw fail('LINE_GROUP_PROOF_CONTEXT');
    const clock = typeof item.time === 'string' ? CLOCK.exec(item.time) : null;
    if (item.time !== null && !clock) throw fail('LINE_GROUP_PROOF_CONTEXT');
    let day = null;
    if (item.dateLabel !== null) {
      try { day = resolveDateLabel(item.dateLabel, observedAt); }
      catch { throw fail('LINE_GROUP_PROOF_CONTEXT'); }
    }
    return { kind: item.kind, text: normalizedText(item.text),
      senderName: item.senderName === null ? null : normalizedText(item.senderName),
      direction: item.direction, date: day,
      minute: clock ? `${clock[1].padStart(2, '0')}:${clock[2]}` : null };
  });
  const seenNames = new Map();
  for (const item of messages) {
    if (item.senderRef === ownSenderRef) continue;
    if (!SENDER_REF.test(item.senderRef ?? '') || typeof item.sender !== 'string'
        || !item.sender || item.sender.length > 200) throw fail('LINE_GROUP_PROOF_SENDER');
    const alias = normalizedText(item.sender);
    if (seenNames.has(alias) && seenNames.get(alias) !== item.senderRef) {
      throw fail('LINE_GROUP_PROOF_SENDER');
    }
    seenNames.set(alias, item.senderRef);
  }
  const tail = visible.slice(-suffix.length);
  const keys = new Set();
  const anchors = [];
  let accountedNontextCount = 0;
  for (let index = 0; index < suffix.length; index += 1) {
    const expected = suffix[index], observed = tail[index];
    const kind = expected.contentType === 0 ? 'text' : 'nontext';
    const direction = expected.senderRef === ownSenderRef ? 'outgoing' : 'incoming';
    const expectedSender = direction === 'outgoing' ? null : normalizedText(expected.sender);
    if (observed.kind !== kind || observed.direction !== direction
        || observed.senderName !== expectedSender || observed.date !== expected.date
        || observed.minute !== expected.time.slice(0, 5)
        || (kind === 'text' ? observed.text !== expected.text : observed.text !== '')) {
      throw fail('LINE_GROUP_PROOF_CONTEXT');
    }
    const key = JSON.stringify([observed.kind, observed.text, observed.senderName,
      observed.date, observed.minute, observed.direction]);
    if (keys.has(key) || visible.slice(0, -suffix.length).some(previous =>
      previous.kind === observed.kind && previous.text === observed.text
      && (previous.senderName === null || previous.senderName === observed.senderName)
      && (previous.direction === 'unknown' || previous.direction === observed.direction)
      && (previous.date === null || previous.date === observed.date)
      && (previous.minute === null || previous.minute === observed.minute))) {
      throw fail('LINE_GROUP_PROOF_CONTEXT');
    }
    keys.add(key);
    if (kind === 'nontext') accountedNontextCount += 1;
    else anchors.push(Object.freeze({ sourceRef: expected.sourceRef, senderRef: expected.senderRef,
      text: expected.text, date: expected.date, minute: expected.time.slice(0, 5), direction }));
  }
  if (anchors.length !== 2) throw fail('LINE_GROUP_PROOF_CONTEXT');
  return Object.freeze({ anchors: Object.freeze(anchors), accountedNontextCount,
    suffixLength: suffix.length, memberCount: observedMemberCount });
}

export class GroupChatProof {
  #tokens = new Map();
  #verified = new Map();
  #receipts = new Map();

  constructor({ readCandidate, readMessages, now = () => Date.now(),
    randomToken = () => randomBytes(24).toString('base64url'), capture, select, search } = {}) {
    if ([readCandidate, readMessages, now, randomToken, capture, select, search]
      .some(callback => typeof callback !== 'function')) {
      throw new TypeError('GroupChatProof requires seven injected functions.');
    }
    this.readCandidate = readCandidate;
    this.readMessages = readMessages;
    this.now = now;
    this.randomToken = randomToken;
    this.capture = capture;
    this.select = select;
    this.search = search;
  }

  #prune(nowMs) {
    for (const [token, item] of this.#tokens) if (item.expiresAt <= nowMs) this.#tokens.delete(token);
    for (const [chatName, item] of this.#verified) if (item.expiresAt <= nowMs) this.#verified.delete(chatName);
    for (const [chatName, item] of this.#receipts) if (item.expiresAt <= nowMs) this.#receipts.delete(chatName);
  }

  #issue(phase, value) {
    const nowMs = currentTime(this.now);
    this.#prune(nowMs);
    if (this.#tokens.size >= MAX_RECORDS) throw fail('LINE_GROUP_PROOF_CAPACITY');
    const token = this.randomToken();
    if (typeof token !== 'string' || !/^[A-Za-z0-9_-]{16,128}$/u.test(token)
        || this.#tokens.has(token)) throw fail('LINE_GROUP_PROOF_TOKEN');
    const expiresAt = nowMs + TOKEN_TTL_MS;
    this.#tokens.set(token, Object.freeze({ ...value, phase, expiresAt }));
    return { token, expiresAt: new Date(expiresAt).toISOString() };
  }

  #take(token, phase) {
    const item = this.#tokens.get(token);
    this.#tokens.delete(token); // Wrong phase and failed observation consume a live token.
    if (!item || item.phase !== phase || item.expiresAt <= currentTime(this.now)) {
      throw fail('LINE_GROUP_PROOF_TOKEN');
    }
    return item;
  }

  invalidate(chatName) {
    exactName(chatName);
    for (const [token, item] of this.#tokens) if (item.chatName === chatName) this.#tokens.delete(token);
    this.#verified.delete(chatName);
    this.#receipts.delete(chatName);
  }

  getVerified(chatName) {
    exactName(chatName);
    this.#prune(currentTime(this.now));
    return this.#verified.get(chatName) ?? null;
  }

  markDispatched(chatName, message) {
    exactName(chatName);
    if (typeof message !== 'string' || !message.trim() || message.length > 10_000) {
      throw fail('LINE_GROUP_PROOF_DISPATCH');
    }
    const verified = this.getVerified(chatName);
    if (!verified || this.#receipts.size >= MAX_RECORDS) throw fail('LINE_GROUP_PROOF_DISPATCH');
    this.#verified.delete(chatName);
    const nowMs = currentTime(this.now);
    const receipt = Object.freeze({ ...verified, messageDigest: createHash('sha256')
      .update(message).digest('hex'), dispatchedAt: nowMs, expiresAt: nowMs + RECEIPT_TTL_MS });
    this.#receipts.set(chatName, receipt);
    return Object.freeze({ chatName, receiptAvailable: true,
      expiresAt: new Date(receipt.expiresAt).toISOString(), deliveryVerified: false });
  }

  async dispatch({ chatName, stage, dateFrom, dateTo, messageLimit, token, observation } = {}) {
    if (stage === 'search') {
      const scope = scopedRequest({ chatName, dateFrom, dateTo, messageLimit });
      this.invalidate(chatName);
      const identity = candidateIdentity(await this.readCandidate({ chatName }), chatName);
      const found = await this.search(chatName);
      const selectedTarget = target(found?.target);
      const window = windowSnapshot(found?.window, selectedTarget);
      const searchFingerprint = fingerprint(found?.searchFingerprint);
      const searchImage = image(found?.searchImage);
      const rowPoint = point(found?.rowPoint, searchFingerprint.region);
      const stamp = capturedAt(found?.capturedAt, currentTime(this.now));
      const next = this.#issue('open', { chatName, scope, identity, target: selectedTarget,
        window, searchFingerprint, rowPoint, capturedAt: stamp });
      return { phase: 'search', chatName, ...next, capturedAt: new Date(stamp).toISOString(),
        images: [searchImage], knownNameUnique: true, chatType: 'group' };
    }
    if (stage === 'receipt') return this.#receipt(chatName);
    const previous = this.#take(token, stage);
    if (exactName(chatName) !== previous.chatName) throw fail('LINE_GROUP_PROOF_TOKEN');
    if (dateFrom !== undefined || dateTo !== undefined || messageLimit !== undefined) {
      const supplied = scopedRequest({ chatName, dateFrom, dateTo, messageLimit });
      if (!sameScope(previous.scope, supplied)) throw fail('LINE_GROUP_PROOF_SCOPE');
    }
    if (stage === 'open') {
      exactSearchObservation(observation, chatName);
      const opened = await this.select(previous);
      const selectedTarget = target(opened?.target);
      const window = windowSnapshot(opened?.window, selectedTarget);
      if (!sameTarget(previous.target, selectedTarget) || !sameWindow(previous.window, window)) {
        throw fail('LINE_GROUP_PROOF_TARGET');
      }
      const headerFingerprint = fingerprint(opened?.headerFingerprint);
      const headerImage = image(opened?.headerImage);
      const stamp = capturedAt(opened?.capturedAt, currentTime(this.now));
      const next = this.#issue('context', { ...previous, target: selectedTarget, window,
        headerFingerprint, capturedAt: stamp });
      return { phase: 'header', chatName, ...next, capturedAt: new Date(stamp).toISOString(),
        images: [headerImage] };
    }
    if (stage === 'context') {
      const memberCount = exactHeaderObservation(observation, chatName);
      const initialIdentity = candidateIdentity(await this.readCandidate({ chatName }), chatName);
      if (initialIdentity.chatRef !== previous.identity.chatRef) {
        throw fail('LINE_GROUP_PROOF_IDENTITY_CHANGED');
      }
      const initialLocal = await this.readMessages(previous.scope);
      scopedHistory(initialLocal, previous.scope, previous.identity.chatRef);
      const localFingerprint = fingerprintScopedHistory(initialLocal);
      const checkLocal = async () => {
        if (previous.expiresAt <= currentTime(this.now)) throw fail('LINE_GROUP_PROOF_TOKEN');
        // These are independent read-only scopes. Await both even if one
        // rejects so no reader survives a failed guard into the next capture.
        const [candidateResult, messagesResult] = await Promise.allSettled([
          Promise.resolve().then(() => this.readCandidate({ chatName })),
          Promise.resolve().then(() => this.readMessages(previous.scope)),
        ]);
        if (candidateResult.status === 'rejected') throw candidateResult.reason;
        const identity = candidateIdentity(candidateResult.value, chatName);
        if (identity.chatRef !== previous.identity.chatRef) {
          throw fail('LINE_GROUP_PROOF_IDENTITY_CHANGED');
        }
        if (messagesResult.status === 'rejected') throw messagesResult.reason;
        const local = messagesResult.value;
        scopedHistory(local, previous.scope, previous.identity.chatRef);
        if (local.ownSenderRef !== initialLocal.ownSenderRef
            || fingerprintScopedHistory(local) !== localFingerprint) {
          throw fail('LINE_GROUP_PROOF_HISTORY');
        }
        if (previous.expiresAt <= currentTime(this.now)) throw fail('LINE_GROUP_PROOF_TOKEN');
      };
      const captured = await this.capture(previous, 'context', checkLocal);
      const observed = this.#assertCapture(captured, previous, true);
      const contextImage = image(captured?.contextImage);
      await checkLocal();
      if (previous.expiresAt <= currentTime(this.now)) throw fail('LINE_GROUP_PROOF_TOKEN');
      const next = this.#issue('verify', { ...previous,
        bodyFingerprint: observed.bodyFingerprint, memberCount,
        capturedAt: observed.capturedAt });
      return { phase: 'context', chatName, ...next,
        capturedAt: new Date(observed.capturedAt).toISOString(), images: [contextImage] };
    }
    if (stage === 'verify') {
      const freshIdentity = candidateIdentity(await this.readCandidate({ chatName }), chatName);
      if (freshIdentity.chatRef !== previous.identity.chatRef) throw fail('LINE_GROUP_PROOF_IDENTITY_CHANGED');
      const local = await this.readMessages(previous.scope);
      const records = scopedHistory(local, previous.scope, previous.identity.chatRef);
      const matched = validateContextObservation(observation, {
        chatName, chatRef: previous.identity.chatRef, ownSenderRef: local.ownSenderRef,
        messages: records, capturedAt: previous.capturedAt, memberCount: previous.memberCount,
      });
      const localFingerprint = fingerprintScopedHistory(local);
      const captured = await this.capture(previous, 'verify');
      this.#assertCapture(captured, previous, true, true);
      // A bounded visual reacquisition can outlive the local evidence or the
      // one-use token. Re-read both before creating a verified send record.
      if (previous.expiresAt <= currentTime(this.now)) throw fail('LINE_GROUP_PROOF_TOKEN');
      const finalIdentity = candidateIdentity(await this.readCandidate({ chatName }), chatName);
      if (finalIdentity.chatRef !== previous.identity.chatRef) {
        throw fail('LINE_GROUP_PROOF_IDENTITY_CHANGED');
      }
      const finalLocal = await this.readMessages(previous.scope);
      scopedHistory(finalLocal, previous.scope, previous.identity.chatRef);
      if (finalLocal.ownSenderRef !== local.ownSenderRef
          || fingerprintScopedHistory(finalLocal) !== localFingerprint) {
        throw fail('LINE_GROUP_PROOF_HISTORY');
      }
      const nowMs = currentTime(this.now);
      if (previous.expiresAt <= nowMs) throw fail('LINE_GROUP_PROOF_TOKEN');
      this.#prune(nowMs);
      if (this.#verified.size >= MAX_RECORDS && !this.#verified.has(chatName)) {
        throw fail('LINE_GROUP_PROOF_CAPACITY');
      }
      const verified = Object.freeze({ chatName, identity: finalIdentity, scope: previous.scope,
        target: previous.target, window: previous.window,
        headerFingerprint: previous.headerFingerprint, bodyFingerprint: previous.bodyFingerprint,
        localFingerprint, ownSenderRef: local.ownSenderRef, anchors: matched.anchors,
        memberCount: matched.memberCount, accountedNontextCount: matched.accountedNontextCount,
        verifiedAt: nowMs, expiresAt: nowMs + TOKEN_TTL_MS });
      this.#verified.set(chatName, verified);
      return { phase: 'verified', chatName, chatType: 'group', verification: 'bounded-group-context',
        matchedCount: 2, accountedNontextCount: matched.accountedNontextCount,
        expiresAt: new Date(verified.expiresAt).toISOString(),
        deliveryVerified: false };
    }
    throw fail('LINE_GROUP_PROOF_STAGE');
  }

  #assertCapture(value, previous, requireBody, sameBody = false) {
    const observedTarget = target(value?.target);
    const window = windowSnapshot(value?.window, observedTarget);
    const headerFingerprint = fingerprint(value?.headerFingerprint);
    if (!sameTarget(previous.target, observedTarget) || !sameWindow(previous.window, window)
        || !sameFingerprint(previous.headerFingerprint, headerFingerprint)) {
      throw fail('LINE_GROUP_PROOF_STALE');
    }
    const bodyFingerprint = requireBody ? fingerprint(value?.bodyFingerprint) : undefined;
    if (sameBody && !sameFingerprint(previous.bodyFingerprint, bodyFingerprint)) {
      throw fail('LINE_GROUP_PROOF_STALE');
    }
    return { bodyFingerprint, capturedAt: capturedAt(value?.capturedAt, currentTime(this.now)) };
  }

  async #receipt(chatName) {
    exactName(chatName);
    this.#prune(currentTime(this.now));
    const previous = this.#receipts.get(chatName);
    if (!previous) throw fail('LINE_GROUP_PROOF_RECEIPT');
    const freshIdentity = candidateIdentity(await this.readCandidate({ chatName }), chatName);
    if (freshIdentity.chatRef !== previous.identity.chatRef) throw fail('LINE_GROUP_PROOF_IDENTITY_CHANGED');
    const captured = await this.capture(previous, 'receipt');
    const observed = this.#assertCapture(captured, previous, false);
    const receiptImage = image(captured?.receiptImage);
    return { phase: 'receipt', chatName, deliveryVerified: false,
      capturedAt: new Date(observed.capturedAt).toISOString(), images: [receiptImage],
      verification: 'same-group-window-and-header-after-dispatch' };
  }
}
