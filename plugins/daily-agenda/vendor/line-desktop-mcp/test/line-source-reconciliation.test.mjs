import assert from 'node:assert/strict';
import test from 'node:test';

import { LineToolError } from '../src/extensions/line-runtime.mjs';
import { reconcileLineSources } from '../src/extensions/line-source-reconciliation.mjs';

const CHAT = '測試群組';
const REQUESTED = {
  chatName: CHAT,
  dateFrom: '2026-09-09',
  dateTo: '2026-09-10',
  messageLimit: 200,
};

function local(messages, overrides = {}) {
  return {
    chatName: CHAT,
    retrievedAt: '2026-09-10T10:00:00+08:00',
    scope: { kind: 'local_database', requested: { ...REQUESTED } },
    messages,
    ...overrides,
  };
}

function ui(messages, overrides = {}) {
  return {
    chatName: CHAT,
    retrievedAt: '2026-09-10T10:01:00.000Z',
    scope: { kind: 'loaded_history_window' },
    messages,
    ...overrides,
  };
}

function localMessage(overrides = {}) {
  return {
    sourceRef: 'message:one',
    date: '2026-09-10',
    time: '09:05:17',
    sender: 'Alice',
    text: '完整本文',
    contentType: 0,
    media: { state: 'not_applicable' },
    ...overrides,
  };
}

function uiMessage(overrides = {}) {
  return {
    date: '2026-09-10',
    time: '09:05',
    sender: 'Alice',
    text: '完整本文',
    kind: 'message',
    ...overrides,
  };
}

function sourceMismatch(action) {
  assert.throws(action, error => error instanceof LineToolError && error.code === 'LINE_SOURCE_SCOPE_MISMATCH');
}

test('corroborates only exact date-minute full multiline text after CRLF-to-LF normalization', () => {
  const db = local([localMessage({ text: '第一行\r\n第二行\n😀' })]);
  const copy = ui([uiMessage({ text: '第一行\n第二行\n😀' })]);
  const result = reconcileLineSources(db, copy);

  assert.deepEqual(result.matches, [{
    sourceRef: 'message:one',
    uiIndex: 0,
    matchBasis: 'exact_date_minute_full_text',
    senderEvidence: 'exact',
  }]);
  assert.equal(result.comparison.kind, 'local_database_vs_loaded_history_window_content_reconciliation');
  assert.equal(result.localRetrievedAt, db.retrievedAt);
  assert.equal(result.uiRetrievedAt, copy.retrievedAt);
  assert.deepEqual(result.counts, {
    localMessages: 1,
    uiMessages: 1,
    qualifiedTextLocal: 1,
    typedTextUi: 1,
    typedTextUiInDateScope: 1,
    candidateUi: 1,
    candidateUiInDateScope: 1,
    qualifiedTextUi: 1,
    qualifiedUiInDateScope: 1,
    uiOutsideDateScope: 0,
    qualifiedUiOutsideDateScope: 0,
    qualifiedTextMatches: 1,
    fullyCorroborated: 1,
    senderUnverified: 0,
    ambiguous: 0,
    ambiguousSourceRefs: 0,
    notObserved: 0,
    localOnly: 0,
    uiOnly: 0,
    uiOnlyComparable: 0,
    uiOnlyNonComparable: 0,
    uiUnclassified: 0,
    nontext: 0,
    uiNontext: 0,
  });
  for (const field of ['uiActionIdentityVerified', 'totalHistoryKnown', 'deliveryVerified']) assert.equal(result[field], false);
  assert.deepEqual(result.localOnlySourceRefs, []);
  assert.deepEqual(result.uiOnly, []);
  assert.deepEqual(result.uiUnclassified, []);
  assert.equal(result.timing.status, 'unknown');
  assert.equal(result.timing.localSnapshotCaptureCompletedAt, null);
});

test('refuses wrong chat and malformed bounded source structures', () => {
  sourceMismatch(() => reconcileLineSources(local([localMessage()]), ui([uiMessage()], { chatName: '其他群組' })));
  sourceMismatch(() => reconcileLineSources(local([localMessage()], { scope: { kind: 'local_database', requested: { ...REQUESTED, chatName: '其他群組' } } }), ui([uiMessage()])));
  sourceMismatch(() => reconcileLineSources(local([localMessage()], { scope: { kind: 'wrong', requested: REQUESTED } }), ui([uiMessage()])));
  sourceMismatch(() => reconcileLineSources(local([localMessage({ time: '09:05' })]), ui([uiMessage()])));
  sourceMismatch(() => reconcileLineSources(local([localMessage()]), ui([{ ...uiMessage(), date: '2026-02-30' }])));
});

test('compares only UI rows inside the local requested date window', () => {
  const result = reconcileLineSources(
    local([localMessage()]),
    ui([uiMessage({ date: '2026-09-08' })]),
  );
  assert.equal(result.counts.qualifiedUiInDateScope, 0);
  assert.equal(result.counts.uiOutsideDateScope, 1);
  assert.deepEqual(result.matches, []);
  assert.deepEqual(result.notObservedSourceRefs, ['message:one']);
  assert.deepEqual(result.uiOnly, [{
    uiIndex: 0,
    date: '2026-09-08',
    time: '09:05',
    sender: 'Alice',
    comparable: false,
    reason: 'outside_local_date_scope',
  }]);
  assert.match(result.note, /bounded UI history window/u);
});

test('duplicate candidates are ambiguous even when only one UI row exists', () => {
  const result = reconcileLineSources(
    local([
      localMessage({ sourceRef: 'message:one' }),
      localMessage({ sourceRef: 'message:two' }),
    ]),
    ui([uiMessage()]),
  );
  assert.deepEqual(result.matches, []);
  assert.deepEqual(result.ambiguities, [{
    sourceRefs: ['message:one', 'message:two'],
    uiIndexes: [0],
    candidateMatchBases: ['exact_date_minute_full_text'],
    reason: 'non_unique_candidate_component',
  }]);
  assert.equal(result.counts.ambiguous, 1);
  assert.equal(result.counts.ambiguousSourceRefs, 2);
  assert.deepEqual(result.notObservedSourceRefs, []);
  assert.deepEqual(result.uiOnlyIndexes, []);
});

test('one local message also refuses two otherwise identical UI candidates', () => {
  const result = reconcileLineSources(
    local([localMessage()]),
    ui([uiMessage(), uiMessage()]),
  );
  assert.deepEqual(result.matches, []);
  assert.deepEqual(result.ambiguities[0].sourceRefs, ['message:one']);
  assert.deepEqual(result.ambiguities[0].uiIndexes, [0, 1]);
  assert.equal(result.counts.ambiguous, 1);
  assert.equal(result.counts.notObserved, 0);
  assert.deepEqual(result.uiOnlyIndexes, []);
});

test('ambiguities retain embedded-row and mixed candidate evidence without inventing a single basis', () => {
  const embedded = reconcileLineSources(local([
    localMessage({ sourceRef: 'message:one', text: '全文' }),
    localMessage({ sourceRef: 'message:two', text: '全文' }),
  ]), ui([uiMessage({ sender: null, text: 'Alice 全文' })]));
  assert.deepEqual(embedded.matches, []);
  assert.deepEqual(embedded.ambiguities[0].candidateMatchBases, ['exact_date_minute_full_copied_row']);
  const mixed = reconcileLineSources(local([
    localMessage({ sourceRef: 'message:one', text: '全文' }),
    localMessage({ sourceRef: 'message:two', sender: 'Bob', text: 'Alice 全文' }),
  ]), ui([uiMessage({ sender: null, text: 'Alice 全文' })]));
  assert.deepEqual(mixed.matches, []);
  assert.deepEqual(mixed.ambiguities[0].candidateMatchBases, ['exact_date_minute_full_copied_row', 'exact_date_minute_full_text']);
});

test('sender evidence distinguishes exact, copied wrapper, embedded rows, and unverified rows', () => {
  const exact = reconcileLineSources(local([localMessage()]), ui([uiMessage()]));
  assert.equal(exact.matches[0].senderEvidence, 'exact');

  const alias = reconcileLineSources(local([localMessage({ sender: '*Alice*' })]), ui([uiMessage({ sender: 'Alice' })]));
  assert.equal(alias.matches[0].senderEvidence, 'copy_wrapper_difference');

  const embedded = reconcileLineSources(local([localMessage({ text: '需要完整比對' })]), ui([uiMessage({ kind: 'unknown', sender: null, text: 'Alice 需要完整比對' })]));
  assert.deepEqual(embedded.matches[0], {
    sourceRef: 'message:one',
    uiIndex: 0,
    matchBasis: 'exact_date_minute_full_copied_row',
    senderEvidence: 'sender_embedded_in_copied_row',
  });
  assert.deepEqual(embedded.uiUnclassifiedIndexes, []);

  const unknown = reconcileLineSources(local([localMessage()]), ui([uiMessage({ sender: null })]));
  assert.equal(unknown.matches[0].senderEvidence, 'sender_unverified');
  assert.equal(unknown.counts.qualifiedTextMatches, 1);
  assert.equal(unknown.counts.fullyCorroborated, 0);
  assert.equal(unknown.counts.senderUnverified, 1);
});

test('explicit sender mismatch, prefixes, and truncation are not candidates', () => {
  const differentSender = reconcileLineSources(local([localMessage()]), ui([uiMessage({ sender: 'Bob' })]));
  assert.deepEqual(differentSender.matches, []);
  assert.deepEqual(differentSender.notObservedSourceRefs, ['message:one']);

  const truncated = reconcileLineSources(local([localMessage({ text: '完整本文不可截斷' })]), ui([uiMessage({ text: '完整本文…' })]));
  assert.deepEqual(truncated.matches, []);
  assert.deepEqual(truncated.notObservedSourceRefs, ['message:one']);

  const unknownPrefix = reconcileLineSources(local([localMessage({ text: '全文' })]), ui([uiMessage({ sender: null, text: 'Alice 全文（截斷）' })]));
  assert.deepEqual(unknownPrefix.matches, []);
  assert.deepEqual(unknownPrefix.notObservedSourceRefs, ['message:one']);
});

test('nontext local messages never use placeholders, media previews, or timestamps as identity', () => {
  let mediaReads = 0;
  const image = localMessage({
    sourceRef: 'message:image',
    contentType: 1,
    text: '[圖片]',
  });
  Object.defineProperty(image, 'media', {
    enumerable: true,
    get() {
      mediaReads += 1;
      throw new Error('media preview content must not be read');
    },
  });
  const result = reconcileLineSources(local([image]), ui([uiMessage({ text: '[圖片]' })]));
  assert.equal(mediaReads, 0);
  assert.deepEqual(result.matches, []);
  assert.deepEqual(result.notObservedSourceRefs, []);
  assert.deepEqual(result.nontext, [{
    sourceRef: 'message:image',
    contentType: 1,
    reason: 'nontext_or_empty_text_not_compared',
  }]);
  assert.equal(result.counts.nontext, 1);
  assert.doesNotMatch(JSON.stringify(result), /media preview|secret|\[圖片\]/u);
});

test('a lone carriage return is not normalized and cannot become an exact full-text match', () => {
  const result = reconcileLineSources(
    local([localMessage({ text: '第一行\r第二行' })]),
    ui([uiMessage({ text: '第一行\n第二行' })]),
  );
  assert.deepEqual(result.matches, []);
  assert.deepEqual(result.notObservedSourceRefs, ['message:one']);
});

test('reports qualified UI-only text rows and local-only refs directionally without claiming database incompleteness', () => {
  const uiOnly = reconcileLineSources(local([]), ui([uiMessage({ text: '只在視窗看到' })]));
  assert.deepEqual(uiOnly.localOnlySourceRefs, []);
  assert.deepEqual(uiOnly.uiOnly, [{
    uiIndex: 0,
    date: '2026-09-10',
    time: '09:05',
    sender: 'Alice',
    comparable: true,
    reason: 'no_local_candidate_in_comparable_scope',
  }]);
  assert.deepEqual(uiOnly.uiOnlyIndexes, [0]);
  assert.equal(uiOnly.counts.uiOnly, 1);
  assert.equal(uiOnly.counts.uiOnlyComparable, 1);
  assert.doesNotMatch(uiOnly.note, /database is incomplete|complete agreement/u);

  const localOnly = reconcileLineSources(local([localMessage()]), ui([]));
  assert.deepEqual(localOnly.notObservedSourceRefs, ['message:one']);
  assert.deepEqual(localOnly.localOnlySourceRefs, ['message:one']);
  assert.deepEqual(localOnly.uiOnlyIndexes, []);
});

test('does not label system, image, sticker, or empty UI rows as missing text', () => {
  const result = reconcileLineSources(local([]), ui([
    uiMessage({ kind: 'system', text: '系統事件不可當文字缺漏' }),
    uiMessage({ kind: 'image', text: '[私有圖片預覽]' }),
    uiMessage({ kind: 'sticker', text: '[私有貼圖預覽]' }),
    uiMessage({ kind: 'message', text: '' }),
  ]));
  assert.deepEqual(result.uiOnly, []);
  assert.deepEqual(result.uiOnlyIndexes, []);
  assert.deepEqual(result.uiUnclassified, []);
  assert.deepEqual(result.uiNontext, [
    { uiIndex: 0, kind: 'system', reason: 'nontext_or_nonmessage_not_compared' },
    { uiIndex: 1, kind: 'image', reason: 'nontext_or_nonmessage_not_compared' },
    { uiIndex: 2, kind: 'sticker', reason: 'nontext_or_nonmessage_not_compared' },
    { uiIndex: 3, kind: 'message', reason: 'empty_text_not_compared' },
  ]);
  assert.equal(result.counts.uiNontext, 4);
  assert.doesNotMatch(JSON.stringify(result), /私有圖片|私有貼圖|系統事件/u);
});

test('keeps a zero-candidate unknown UI row as unclassified evidence instead of UI-only text', () => {
  const result = reconcileLineSources(local([]), ui([
    uiMessage({ kind: 'unknown', sender: null, text: '沒有可用的結構化分類' }),
  ]));
  assert.deepEqual(result.uiOnly, []);
  assert.deepEqual(result.uiOnlyIndexes, []);
  assert.deepEqual(result.uiUnclassified, [{
    uiIndex: 0,
    date: '2026-09-10',
    time: '09:05',
    sender: null,
    comparable: false,
    reason: 'unknown_ui_kind_zero_candidates',
  }]);
  assert.deepEqual(result.uiUnclassifiedIndexes, [0]);
  assert.equal(result.counts.uiUnclassified, 1);
  assert.doesNotMatch(JSON.stringify(result), /沒有可用的結構化分類/u);
});

test('counts exact unknown copied rows as qualified text evidence without double-counting typed UI rows', () => {
  const result = reconcileLineSources(local([
    localMessage({ sourceRef: 'message:unknown-one', text: '第一則' }),
    localMessage({ sourceRef: 'message:unknown-two', time: '09:06:17', text: '第二則' }),
    localMessage({ sourceRef: 'message:unknown-three', time: '09:07:17', text: '第三則' }),
    localMessage({ sourceRef: 'message:typed', time: '09:08:17', text: '明確文字' }),
  ]), ui([
    uiMessage({ kind: 'unknown', sender: null, text: 'Alice 第一則' }),
    uiMessage({ kind: 'unknown', sender: null, time: '09:06', text: 'Alice 第二則' }),
    uiMessage({ kind: 'unknown', sender: null, time: '09:07', text: 'Alice 第三則' }),
    uiMessage({ kind: 'message', time: '09:08', text: '明確文字' }),
  ]));
  assert.equal(result.counts.qualifiedTextMatches, 4);
  assert.equal(result.counts.candidateUi, 4);
  assert.equal(result.counts.candidateUiInDateScope, 4);
  assert.equal(result.counts.typedTextUi, 1);
  assert.equal(result.counts.typedTextUiInDateScope, 1);
  assert.equal(result.counts.qualifiedTextUi, 4);
  assert.equal(result.counts.qualifiedUiInDateScope, 4);
  assert.ok(result.counts.qualifiedTextMatches <= result.counts.qualifiedTextUi);
});

test('unmatched common media placeholders are unclassified, while exact literal text remains matchable', () => {
  const placeholders = reconcileLineSources(local([]), ui([
    uiMessage({ kind: 'message', text: '圖片' }),
    uiMessage({ kind: 'message', time: '09:06', text: '貼圖' }),
  ]));
  assert.deepEqual(placeholders.uiOnlyIndexes, []);
  assert.deepEqual(placeholders.uiUnclassified, [
    {
      uiIndex: 0,
      date: '2026-09-10',
      time: '09:05',
      sender: 'Alice',
      comparable: false,
      reason: 'common_media_placeholder_zero_candidates',
    },
    {
      uiIndex: 1,
      date: '2026-09-10',
      time: '09:06',
      sender: 'Alice',
      comparable: false,
      reason: 'common_media_placeholder_zero_candidates',
    },
  ]);
  assert.doesNotMatch(JSON.stringify(placeholders), /圖片|貼圖/u);

  const literalText = reconcileLineSources(local([
    localMessage({ sourceRef: 'message:literal-image-word', text: '圖片' }),
    localMessage({ sourceRef: 'message:literal-sticker-word', time: '09:06:17', text: '貼圖' }),
  ]), ui([
    uiMessage({ kind: 'message', text: '圖片' }),
    uiMessage({ kind: 'message', time: '09:06', text: '貼圖' }),
  ]));
  assert.deepEqual(literalText.matches.map(match => [match.sourceRef, match.uiIndex]), [
    ['message:literal-image-word', 0],
    ['message:literal-sticker-word', 1],
  ]);
  assert.deepEqual(literalText.uiOnlyIndexes, []);
  assert.deepEqual(literalText.uiUnclassifiedIndexes, []);
});

test('retains UI-only rows but marks date, query, and local-page limits non-comparable', () => {
  const outsideDate = reconcileLineSources(local([]), ui([uiMessage({ date: '2026-09-08', text: '日期範圍外' })]));
  assert.equal(outsideDate.uiOnly[0].comparable, false);
  assert.equal(outsideDate.uiOnly[0].reason, 'outside_local_date_scope');

  const queryFiltered = reconcileLineSources(
    local([], { scope: { kind: 'local_database', requested: { ...REQUESTED, query: 'needle' } } }),
    ui([uiMessage({ text: '這列不含查詢字' })]),
  );
  assert.deepEqual(queryFiltered.uiOnlyIndexes, [0]);
  assert.equal(queryFiltered.uiOnly[0].comparable, false);
  assert.equal(queryFiltered.uiOnly[0].reason, 'outside_local_query_scope');
  assert.equal(queryFiltered.scopeComparability.localQueryApplied, true);

  const pageLimited = reconcileLineSources(
    local([], {
      pagination: { hasMore: true, nextCursor: 'opaque-next-page' },
      scope: {
        kind: 'local_database',
        requested: {
          ...REQUESTED,
          cursor: 'opaque-next-page',
          mediaMode: 'preview',
          mediaSourceRefs: ['message:0123456789abcdef01234567'],
        },
        truncated: true,
      },
    }),
    ui([uiMessage({ text: '可能在別頁的列' })]),
  );
  assert.deepEqual(pageLimited.uiOnlyIndexes, [0]);
  assert.equal(pageLimited.uiOnly[0].comparable, false);
  assert.equal(pageLimited.uiOnly[0].reason, 'local_result_page_limited');
  assert.equal(pageLimited.scopeComparability.localPageLimited, true);
});

test('uses local snapshot completion rather than local retrieval time for timing evidence', () => {
  const withSnapshot = reconcileLineSources(
    local([], { scope: {
      kind: 'local_database',
      requested: { ...REQUESTED },
      snapshot: {
        captureStartedAt: '2026-09-10T09:59:55+08:00',
        captureCompletedAt: '2026-09-10T10:00:00+08:00',
      },
    } }),
    ui([], { retrievedAt: '2026-09-10T02:01:00.000Z' }),
  );
  assert.deepEqual(withSnapshot.timing, {
    localSnapshotCaptureCompletedAt: '2026-09-10T10:00:00+08:00',
    uiRetrievedAt: '2026-09-10T02:01:00.000Z',
    status: 'known',
    ordering: 'ui_retrieved_after_local_snapshot',
    note: 'Timestamp ordering compares local snapshot completion with UI retrieval only; it does not establish whether a message is new or absent.',
  });

  const withoutSnapshot = reconcileLineSources(local([], { retrievedAt: '2026-09-10T10:00:00+08:00' }), ui([]));
  assert.equal(withoutSnapshot.timing.status, 'unknown');
  assert.equal(withoutSnapshot.timing.reason, 'local_snapshot_capture_completed_at_unavailable');
  assert.equal(withoutSnapshot.timing.localSnapshotCaptureCompletedAt, null);
  assert.notEqual(withoutSnapshot.timing.localSnapshotCaptureCompletedAt, withoutSnapshot.localRetrievedAt);
  assert.match(withoutSnapshot.timing.note, /not substituted as snapshot evidence/u);
});
