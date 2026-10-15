import { LineToolError, requireChat } from './line-runtime.mjs';

const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/u;
const LOCAL_TIME = /^(\d{2}):(\d{2}):(\d{2})$/u;
const UI_TIME = /^(\d{2}):(\d{2})$/u;

/**
 * Conservatively compare a local LINE database result with a copied, bounded
 * UI history window. This is content corroboration only: it never proves the
 * current visible bubble, UI action identity, delivery, or complete history.
 */
export function reconcileLineSources(local, ui) {
  const localSource = validateLocalSource(local);
  const uiSource = validateUiSource(ui, localSource);
  const classified = classifyLocalMessages(localSource.messages);
  const classifiedUi = classifyUiMessages(uiSource.messages, localSource.scope);
  const graph = buildCandidateGraph(classified.qualified, classifiedUi.candidatesInDateScope);
  const resolved = resolveCandidateGraph(graph, classified.qualified);

  const matches = resolved.matches.map(match => ({
    sourceRef: match.local.sourceRef,
    uiIndex: match.ui.uiIndex,
    matchBasis: match.edge.matchBasis,
    senderEvidence: match.edge.senderEvidence,
  }));
  const matchedUiIndexes = new Set(matches.map(match => match.uiIndex));
  const qualifiedTextUiIndexes = new Set([
    ...classifiedUi.qualified.map(message => message.uiIndex),
    ...matchedUiIndexes,
  ]);
  const qualifiedUiInDateScopeIndexes = new Set([
    ...classifiedUi.qualifiedInDateScope.map(message => message.uiIndex),
    ...matchedUiIndexes,
  ]);
  const notObservedSourceRefs = resolved.notObserved.map(message => message.sourceRef);
  const localOnlySourceRefs = [...notObservedSourceRefs];
  const uiDifferences = classifyZeroCandidateUiMessages(classifiedUi.candidates, graph, localSource.scope);
  const { uiOnly, uiUnclassified } = uiDifferences;
  const uiOnlyIndexes = uiOnly.map(message => message.uiIndex);
  const uiUnclassifiedIndexes = uiUnclassified.map(message => message.uiIndex);
  const ambiguousSourceRefCount = new Set(resolved.ambiguities.flatMap(item => item.sourceRefs)).size;
  const senderUnverifiedCount = matches.filter(match => match.senderEvidence === 'sender_unverified').length;
  const uiOnlyComparableCount = uiOnly.filter(message => message.comparable).length;

  return {
    success: true,
    comparison: {
      kind: 'local_database_vs_loaded_history_window_content_reconciliation',
      localRetrievedAt: localSource.retrievedAt,
      uiRetrievedAt: uiSource.retrievedAt,
      chatName: localSource.chatName,
      dateFrom: localSource.scope.requested.dateFrom,
      dateTo: localSource.scope.requested.dateTo,
    },
    localRetrievedAt: localSource.retrievedAt,
    uiRetrievedAt: uiSource.retrievedAt,
    counts: {
      localMessages: localSource.messages.length,
      uiMessages: uiSource.messages.length,
      qualifiedTextLocal: classified.qualified.length,
      typedTextUi: classifiedUi.qualified.length,
      typedTextUiInDateScope: classifiedUi.qualifiedInDateScope.length,
      candidateUi: classifiedUi.candidates.length,
      candidateUiInDateScope: classifiedUi.candidatesInDateScope.length,
      qualifiedTextUi: qualifiedTextUiIndexes.size,
      qualifiedUiInDateScope: qualifiedUiInDateScopeIndexes.size,
      uiOutsideDateScope: classifiedUi.outsideDateScope,
      qualifiedUiOutsideDateScope: classifiedUi.qualifiedOutsideDateScope,
      qualifiedTextMatches: matches.length,
      fullyCorroborated: matches.length - senderUnverifiedCount,
      senderUnverified: senderUnverifiedCount,
      ambiguous: resolved.ambiguities.length,
      ambiguousSourceRefs: ambiguousSourceRefCount,
      notObserved: notObservedSourceRefs.length,
      localOnly: localOnlySourceRefs.length,
      uiOnly: uiOnly.length,
      uiOnlyComparable: uiOnlyComparableCount,
      uiOnlyNonComparable: uiOnly.length - uiOnlyComparableCount,
      uiUnclassified: uiUnclassified.length,
      nontext: classified.nontext.length,
      uiNontext: classifiedUi.nontext.length,
    },
    matches,
    ambiguities: resolved.ambiguities,
    notObservedSourceRefs,
    // Alias retained separately so consumers can use directional naming without
    // changing the bounded-window meaning of the original field.
    localOnlySourceRefs,
    uiOnly,
    uiOnlyIndexes,
    uiUnclassified,
    uiUnclassifiedIndexes,
    nontext: classified.nontext,
    uiNontext: classifiedUi.nontext,
    scopeComparability: {
      localQueryApplied: localSource.scope.requested.query !== undefined,
      localPageLimited: localSource.scope.pageLimited,
      uiHistoryWindowBounded: true,
    },
    timing: buildTimingEvidence(localSource, uiSource),
    uiActionIdentityVerified: false,
    totalHistoryKnown: false,
    deliveryVerified: false,
    note: 'This is exact content corroboration within one bounded UI history window. Directional unmatched rows and unclassified UI evidence do not establish local database incompleteness, current bubble identity, a complete archive, or delivery.',
  };
}

function validateLocalSource(value) {
  const local = requireRecord(value, 'local source');
  requireExactChat(local.chatName, 'local.chatName');
  requireRetrievedAt(local.retrievedAt, 'local.retrievedAt');
  const scope = requireRecord(local.scope, 'local.scope');
  if (scope.kind !== 'local_database') fail();
  const requested = requireRecord(scope.requested, 'local.scope.requested');
  requireExactChat(requested.chatName, 'local.scope.requested.chatName');
  if (requested.chatName !== local.chatName) fail();
  const dateFrom = requireDate(requested.dateFrom, 'local.scope.requested.dateFrom');
  const dateTo = requireDate(requested.dateTo, 'local.scope.requested.dateTo');
  if (dateFrom > dateTo || !Number.isInteger(requested.messageLimit) || requested.messageLimit < 1 || requested.messageLimit > 1000) fail();
  const query = optionalScopeText(requested.query, 1000);
  const cursor = optionalScopeText(requested.cursor, 2048);
  if (!Array.isArray(local.messages)) fail();

  const sourceRefs = new Set();
  const messages = local.messages.map((message, index) => validateLocalMessage(message, index, { dateFrom, dateTo }, sourceRefs));
  return {
    chatName: local.chatName,
    retrievedAt: local.retrievedAt,
    scope: {
      requested: { chatName: requested.chatName, dateFrom, dateTo, messageLimit: requested.messageLimit, query, cursor },
      pageLimited: localPageIsLimited(local, scope, cursor),
      snapshotCaptureCompletedAt: snapshotCaptureCompletedAt(scope.snapshot),
    },
    messages,
  };
}

function validateUiSource(value, local) {
  const ui = requireRecord(value, 'ui source');
  requireExactChat(ui.chatName, 'ui.chatName');
  if (ui.chatName !== local.chatName) fail();
  requireRetrievedAt(ui.retrievedAt, 'ui.retrievedAt');
  const scope = requireRecord(ui.scope, 'ui.scope');
  if (scope.kind !== 'loaded_history_window' || !Array.isArray(ui.messages)) fail();
  return {
    chatName: ui.chatName,
    retrievedAt: ui.retrievedAt,
    messages: ui.messages.map(validateUiMessage),
  };
}

function validateLocalMessage(value, index, dateScope, sourceRefs) {
  const message = requireRecord(value, `local.messages[${index}]`);
  if (typeof message.sourceRef !== 'string' || !message.sourceRef || message.sourceRef.includes('\0') || sourceRefs.has(message.sourceRef)) fail();
  sourceRefs.add(message.sourceRef);
  const date = requireDate(message.date, `local.messages[${index}].date`);
  if (date < dateScope.dateFrom || date > dateScope.dateTo) fail();
  const time = requireTime(message.time, LOCAL_TIME, `local.messages[${index}].time`);
  if (message.sender !== null && typeof message.sender !== 'string') fail();
  if (!isContentType(message.contentType)) fail();
  if (isTextContentType(message.contentType) && typeof message.text !== 'string') fail();
  return {
    sourceRef: message.sourceRef,
    date,
    time,
    minute: time.slice(0, 5),
    sender: message.sender,
    text: typeof message.text === 'string' ? normalizeLineEndings(message.text) : null,
    contentType: message.contentType,
  };
}

function validateUiMessage(value, index) {
  const message = requireRecord(value, `ui.messages[${index}]`);
  const date = requireDate(message.date, `ui.messages[${index}].date`);
  const time = requireTime(message.time, UI_TIME, `ui.messages[${index}].time`);
  if (message.sender !== null && typeof message.sender !== 'string') fail();
  if (typeof message.text !== 'string' || typeof message.kind !== 'string') fail();
  return {
    uiIndex: index,
    date,
    time,
    sender: message.sender,
    text: normalizeLineEndings(message.text),
    kind: message.kind,
  };
}

function classifyLocalMessages(messages) {
  const qualified = [];
  const nontext = [];
  for (const message of messages) {
    if (!isTextContentType(message.contentType) || message.text === null || message.text.length === 0) {
      nontext.push({
        sourceRef: message.sourceRef,
        contentType: message.contentType,
        reason: 'nontext_or_empty_text_not_compared',
      });
      continue;
    }
    qualified.push(message);
  }
  return { qualified, nontext };
}

function classifyUiMessages(messages, localScope) {
  const qualified = [];
  const qualifiedInDateScope = [];
  const candidates = [];
  const candidatesInDateScope = [];
  const nontext = [];
  let outsideDateScope = 0;
  let qualifiedOutsideDateScope = 0;
  for (const message of messages) {
    const outsideLocalDateScope = message.date < localScope.requested.dateFrom || message.date > localScope.requested.dateTo;
    if (outsideLocalDateScope) outsideDateScope += 1;
    if (!isCandidateUiRow(message)) {
      nontext.push({
        uiIndex: message.uiIndex,
        kind: message.kind,
        reason: message.kind === 'message' ? 'empty_text_not_compared' : 'nontext_or_nonmessage_not_compared',
      });
      continue;
    }
    candidates.push(message);
    if (isClearlyTypedTextMessage(message)) qualified.push(message);
    if (outsideLocalDateScope) {
      if (isClearlyTypedTextMessage(message)) qualifiedOutsideDateScope += 1;
      continue;
    }
    candidatesInDateScope.push(message);
    if (isClearlyTypedTextMessage(message)) qualifiedInDateScope.push(message);
  }
  return { qualified, qualifiedInDateScope, candidates, candidatesInDateScope, nontext, outsideDateScope, qualifiedOutsideDateScope };
}

function isCandidateUiRow(message) {
  return (message.kind === 'message' || message.kind === 'unknown') && message.text.length > 0;
}

function isClearlyTypedTextMessage(message) {
  return message.kind === 'message' && !isCommonMediaPlaceholder(message.text);
}

function classifyZeroCandidateUiMessages(messages, graph, localScope) {
  const uiOnly = [];
  const uiUnclassified = [];
  for (const message of messages) {
    if ((graph.byUi.get(message.uiIndex) || []).length > 0) continue;
    if (message.kind === 'unknown') {
      uiUnclassified.push(unclassifiedUiRow(message, 'unknown_ui_kind_zero_candidates'));
      continue;
    }
    if (isCommonMediaPlaceholder(message.text)) {
      uiUnclassified.push(unclassifiedUiRow(message, 'common_media_placeholder_zero_candidates'));
      continue;
    }
    const comparison = uiOnlyComparability(message, localScope);
    uiOnly.push({
      uiIndex: message.uiIndex,
      date: message.date,
      time: message.time,
      sender: message.sender,
      comparable: comparison.comparable,
      reason: comparison.reason,
    });
  }
  return { uiOnly, uiUnclassified };
}

function unclassifiedUiRow(message, reason) {
  return {
    uiIndex: message.uiIndex,
    date: message.date,
    time: message.time,
    sender: message.sender,
    comparable: false,
    reason,
  };
}

function isCommonMediaPlaceholder(text) {
  return text === '圖片' || text === '貼圖';
}

function uiOnlyComparability(message, localScope) {
  const { requested } = localScope;
  if (message.date < requested.dateFrom || message.date > requested.dateTo) {
    return { comparable: false, reason: 'outside_local_date_scope' };
  }
  if (requested.query !== undefined && !message.text.includes(normalizeLineEndings(requested.query))) {
    return { comparable: false, reason: 'outside_local_query_scope' };
  }
  if (localScope.pageLimited) {
    return { comparable: false, reason: 'local_result_page_limited' };
  }
  return { comparable: true, reason: 'no_local_candidate_in_comparable_scope' };
}

function buildCandidateGraph(localMessages, uiMessages) {
  const byLocal = new Map(localMessages.map(message => [message.sourceRef, []]));
  const byUi = new Map(uiMessages.map(message => [message.uiIndex, []]));
  for (const local of localMessages) {
    for (const ui of uiMessages) {
      const edge = exactCandidate(local, ui);
      if (!edge) continue;
      const candidate = { local, ui, ...edge };
      byLocal.get(local.sourceRef).push(candidate);
      byUi.get(ui.uiIndex).push(candidate);
    }
  }
  return { byLocal, byUi };
}

function exactCandidate(local, ui) {
  if (local.date !== ui.date || local.minute !== ui.time) return null;
  if (ui.text === local.text) {
    const senderEvidence = directSenderEvidence(local.sender, ui.sender);
    if (!senderEvidence) return null;
    return { matchBasis: 'exact_date_minute_full_text', senderEvidence };
  }
  if (ui.sender === null && typeof local.sender === 'string' && local.sender.length > 0
      && ui.text === `${local.sender} ${local.text}`) {
    return {
      matchBasis: 'exact_date_minute_full_copied_row',
      senderEvidence: 'sender_embedded_in_copied_row',
    };
  }
  return null;
}

function directSenderEvidence(localSender, uiSender) {
  if (uiSender === null) return 'sender_unverified';
  if (typeof localSender !== 'string') return null;
  if (localSender === uiSender) return 'exact';
  const wrapped = /^\*([^*].*?)\*$/u.exec(localSender);
  if (wrapped && wrapped[1] === uiSender) return 'copy_wrapper_difference';
  return null;
}

function resolveCandidateGraph(graph, localMessages) {
  const visitedLocal = new Set();
  const visitedUi = new Set();
  const matches = [];
  const ambiguities = [];
  const ambiguousRefs = new Set();

  for (const local of localMessages) {
    if (visitedLocal.has(local.sourceRef) || graph.byLocal.get(local.sourceRef).length === 0) continue;
    const component = walkComponent(local, graph, visitedLocal, visitedUi);
    if (component.locals.length === 1 && component.uis.length === 1) {
      matches.push({ local: component.locals[0], ui: component.uis[0], edge: component.edges[0] });
      continue;
    }
    const sourceRefs = component.locals.map(item => item.sourceRef);
    for (const sourceRef of sourceRefs) ambiguousRefs.add(sourceRef);
    ambiguities.push({
      sourceRefs,
      uiIndexes: component.uis.map(item => item.uiIndex),
      candidateMatchBases: [...new Set(component.edges.map(edge => edge.matchBasis))].sort(),
      reason: 'non_unique_candidate_component',
    });
  }

  const notObserved = localMessages.filter(message => !visitedLocal.has(message.sourceRef) && !ambiguousRefs.has(message.sourceRef));
  return { matches, ambiguities, notObserved };
}

function walkComponent(start, graph, visitedLocal, visitedUi) {
  const locals = [];
  const uis = [];
  const edges = [];
  const queuedLocals = [start];
  const queuedUi = [];

  while (queuedLocals.length || queuedUi.length) {
    while (queuedLocals.length) {
      const local = queuedLocals.shift();
      if (visitedLocal.has(local.sourceRef)) continue;
      visitedLocal.add(local.sourceRef);
      locals.push(local);
      for (const edge of graph.byLocal.get(local.sourceRef)) {
        edges.push(edge);
        if (!visitedUi.has(edge.ui.uiIndex)) queuedUi.push(edge.ui);
      }
    }
    while (queuedUi.length) {
      const ui = queuedUi.shift();
      if (visitedUi.has(ui.uiIndex)) continue;
      visitedUi.add(ui.uiIndex);
      uis.push(ui);
      for (const edge of graph.byUi.get(ui.uiIndex)) {
        if (!visitedLocal.has(edge.local.sourceRef)) queuedLocals.push(edge.local);
      }
    }
  }

  return { locals, uis, edges };
}

function optionalScopeText(value, maximumLength) {
  if (value === undefined || value === null) return undefined;
  if (typeof value !== 'string' || !value.length || value.length > maximumLength || value.includes('\0')) fail();
  return value;
}

function localPageIsLimited(local, scope, cursor) {
  return cursor !== undefined
    || scope.truncated === true
    || paginationShowsMore(scope.pagination)
    || paginationShowsMore(local.pagination);
}

function paginationShowsMore(value) {
  return isRecord(value)
    && (value.hasMore === true || (typeof value.nextCursor === 'string' && value.nextCursor.length > 0));
}

function snapshotCaptureCompletedAt(value) {
  if (!isRecord(value)) return null;
  const completedAt = value.captureCompletedAt;
  return typeof completedAt === 'string' && completedAt.trim() && !completedAt.includes('\0') ? completedAt : null;
}

function buildTimingEvidence(local, ui) {
  const localSnapshotCaptureCompletedAt = local.scope.snapshotCaptureCompletedAt;
  const uiRetrievedAt = ui.retrievedAt;
  const localTime = parseTimestamp(localSnapshotCaptureCompletedAt);
  const uiTime = parseTimestamp(uiRetrievedAt);
  const base = { localSnapshotCaptureCompletedAt, uiRetrievedAt };

  if (localTime === null) {
    return {
      ...base,
      status: 'unknown',
      reason: 'local_snapshot_capture_completed_at_unavailable',
      note: 'No local snapshot completion timestamp is available, so retrieval timestamps are not substituted as snapshot evidence.',
    };
  }
  if (uiTime === null) {
    return {
      ...base,
      status: 'unknown',
      reason: 'ui_retrieved_at_unparseable',
      note: 'The UI retrieval timestamp cannot be ordered against the local snapshot completion timestamp.',
    };
  }

  return {
    ...base,
    status: 'known',
    ordering: uiTime > localTime
      ? 'ui_retrieved_after_local_snapshot'
      : uiTime < localTime
        ? 'ui_retrieved_before_local_snapshot'
        : 'same_instant',
    note: 'Timestamp ordering compares local snapshot completion with UI retrieval only; it does not establish whether a message is new or absent.',
  };
}

function parseTimestamp(value) {
  if (typeof value !== 'string' || !value.trim()) return null;
  const timestamp = Date.parse(value);
  return Number.isFinite(timestamp) ? timestamp : null;
}

function isTextContentType(value) {
  return value === 0 || value === 'text';
}

function isContentType(value) {
  return Number.isInteger(value) || typeof value === 'string';
}

function normalizeLineEndings(value) {
  return value.replace(/\r\n/gu, '\n');
}

function requireRecord(value, name) {
  if (!isRecord(value)) fail();
  return value;
}

function isRecord(value) {
  return Boolean(value) && typeof value === 'object' && !Array.isArray(value);
}

function requireExactChat(value) {
  try { requireChat(value); }
  catch { fail(); }
}

function requireRetrievedAt(value) {
  if (typeof value !== 'string' || !value.trim() || value.includes('\0')) fail();
}

function requireDate(value) {
  if (typeof value !== 'string') fail();
  const match = ISO_DATE.exec(value);
  if (!match || match[0] !== value) fail();
  const year = Number(match[1]);
  const month = Number(match[2]);
  const day = Number(match[3]);
  if (!isRealDate(year, month, day)) fail();
  return value;
}

function requireTime(value, pattern) {
  if (typeof value !== 'string') fail();
  const match = pattern.exec(value);
  if (!match || match[0] !== value) fail();
  const hour = Number(match[1]);
  const minute = Number(match[2]);
  const second = match[3] === undefined ? 0 : Number(match[3]);
  if (hour > 23 || minute > 59 || second > 59) fail();
  return value;
}

function isRealDate(year, month, day) {
  if (year < 1 || year > 9999 || month < 1 || month > 12 || day < 1) return false;
  const days = [31, isLeapYear(year) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return day <= days[month - 1];
}

function isLeapYear(year) {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

function fail() {
  throw new LineToolError('LINE_SOURCE_SCOPE_MISMATCH', 'Local and UI sources must be exact, compatible bounded LINE message scopes.');
}
