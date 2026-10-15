import { createHash } from 'node:crypto';

import { LineToolError, requireChat } from './line-runtime.mjs';

const WORKFLOWS = ['mentions', 'reply', 'polls'];
const ROOT_FIELDS = {
  mentions: ['workflow', 'chatName', 'message', 'mentionTargets'],
  reply: ['workflow', 'chatName', 'message', 'source'],
  polls: ['workflow', 'chatName', 'poll'],
};
const SOURCE_FIELDS = ['text', 'sender', 'date', 'time'];
const POLL_FIELDS = ['question', 'options', 'multipleChoice', 'anonymous', 'allowAddOptions', 'deadline'];
const ISO_DATE = /^(\d{4})-(\d{2})-(\d{2})$/u;
const REPLY_TIME = /^(\d{2}):(\d{2})(?::(\d{2}))?$/u;
const TAIPEI_DEADLINE = /^(\d{4})-(\d{2})-(\d{2})T(\d{2}):(\d{2})\+08:00$/u;

const messageSchema = {
  type: 'string',
  minLength: 1,
  maxLength: 10000,
  description: 'Literal message text. The local planner validates it but never stages or sends it.',
};
const chatSchema = {
  type: 'string',
  minLength: 1,
  maxLength: 200,
  description: 'Exact single-line LINE chat name.',
};
const sourceSchema = {
  type: 'object',
  additionalProperties: false,
  required: SOURCE_FIELDS,
  properties: {
    text: { type: 'string', minLength: 1, maxLength: 10000 },
    sender: { type: 'string', minLength: 1, maxLength: 200 },
    date: { type: 'string', pattern: '^\\d{4}-\\d{2}-\\d{2}$' },
    time: { type: 'string', pattern: '^\\d{2}:\\d{2}(?::\\d{2})?$' },
  },
};
const pollSchema = {
  type: 'object',
  additionalProperties: false,
  required: POLL_FIELDS,
  description: 'Local v1 planner limits for a text-only poll; they are not assertions about LINE platform limits.',
  properties: {
    question: { type: 'string', minLength: 1, maxLength: 400 },
    options: {
      type: 'array',
      minItems: 2,
      maxItems: 20,
      items: { type: 'string', minLength: 1, maxLength: 200 },
      description: 'Text options only. Uploads and attachments are not accepted.',
    },
    multipleChoice: { type: 'boolean' },
    anonymous: { type: 'boolean' },
    allowAddOptions: { type: 'boolean' },
    deadline: {
      anyOf: [
        { type: 'null' },
        { type: 'string', pattern: '^\\d{4}-\\d{2}-\\d{2}T\\d{2}:\\d{2}\\+08:00$' },
      ],
    },
  },
};

/**
 * These properties are exported for an MCP descriptor. Use
 * LINE_WORKFLOW_PLAN_SCHEMA when the descriptor can preserve the workflow
 * branches; properties alone cannot reject a valid field from another branch.
 */
export const LINE_WORKFLOW_PLAN_PROPERTIES = {
  workflow: { type: 'string', enum: WORKFLOWS },
  chatName: chatSchema,
  message: messageSchema,
  mentionTargets: {
    type: 'array',
    minItems: 1,
    maxItems: 20,
    items: { type: 'string', minLength: 1, maxLength: 200 },
    description: 'Exact display names or All without a leading @. Real mention selection is a later UI action.',
  },
  source: sourceSchema,
  poll: pollSchema,
};

/** A closed schema suitable for the actual planner MCP tool. */
export const LINE_WORKFLOW_PLAN_SCHEMA = {
  oneOf: [
    {
      type: 'object',
      additionalProperties: false,
      required: ROOT_FIELDS.mentions,
      properties: {
        workflow: { const: 'mentions' },
        chatName: chatSchema,
        message: messageSchema,
        mentionTargets: LINE_WORKFLOW_PLAN_PROPERTIES.mentionTargets,
      },
    },
    {
      type: 'object',
      additionalProperties: false,
      required: ROOT_FIELDS.reply,
      properties: {
        workflow: { const: 'reply' },
        chatName: chatSchema,
        message: messageSchema,
        source: sourceSchema,
      },
    },
    {
      type: 'object',
      additionalProperties: false,
      required: ROOT_FIELDS.polls,
      properties: {
        workflow: { const: 'polls' },
        chatName: chatSchema,
        poll: pollSchema,
      },
    },
  ],
};

/**
 * Validate a local, reviewable LINE workflow plan without touching LINE,
 * files, the network, or an automation backend.
 *
 * `planId` is a SHA-256 fingerprint of the canonical content object only.
 * It intentionally excludes all approval and verification state.
 */
export function prepareLineWorkflow(args, { now = () => new Date() } = {}) {
  const input = requireRecord(args, 'workflow plan');
  const workflow = requireWorkflow(input.workflow);
  rejectUnexpectedFields(input, ROOT_FIELDS[workflow], 'workflow plan');
  requireAllFields(input, ROOT_FIELDS[workflow], 'workflow plan');

  const chatName = requireChat(input.chatName);
  const content = workflow === 'mentions'
    ? prepareMentionContent(chatName, input)
    : workflow === 'reply'
      ? prepareReplyContent(chatName, input)
      : preparePollContent(chatName, input, now);

  return buildPreparedPlan(content);
}

function prepareMentionContent(chatName, input) {
  const message = requireNonemptyText(input.message, 'message', 10000);
  const mentionTargets = requireMentionTargets(input.mentionTargets);
  return {
    version: 'line_workflow_plan_v1',
    workflow: 'mentions',
    chatName,
    literalBody: message,
    mentionTargets,
  };
}

function prepareReplyContent(chatName, input) {
  const message = requireNonemptyText(input.message, 'message', 10000);
  const source = requireReplySource(input.source);
  return {
    version: 'line_workflow_plan_v1',
    workflow: 'reply',
    chatName,
    replyBody: message,
    source,
  };
}

function preparePollContent(chatName, input, now) {
  const poll = requirePoll(input.poll, now);
  return {
    version: 'line_workflow_plan_v1',
    workflow: 'polls',
    chatName,
    poll,
  };
}

function buildPreparedPlan(content) {
  const planId = sha256Canonical(content);
  const common = {
    success: true,
    execution: 'preparation_only',
    interaction: {
      userSurface: 'Codex',
      operator: 'agent',
      dataLookup: 'agent_retrieves_and_verifies_authorized_sources_before_drafting',
      approvalSurface: 'Codex_exact_recipient_and_content',
      userUIInspectionRequired: false,
    },
    planId,
    workflow: content.workflow,
    chatName: content.chatName,
    performedAction: false,
    sent: false,
    published: false,
    permissionVerified: false,
    approvalVerified: false,
    uiVerified: false,
    localPlannerLimits: {
      version: 'local_v1',
      claimsLinePlatformLimits: false,
    },
    requiredUIchecks: commonChecks(),
  };

  if (content.workflow === 'mentions') {
    return {
      ...common,
      mentionSelectionVerified: false,
      draft: {
        literalBody: content.literalBody,
        mentionTargets: [...content.mentionTargets],
        expectedMentionTokens: content.mentionTargets.map(target => `@${target}`),
      },
      humanReview: {
        literalBody: content.literalBody,
        mentionTargets: [...content.mentionTargets],
        expectedMentionTokens: content.mentionTargets.map(target => `@${target}`),
        plainTextIsMentionProof: false,
      },
      requiredUIchecks: [
        ...common.requiredUIchecks,
        check('blue_mention_tokens', 'Select every target from LINE’s mention list and visually verify every expected token is blue.', false),
        check('literal_body_is_insufficient', 'Do not treat literalBody or plain @ text as proof of a real mention notification.', false),
      ],
    };
  }

  if (content.workflow === 'reply') {
    return {
      ...common,
      replyContextVerified: false,
      draft: {
        replyBody: content.replyBody,
        source: { ...content.source },
      },
      humanReview: {
        replyBody: content.replyBody,
        source: { ...content.source },
        fullSourceMatchRequired: true,
        sourcePrefixMatchAllowed: false,
        guidedVisualRequiredForTruncatedPreview: true,
        preserveExistingDraftAndQuote: true,
      },
      requiredUIchecks: [
        ...common.requiredUIchecks,
        check('full_source_disambiguation', 'Match the full source text with its exact sender, date, and time. Do not invent a sourceRef or identify a source by a prefix.', false),
        check('protect_existing_draft_or_quote', 'Inspect and protect any existing composer draft or quoted-reply context; stop on a conflict.', false),
        check('guided_visual_for_truncated_preview', 'If LINE truncates the source preview, use guided visual confirmation of the full source rather than a prefix.', false),
      ],
    };
  }

  return {
    ...common,
    pollPublishedVerified: false,
    draft: {
      poll: clonePoll(content.poll),
      textOptionsOnly: true,
    },
    humanReview: {
      poll: clonePoll(content.poll),
      textOptionsOnly: true,
      reviewEveryOptionSettingAndDeadline: true,
      doneIsPublishOrAutomaticMessageBoundary: true,
      automaticRetryAfterUncertainPublishAllowed: false,
    },
    requiredUIchecks: [
      ...common.requiredUIchecks,
      check('review_all_options_settings_deadline', 'Visually review every text option, every setting, and the deadline before Done.', false),
      check('done_can_publish_or_message', 'Treat Done as a publish or automatic-message boundary and obtain approval immediately before it.', false),
      check('no_automatic_retry_after_uncertain_publish', 'After an uncertain publish outcome, do not retry automatically; inspect the poll state first.', false),
    ],
  };
}

function commonChecks() {
  return [
    check('fresh_exact_chat_ui', 'Open and freshly verify the exact named chat in LINE UI before continuing.', false),
    check('stop_on_chat_interference', 'Stop without retry if the chat identity changes or another UI interaction interferes.', false),
  ];
}

function check(id, requirement, verified) {
  return { id, requirement, verified };
}

function requireWorkflow(value) {
  if (!WORKFLOWS.includes(value)) {
    invalid('workflow must be one of: mentions, reply, polls.');
  }
  return value;
}

function requireMentionTargets(value) {
  if (!Array.isArray(value) || value.length < 1 || value.length > 20) {
    invalid('mentionTargets must be an array containing 1 through 20 exact display names.');
  }
  const seen = new Set();
  return value.map((target, index) => {
    if (typeof target !== 'string' || !target || target.length > 200 || target.includes('\0')) {
      invalid(`mentionTargets[${index}] must be a nonempty string of at most 200 characters without NUL.`);
    }
    if (target !== target.trim() || /[\r\n\t]/u.test(target)) {
      invalid(`mentionTargets[${index}] must be an exact single-line display name without surrounding whitespace.`);
    }
    if (target.startsWith('@')) {
      invalid(`mentionTargets[${index}] must not start with @; use the display name or All.`);
    }
    const normalized = target.normalize('NFC');
    if (seen.has(normalized)) {
      invalid('mentionTargets must be unique after NFC normalization.');
    }
    seen.add(normalized);
    return target;
  });
}

function requireReplySource(value) {
  const source = requireRecord(value, 'source');
  rejectUnexpectedFields(source, SOURCE_FIELDS, 'source');
  requireAllFields(source, SOURCE_FIELDS, 'source');
  return {
    text: requireNonemptyText(source.text, 'source.text', 10000),
    sender: requireNonemptyText(source.sender, 'source.sender', 200),
    date: requireRealIsoDate(source.date, 'source.date'),
    time: requireReplyTime(source.time),
  };
}

function requirePoll(value, now) {
  const poll = requireRecord(value, 'poll');
  rejectUnexpectedFields(poll, POLL_FIELDS, 'poll');
  requireAllFields(poll, POLL_FIELDS, 'poll');

  return {
    question: requireNonemptyText(poll.question, 'poll.question', 400),
    options: requirePollOptions(poll.options),
    multipleChoice: requireBoolean(poll.multipleChoice, 'poll.multipleChoice'),
    anonymous: requireBoolean(poll.anonymous, 'poll.anonymous'),
    allowAddOptions: requireBoolean(poll.allowAddOptions, 'poll.allowAddOptions'),
    deadline: requireTaipeiDeadline(poll.deadline, now),
  };
}

function requirePollOptions(value) {
  if (!Array.isArray(value) || value.length < 2 || value.length > 20) {
    invalid('poll.options must be an array containing 2 through 20 text options.');
  }
  const seen = new Set();
  return value.map((option, index) => {
    if (typeof option !== 'string' || !option.trim() || option.length > 200 || option.includes('\0')) {
      invalid(`poll.options[${index}] must be a nonempty text string of at most 200 characters without NUL.`);
    }
    const duplicateKey = normalizeWhitespace(option.normalize('NFC'));
    if (seen.has(duplicateKey)) {
      invalid('poll.options must be unique after NFC and whitespace normalization.');
    }
    seen.add(duplicateKey);
    return option;
  });
}

function requireNonemptyText(value, name, maximum) {
  if (typeof value !== 'string' || !value.trim() || value.length > maximum || value.includes('\0')) {
    invalid(`${name} must be a nonempty string of at most ${maximum} characters without NUL.`);
  }
  return value;
}

function requireBoolean(value, name) {
  if (typeof value !== 'boolean') invalid(`${name} must be an explicit boolean.`);
  return value;
}

function requireRealIsoDate(value, name) {
  if (typeof value !== 'string') invalid(`${name} must use strict YYYY-MM-DD.`);
  const match = ISO_DATE.exec(value);
  if (!match || match[0] !== value || !isRealCalendarDate(...match.slice(1).map(Number))) {
    invalid(`${name} must be a real calendar date using strict YYYY-MM-DD.`);
  }
  return value;
}

function requireReplyTime(value) {
  if (typeof value !== 'string') invalid('source.time must use HH:mm or HH:mm:ss.');
  const match = REPLY_TIME.exec(value);
  if (!match || match[0] !== value) invalid('source.time must use HH:mm or HH:mm:ss.');
  const [, hour, minute, second] = match.map(item => item === undefined ? undefined : Number(item));
  if (hour > 23 || minute > 59 || (second !== undefined && second > 59)) {
    invalid('source.time must be a real 24-hour time using HH:mm or HH:mm:ss.');
  }
  return value;
}

function requireTaipeiDeadline(value, now) {
  if (value === null) return null;
  if (typeof value !== 'string') invalid('poll.deadline must be null or YYYY-MM-DDTHH:mm+08:00.');
  const match = TAIPEI_DEADLINE.exec(value);
  if (!match || match[0] !== value) invalid('poll.deadline must be null or strict YYYY-MM-DDTHH:mm+08:00.');
  const [, yearText, monthText, dayText, hourText, minuteText] = match;
  const year = Number(yearText);
  const month = Number(monthText);
  const day = Number(dayText);
  const hour = Number(hourText);
  const minute = Number(minuteText);
  if (!isRealCalendarDate(year, month, day) || hour > 23 || minute > 59) {
    invalid('poll.deadline must contain a real Taipei calendar date and minute.');
  }
  if (taipeiDeadlineEpochMilliseconds(year, month, day, hour, minute) <= requireNow(now)) {
    invalid('poll.deadline must be in the future relative to now.');
  }
  return value;
}

function requireNow(now) {
  if (typeof now !== 'function') invalid('now must be a function returning a valid Date.');
  const value = now();
  if (!(value instanceof Date) || !Number.isFinite(value.getTime())) {
    invalid('now must return a valid Date.');
  }
  return value.getTime();
}

function taipeiDeadlineEpochMilliseconds(year, month, day, hour, minute) {
  // Calendar bounds are checked before this conversion, so no Date rollover is
  // accepted as input validation. The supplied wall time is fixed at UTC+08:00.
  const date = new Date(0);
  date.setUTCFullYear(year, month - 1, day);
  date.setUTCHours(hour - 8, minute, 0, 0);
  return date.getTime();
}

function isRealCalendarDate(year, month, day) {
  if (!Number.isInteger(year) || year < 1 || year > 9999 || !Number.isInteger(month) || !Number.isInteger(day)) return false;
  const daysByMonth = [31, isLeapYear(year) ? 29 : 28, 31, 30, 31, 30, 31, 31, 30, 31, 30, 31];
  return month >= 1 && month <= 12 && day >= 1 && day <= daysByMonth[month - 1];
}

function isLeapYear(year) {
  return year % 4 === 0 && (year % 100 !== 0 || year % 400 === 0);
}

function normalizeWhitespace(value) {
  return value.trim().replace(/\s+/gu, ' ');
}

function requireRecord(value, name) {
  if (!value || typeof value !== 'object' || Array.isArray(value)) {
    invalid(`${name} must be an object.`);
  }
  return value;
}

function rejectUnexpectedFields(value, allowed, name) {
  for (const field of Object.keys(value)) {
    if (!allowed.includes(field)) invalid(`${name} does not allow ${field}.`);
  }
}

function requireAllFields(value, required, name) {
  for (const field of required) {
    if (!Object.hasOwn(value, field)) invalid(`${name} requires ${field}.`);
  }
}

function clonePoll(poll) {
  return { ...poll, options: [...poll.options] };
}

function sha256Canonical(value) {
  return createHash('sha256').update(canonicalJson(value), 'utf8').digest('hex');
}

function canonicalJson(value) {
  if (value === null || typeof value !== 'object') return JSON.stringify(value);
  if (Array.isArray(value)) return `[${value.map(canonicalJson).join(',')}]`;
  return `{${Object.keys(value).sort().map(key => `${JSON.stringify(key)}:${canonicalJson(value[key])}`).join(',')}}`;
}

function invalid(message) {
  throw new LineToolError('LINE_INVALID_ARGUMENT', message);
}
