import assert from 'node:assert/strict';
import test from 'node:test';

import { LineToolError } from '../src/extensions/line-runtime.mjs';
import {
  LINE_WORKFLOW_PLAN_PROPERTIES,
  LINE_WORKFLOW_PLAN_SCHEMA,
  prepareLineWorkflow,
} from '../src/extensions/line-workflow-plan.mjs';

const fixedNow = () => new Date('2026-09-10T00:00:00.000Z');

function mentionInput(overrides = {}) {
  return {
    workflow: 'mentions',
    chatName: '測試群組',
    message: '請確認今天的安排。',
    mentionTargets: ['Alice', 'All'],
    ...overrides,
  };
}

function replyInput(overrides = {}) {
  return {
    workflow: 'reply',
    chatName: '測試群組',
    message: '收到，我會處理。',
    source: {
      text: '麻煩今天回覆報價。',
      sender: '客戶甲',
      date: '2026-09-09',
      time: '09:05',
    },
    ...overrides,
  };
}

function pollInput(overrides = {}) {
  return {
    workflow: 'polls',
    chatName: '測試群組',
    poll: {
      question: '明天午餐要吃什麼？',
      options: ['便當', '麵', '沙拉'],
      multipleChoice: false,
      anonymous: false,
      allowAddOptions: false,
      deadline: '2026-09-10T08:01+08:00',
    },
    ...overrides,
  };
}

function invalid(action, pattern = /./) {
  assert.throws(action, error => error instanceof LineToolError
    && error.code === 'LINE_INVALID_ARGUMENT'
    && pattern.test(error.message));
}

test('exports a closed branch schema and validates preparation-only mention output', () => {
  assert.ok(LINE_WORKFLOW_PLAN_PROPERTIES.workflow);
  assert.equal(LINE_WORKFLOW_PLAN_SCHEMA.oneOf.length, 3);

  const result = prepareLineWorkflow(mentionInput());
  assert.equal(result.success, true);
  assert.equal(result.execution, 'preparation_only');
  assert.match(result.planId, /^[a-f0-9]{64}$/u);
  assert.equal(result.performedAction, false);
  assert.equal(result.sent, false);
  assert.equal(result.published, false);
  assert.equal(result.permissionVerified, false);
  assert.equal(result.approvalVerified, false);
  assert.equal(result.uiVerified, false);
  assert.deepEqual(result.localPlannerLimits, {
    version: 'local_v1',
    claimsLinePlatformLimits: false,
  });
  assert.equal(result.mentionSelectionVerified, false);
  assert.deepEqual(result.draft, {
    literalBody: '請確認今天的安排。',
    mentionTargets: ['Alice', 'All'],
    expectedMentionTokens: ['@Alice', '@All'],
  });
  assert.equal(result.humanReview.plainTextIsMentionProof, false);
  assert.deepEqual(result.requiredUIchecks.map(item => item.id), [
    'fresh_exact_chat_ui',
    'stop_on_chat_interference',
    'blue_mention_tokens',
    'literal_body_is_insufficient',
  ]);
  assert.ok(result.requiredUIchecks.every(item => item.verified === false));
});

test('rejects wrong or missing chat names before a workflow can be prepared', () => {
  for (const chatName of ['', ' 測試群組', '測試群組 ', '測試\n群組']) {
    invalid(() => prepareLineWorkflow(mentionInput({ chatName })), /chatName/u);
  }
  const { chatName, ...withoutChat } = mentionInput();
  invalid(() => prepareLineWorkflow(withoutChat), /requires chatName/u);
});

test('strictly rejects missing, cross-workflow, and extra input fields', () => {
  const { mentionTargets, ...missingMentionTargets } = mentionInput();
  invalid(() => prepareLineWorkflow(missingMentionTargets), /requires mentionTargets/u);
  invalid(() => prepareLineWorkflow(mentionInput({ poll: pollInput().poll })), /does not allow poll/u);
  invalid(() => prepareLineWorkflow(replyInput({ mentionTargets: ['Alice'] })), /does not allow mentionTargets/u);
  invalid(() => prepareLineWorkflow(pollInput({ message: 'not valid for polls' })), /does not allow message/u);
  invalid(() => prepareLineWorkflow(replyInput({ source: { ...replyInput().source, sourceRef: 'not-a-ui-proof' } })), /does not allow sourceRef/u);
  invalid(() => prepareLineWorkflow(mentionInput({ approved: true })), /does not allow approved/u);
});

test('mention targets require exact non-@ display names and NFC uniqueness', () => {
  invalid(() => prepareLineWorkflow(mentionInput({ mentionTargets: ['@Alice'] })), /must not start/u);
  invalid(() => prepareLineWorkflow(mentionInput({ mentionTargets: [' Alice'] })), /surrounding whitespace/u);
  invalid(() => prepareLineWorkflow(mentionInput({ mentionTargets: ['Jose\u0301', 'José'] })), /NFC normalization/u);

  const result = prepareLineWorkflow(mentionInput({ mentionTargets: ['All', '張三'] }));
  assert.deepEqual(result.humanReview.expectedMentionTokens, ['@All', '@張三']);
});

test('reply requires an exact real source identity and never emits a sourceRef assertion', () => {
  invalid(() => prepareLineWorkflow(replyInput({ source: { ...replyInput().source, date: '2026-02-30' } })), /real calendar/u);
  invalid(() => prepareLineWorkflow(replyInput({ source: { ...replyInput().source, date: '2025-02-29' } })), /real calendar/u);
  const leap = prepareLineWorkflow(replyInput({ source: { ...replyInput().source, date: '2024-02-29', time: '23:59:59' } }));
  assert.equal(leap.draft.source.date, '2024-02-29');
  assert.equal(leap.draft.source.time, '23:59:59');
  assert.equal(Object.hasOwn(leap.draft.source, 'sourceRef'), false);
  assert.equal(leap.humanReview.fullSourceMatchRequired, true);
  assert.equal(leap.humanReview.sourcePrefixMatchAllowed, false);
  assert.equal(leap.humanReview.guidedVisualRequiredForTruncatedPreview, true);
  assert.equal(leap.humanReview.preserveExistingDraftAndQuote, true);
  invalid(() => prepareLineWorkflow(replyInput({ source: { ...replyInput().source, time: '24:00' } })), /real 24-hour/u);
  invalid(() => prepareLineWorkflow(replyInput({ source: { ...replyInput().source, time: '9:05' } })), /HH:mm/u);
  invalid(() => prepareLineWorkflow(replyInput({ source: { ...replyInput().source, date: '2026-09-09\n' } })), /strict YYYY-MM-DD/u);
});

test('poll options reject NFC and whitespace-normalized duplicates and uploads', () => {
  invalid(() => prepareLineWorkflow(pollInput({ poll: { ...pollInput().poll, options: ['Cafe\u0301', 'Café'] } }), { now: fixedNow }), /NFC and whitespace/u);
  invalid(() => prepareLineWorkflow(pollInput({ poll: { ...pollInput().poll, options: ['A  B', ' A B '] } }), { now: fixedNow }), /NFC and whitespace/u);
  invalid(() => prepareLineWorkflow(pollInput({ poll: { ...pollInput().poll, options: [{ filePath: 'C:\\tmp\\x.png' }, '文字'] } }), { now: fixedNow }), /text string/u);
  invalid(() => prepareLineWorkflow(pollInput({ poll: { ...pollInput().poll, attachment: 'x.png' } }), { now: fixedNow }), /does not allow attachment/u);
});

test('poll flags are explicit booleans and deadline is a future strict Taipei minute', () => {
  for (const [field, value] of [
    ['multipleChoice', 'false'],
    ['anonymous', 0],
    ['allowAddOptions', null],
  ]) {
    invalid(() => prepareLineWorkflow(pollInput({ poll: { ...pollInput().poll, [field]: value } }), { now: fixedNow }), /explicit boolean/u);
  }
  const { anonymous, ...missingFlag } = pollInput().poll;
  invalid(() => prepareLineWorkflow(pollInput({ poll: missingFlag }), { now: fixedNow }), /requires anonymous/u);

  invalid(() => prepareLineWorkflow(pollInput({ poll: { ...pollInput().poll, deadline: '2026-09-10T08:00+08:00' } }), { now: fixedNow }), /future/u);
  invalid(() => prepareLineWorkflow(pollInput({ poll: { ...pollInput().poll, deadline: '2026-09-10T08:01Z' } }), { now: fixedNow }), /strict YYYY-MM-DDTHH:mm\+08:00/u);
  invalid(() => prepareLineWorkflow(pollInput({ poll: { ...pollInput().poll, deadline: '2026-09-10T08:01+08:00\n' } }), { now: fixedNow }), /strict YYYY-MM-DDTHH:mm\+08:00/u);
  invalid(() => prepareLineWorkflow(pollInput({ poll: { ...pollInput().poll, deadline: '2026-09-10T08:60+08:00' } }), { now: fixedNow }), /real Taipei/u);
  invalid(() => prepareLineWorkflow(pollInput({ poll: { ...pollInput().poll, deadline: '2026-02-29T09:00+08:00' } }), { now: fixedNow }), /real Taipei/u);

  const result = prepareLineWorkflow(pollInput(), { now: fixedNow });
  assert.equal(result.draft.textOptionsOnly, true);
  assert.equal(result.pollPublishedVerified, false);
  assert.equal(result.humanReview.reviewEveryOptionSettingAndDeadline, true);
  assert.equal(result.humanReview.doneIsPublishOrAutomaticMessageBoundary, true);
  assert.equal(result.humanReview.automaticRetryAfterUncertainPublishAllowed, false);
  assert.deepEqual(result.requiredUIchecks.map(item => item.id), [
    'fresh_exact_chat_ui',
    'stop_on_chat_interference',
    'review_all_options_settings_deadline',
    'done_can_publish_or_message',
    'no_automatic_retry_after_uncertain_publish',
  ]);
});

test('plan IDs are canonical content fingerprints that are stable and setting-sensitive', () => {
  const one = prepareLineWorkflow(replyInput(), { now: fixedNow });
  const two = prepareLineWorkflow({
    source: {
      time: '09:05',
      date: '2026-09-09',
      sender: '客戶甲',
      text: '麻煩今天回覆報價。',
    },
    message: '收到，我會處理。',
    chatName: '測試群組',
    workflow: 'reply',
  }, { now: () => new Date('2099-01-01T00:00:00.000Z') });
  assert.equal(one.planId, two.planId);

  const initialPoll = prepareLineWorkflow(pollInput(), { now: fixedNow });
  const changedSetting = prepareLineWorkflow(pollInput({ poll: { ...pollInput().poll, multipleChoice: true } }), { now: fixedNow });
  assert.notEqual(initialPoll.planId, changedSetting.planId);
});

test('planner has no UI dependency and does not touch an unexpected UI property', () => {
  let uiPropertyReads = 0;
  const args = mentionInput();
  Object.defineProperty(args, 'ui', {
    enumerable: true,
    get() {
      uiPropertyReads += 1;
      throw new Error('a UI backend must not be read by the planner');
    },
  });

  invalid(() => prepareLineWorkflow(args), /does not allow ui/u);
  assert.equal(uiPropertyReads, 0);
});
