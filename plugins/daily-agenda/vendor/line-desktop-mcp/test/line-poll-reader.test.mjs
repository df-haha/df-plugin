import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import test from 'node:test';

import {
  LINE_POLL_EXPLICIT_PANEL_PROOF,
  parseLinePollState,
  readOpenLinePollState,
} from '../src/extensions/line-poll-reader.mjs';

const PANEL_TARGET = { pid: 42, window_id: 99 };
const PARENT_TARGET = { pid: 42, window_id: 10 };

function element(element_index, role, label, extra = {}) {
  return { element_index, role, ...(label === undefined ? {} : { label }), ...extra };
}

function observedDocument(children, {
  documentValue = 'https://w.line.me/poll/liff/write?chatId=redacted',
  treeMarkdown,
  elementsComplete,
} = {}) {
  return {
    elements: [
      element(0, 'Window', '投票'),
      // The fixture intentionally redacts the real document URL, which
      // contained a chat identifier. The parser must not use that URL to bind
      // a named chat or emit it as raw text.
      element(7, 'Document', 'LINE Poll', { parent_index: 0, value: documentValue }),
      ...children.map(child => ({ ...child, parent_index: 7 })),
    ],
    ...(typeof treeMarkdown === 'string' ? { tree_markdown: treeMarkdown } : {}),
    ...(typeof elementsComplete === 'boolean' ? { elements_complete: elementsComplete } : {}),
  };
}

// Observed live CUA empty panel, 2026-09-11. This is the only empty-state
// signature treated as observed rather than a generic translation guess.
const OBSERVED_EMPTY_20260911 = observedDocument([
  element(8, 'Heading', '投票'),
  element(9, 'Text', '準備好要建立新的投票了嗎？'),
  element(10, 'Button', '建立新投票'),
]);

// Redacted from a local poll draft capture, captured from the
// task-owned unpublished form on 2026-09-11. Values are direct Edit readback;
// no checked state existed for the setting buttons.
const OBSERVED_DRAFT_20260911 = observedDocument([
  element(8, 'Button', '一般投票'),
  element(9, 'Button', '日期投票'),
  element(10, 'Edit', '請輸入您想要進行投票的問題', { value: 'LINE功能驗證（測試投票，請勿投票）' }),
  element(11, 'Edit', '輸入選項', { value: '測試選項 A' }),
  element(12, 'Edit', '輸入選項', { value: '測試選項 B' }),
  element(13, 'Edit', '輸入選項'),
  element(14, 'Button', '設定結束日期'),
  element(15, 'Button', '2026.9.13(日)'),
  element(16, 'ComboBox', 'Search for option'),
  element(17, 'Text', '18'),
  element(18, 'ComboBox', 'Search for option'),
  element(19, 'Text', '0'),
  element(20, 'Button', '一人多票'),
  element(21, 'Button', '匿名投票'),
  element(22, 'Button', '允許新增選項'),
  element(23, 'Button', '取消'),
  element(24, 'Button', '完成', { enabled: true }),
]);

// These portable fixtures reduce the three live 2026-09-11 captures to the
// exact fields parsed below. URLs, creator identity, and the live poll ID are
// redacted; no test reads a private tmp artifact.
const OBSERVED_PUBLISHED_20260911 = observedPollView();
const OBSERVED_VOTED_20260911 = observedPollView({ voterCount: 1, optionVotes: [1, 0] });
const OBSERVED_ENDED_20260911 = observedPollView({ state: 'ended', voterCount: 1, optionVotes: [1, 0] });

function observedPollView({
  state = 'published',
  voterCount = 0,
  optionVotes = [0, 0],
  title = 'LINE功能驗證（測試投票，請勿投票）',
} = {}) {
  const ended = state === 'ended';
  const status = ended ? '已於9月11日結束' : '剩下2天';
  const voterLabel = ended ? `已有${voterCount}人投票` : `已有${voterCount}人參與投票`;
  const children = ended
    ? [
      element(8, 'Button'),
      element(9, 'Button', 'btn-menu'),
      element(10, 'Text', status),
      element(11, 'Hyperlink', voterLabel),
      element(12, 'Hyperlink', String(optionVotes[0])),
      element(13, 'Hyperlink', String(optionVotes[1])),
    ]
    : [
      element(8, 'Button'),
      element(9, 'Button', 'btn-menu'),
      element(10, 'Text', status),
      element(11, 'Hyperlink', voterLabel),
      element(12, 'Hyperlink', String(optionVotes[0])),
      element(13, 'Button', '測試選項 A'),
      element(14, 'Hyperlink', String(optionVotes[1])),
      element(15, 'Button', '測試選項 B'),
      element(16, 'Button', '結束投票'),
    ];
  return observedDocument(children, {
    documentValue: 'https://w.line.me/poll/liff/view/1234567890123456789?chatId=redacted',
    elementsComplete: false,
    treeMarkdown: observedPollTree({ ended, status, voterLabel, optionVotes, title }),
  });
}

function observedPollTree({ ended, status, voterLabel, optionVotes, title }) {
  const quote = value => JSON.stringify(value);
  const statusIndent = ended ? '      ' : '    ';
  const titleIndent = '    ';
  const row = (position, label, votes, indexedButton) => [
    `      - ListItem ${quote(`${votes}${label}`)}`,
    `        - [${position}] Hyperlink ${quote(String(votes))}`,
    `        - ${indexedButton ? `[${position + 1}] ` : ''}Button ${quote(label)}`,
  ];
  return [
    '- [0] Window "投票"',
    '- [7] Document "LINE Poll" [value="https://w.line.me/poll/liff/view/1234567890123456789?chatId=redacted"]',
    ...(ended ? ['      - Text "投票結果"'] : []),
    `${statusIndent}- [10] Text ${quote(status)}`,
    `${titleIndent}- Text ${quote(title)}`,
    '      - ListItem "建立者：*Test Creator*"',
    `    - [11] Hyperlink ${quote(voterLabel)}`,
    ...row(12, '測試選項 A', optionVotes[0], !ended),
    ...row(ended ? 13 : 14, '測試選項 B', optionVotes[1], !ended),
  ].join('\n');
}

// Synthetic future detail fixture. These labels are deliberately *not* a
// claimed LINE detail format. The parser may preserve bounded raw text after a
// verified caller origin, but must not parse title/options/votes from it.
const SYNTHETIC_FUTURE_DETAIL = observedDocument([
  element(8, 'Heading', 'A future poll title'),
  element(9, 'Text', 'Option one'),
  element(10, 'Text', '7 voters / 8 votes'),
]);

function freshChildOrigin() {
  return {
    kind: 'newly-opened-child',
    feature: 'polls',
    chatName: '測試群組',
    parentChatVerification: 'exact-chat-pane-header',
    parentTarget: PARENT_TARGET,
    panelTarget: PANEL_TARGET,
    snapshotTarget: PANEL_TARGET,
    opened: true,
    featureVerification: 'exact-feature-child-surface',
  };
}

function explicitOrigin(kind) {
  return {
    kind,
    feature: 'polls',
    chatName: '測試群組',
    parentChatVerification: 'cached-caller-confirmed-main-header-crop',
    parentTarget: PARENT_TARGET,
    panelTarget: PANEL_TARGET,
    snapshotTarget: PANEL_TARGET,
    panelVerification: LINE_POLL_EXPLICIT_PANEL_PROOF,
  };
}

const POLL_CHAT_ID = `c${'a'.repeat(32)}`;
const POLL_CHAT_REF = `chat:${createHash('sha256').update(JSON.stringify([POLL_CHAT_ID]), 'utf8').digest('hex').slice(0, 24)}`;

function pollWindow(overrides = {}) {
  return {
    app_name: 'LINE.exe',
    title: '投票',
    pid: PANEL_TARGET.pid,
    window_id: PANEL_TARGET.window_id,
    is_on_screen: true,
    minimized: false,
    ...overrides,
  };
}

function urlBoundDraft(chatId = POLL_CHAT_ID, url = `https://w.line.me/poll/liff/write?chatId=${chatId}`) {
  const state = structuredClone(OBSERVED_DRAFT_20260911);
  state.elements.find(item => item.role === 'Document').value = url;
  state.images = [{ type: 'image', mimeType: 'image/png', data: 'matched-panel-only' }];
  return state;
}

function urlBoundPublished({ ended = false } = {}) {
  const state = structuredClone(ended ? OBSERVED_ENDED_20260911 : OBSERVED_PUBLISHED_20260911);
  const url = `https://w.line.me/poll/liff/view/1234567890123456789?chatId=${POLL_CHAT_ID}`;
  state.elements.find(item => item.role === 'Document').value = url;
  state.tree_markdown = state.tree_markdown.replace('chatId=redacted', `chatId=${POLL_CHAT_ID}`);
  state.images = [{ type: 'image', mimeType: 'image/png', data: 'matched-panel-only' }];
  return state;
}

function fakeCua({ windows = [pollWindow()], state = urlBoundDraft() } = {}) {
  const calls = [];
  const api = {
    async call(name, args) {
      calls.push({ name, args });
      if (name === 'list_windows') return { windows };
      if (name === 'get_window_state') return structuredClone(state);
      throw new Error(`unexpected CUA call ${name}`);
    },
  };
  return { calls, withClient: async callback => callback(api) };
}

test('recognizes only the observed composite empty signature and leaves poll fields unknown', () => {
  const result = parseLinePollState(OBSERVED_EMPTY_20260911);

  assert.equal(result.state, 'empty');
  assert.equal(result.published, null);
  assert.equal(result.origin.binding, 'unverified');
  assert.equal(result.poll.voterCount, null);
  assert.ok(result.unknownFields.includes('poll.voterCount'));
  assert.match(result.rawText.text, /準備好要建立新的投票了嗎？/);
  assert.equal(result.rawText.text.includes('chatId='), false, 'document URL is never emitted');
});

test('parses the observed editor as a draft without converting controls into published settings', () => {
  const result = parseLinePollState(OBSERVED_DRAFT_20260911);

  assert.equal(result.state, 'draft');
  assert.equal(result.published, false);
  assert.equal(result.draft.title, 'LINE功能驗證（測試投票，請勿投票）');
  assert.equal(result.draft.options, null, 'a bounded snapshot does not prove a complete option list');
  assert.deepEqual(result.draft.observedOptionRows, [
    { position: 1, value: '測試選項 A' },
    { position: 2, value: '測試選項 B' },
    { position: 3, value: null },
  ]);
  assert.equal(result.draft.deadline, null);
  assert.equal(result.draft.multipleChoice, null);
  assert.equal(result.draft.anonymous, null);
  assert.equal(result.draft.allowAddOptions, null);
  assert.ok(result.unknownFields.includes('draft.deadline'));
  assert.ok(result.unknownFields.includes('draft.multipleChoice'));
  assert.ok(result.unknownFields.includes('draft.observedOptionRows[2].value'));
  assert.deepEqual(result.evidence.controlsObserved, ['設定結束日期', '一人多票', '匿名投票', '允許新增選項']);
});

test('parses the observed published shape while leaving incomplete option and total-vote claims unknown', () => {
  const result = parseLinePollState(OBSERVED_PUBLISHED_20260911);

  assert.equal(result.state, 'published');
  assert.equal(result.published, true);
  assert.equal(result.poll.title, 'LINE功能驗證（測試投票，請勿投票）');
  assert.equal(result.poll.statusText, '剩下2天');
  assert.equal(result.poll.voterCount, 0);
  assert.deepEqual(result.poll.observedOptionRows, [
    { position: 1, label: '測試選項 A', votes: 0 },
    { position: 2, label: '測試選項 B', votes: 0 },
  ]);
  assert.equal(result.poll.options, null);
  assert.equal(result.poll.totalVotes, null);
  assert.ok(result.unknownFields.includes('poll.options'));
  assert.ok(result.unknownFields.includes('poll.totalVotes'));
  assert.equal(result.unknownFields.includes('poll.voterCount'), false);
  assert.equal(result.evidence.titleEvidence, 'tree-status-title-creator-relation-not-structured-element');
  assert.equal(result.evidence.observedOptionRowsEvidence, 'document-scoped-structured-count-button-pairs');
  assert.equal(result.evidence.optionRowsComplete, false);
});

test('reads the observed post-vote voter and structured option counts without inferring total votes', () => {
  const result = parseLinePollState(OBSERVED_VOTED_20260911);

  assert.equal(result.state, 'published');
  assert.equal(result.published, true);
  assert.equal(result.poll.voterCount, 1);
  assert.deepEqual(result.poll.observedOptionRows, [
    { position: 1, label: '測試選項 A', votes: 1 },
    { position: 2, label: '測試選項 B', votes: 0 },
  ]);
  assert.equal(result.poll.totalVotes, null);
  assert.ok(result.unknownFields.includes('poll.totalVotes'));
});

test('reads the observed ended result through a bounded tree row and structured-count bridge', () => {
  const result = parseLinePollState(OBSERVED_ENDED_20260911);

  assert.equal(result.state, 'ended');
  assert.equal(result.published, true);
  assert.equal(result.poll.title, 'LINE功能驗證（測試投票，請勿投票）');
  assert.equal(result.poll.statusText, '已於9月11日結束');
  assert.equal(result.poll.voterCount, 1);
  assert.deepEqual(result.poll.observedOptionRows, [
    { position: 1, label: '測試選項 A', votes: 1 },
    { position: 2, label: '測試選項 B', votes: 0 },
  ]);
  assert.equal(result.poll.options, null);
  assert.equal(result.poll.totalVotes, null);
  assert.ok(result.unknownFields.includes('poll.options'));
  assert.ok(result.unknownFields.includes('poll.totalVotes'));
  assert.equal(result.evidence.observedOptionRowsEvidence, 'document-scoped-tree-list-item-structured-count-bare-button-relation');
  assert.equal(result.evidence.optionRowsComplete, false);
});

test('published state fails closed outside the observed numeric poll-view route', () => {
  const malformedRoute = structuredClone(OBSERVED_PUBLISHED_20260911);
  malformedRoute.elements.find(item => item.role === 'Document').value = 'https://w.line.me/poll/liff/write?chatId=redacted';

  const result = parseLinePollState(malformedRoute);
  assert.equal(result.state, 'unknown');
  assert.equal(result.published, null);
  assert.equal(result.poll.title, null);
});

test('tree-only titles fail closed for duplicate candidates, invalid depth, or line breaks', () => {
  const cases = [
    snapshot => {
      snapshot.tree_markdown = snapshot.tree_markdown.replace(
        '- [10] Text "剩下2天"',
        '- [10] Text "剩下2天"\n    - [10] Text "剩下2天"',
      );
    },
    snapshot => {
      snapshot.tree_markdown = snapshot.tree_markdown.replace(
        '    - Text "LINE功能驗證（測試投票，請勿投票）"',
        '   - Text "LINE功能驗證（測試投票，請勿投票）"',
      );
    },
    snapshot => {
      snapshot.tree_markdown = snapshot.tree_markdown.replace(
        '"LINE功能驗證（測試投票，請勿投票）"',
        '"LINE功能驗證\\n（測試投票，請勿投票）"',
      );
    },
  ];
  for (const mutate of cases) {
    const snapshot = structuredClone(OBSERVED_PUBLISHED_20260911);
    mutate(snapshot);
    const result = parseLinePollState(snapshot);
    assert.equal(result.state, 'published');
    assert.equal(result.poll.title, null);
    assert.ok(result.unknownFields.includes('poll.title'));
  }
});

test('tree-derived titles become unknown when the structured status index is reused', () => {
  for (const [fixture, expectedState] of [
    [OBSERVED_PUBLISHED_20260911, 'published'],
    [OBSERVED_ENDED_20260911, 'ended'],
  ]) {
    const snapshot = structuredClone(fixture);
    const status = snapshot.elements.find(item => item.element_index === 10);
    snapshot.elements.push({ ...status, label: 'unrelated duplicate status' });
    const result = parseLinePollState(snapshot);
    assert.equal(result.state, expectedState);
    assert.equal(result.poll.title, null);
    assert.ok(result.unknownFields.includes('poll.title'));
  }
});

test('ended tree rows reject a moved child, mismatched list-item count, or duplicate count index', () => {
  const cases = [
    snapshot => {
      snapshot.tree_markdown = snapshot.tree_markdown.replace(
        '        - Button "測試選項 A"',
        '          - Button "測試選項 A"',
      );
    },
    snapshot => {
      snapshot.tree_markdown = snapshot.tree_markdown.replace('"1測試選項 A"', '"0測試選項 A"');
    },
    snapshot => {
      const count = snapshot.elements.find(item => item.element_index === 12);
      snapshot.elements.push({ ...count });
    },
    snapshot => {
      snapshot.tree_markdown += '\n        - [12] Hyperlink "1"';
    },
  ];
  for (const mutate of cases) {
    const snapshot = structuredClone(OBSERVED_ENDED_20260911);
    mutate(snapshot);
    const result = parseLinePollState(snapshot);
    assert.equal(result.state, 'unknown');
    assert.equal(result.published, null);
    assert.deepEqual(result.poll.observedOptionRows, []);
  }
});

test('ended rows and state fail closed when the structured voter anchor index is reused', () => {
  const snapshot = structuredClone(OBSERVED_ENDED_20260911);
  const voter = snapshot.elements.find(item => item.element_index === 11);
  snapshot.elements.push({ ...voter, label: 'unrelated duplicate voter' });

  const result = parseLinePollState(snapshot);
  assert.equal(result.state, 'unknown');
  assert.equal(result.published, null);
  assert.deepEqual(result.poll.observedOptionRows, []);
});

test('missing direct title value remains unknown and an enabled completion button cannot create a draft by itself', () => {
  const titleMissing = structuredClone(OBSERVED_DRAFT_20260911);
  delete titleMissing.elements.find(item => item.label === '請輸入您想要進行投票的問題').value;
  const missingResult = parseLinePollState(titleMissing);
  assert.equal(missingResult.state, 'draft');
  assert.equal(missingResult.draft.title, null);
  assert.ok(missingResult.unknownFields.includes('draft.title'));

  const onlyComplete = observedDocument([
    element(8, 'Edit', '請輸入您想要進行投票的問題'),
    element(9, 'Button', '完成', { enabled: true }),
  ]);
  const onlyCompleteResult = parseLinePollState(onlyComplete);
  assert.equal(onlyCompleteResult.state, 'unknown');
  assert.equal(onlyCompleteResult.published, null);
});

test('a verified fresh child can describe a nonempty unrecognized panel as a list, while empty evidence wins', () => {
  const list = parseLinePollState(SYNTHETIC_FUTURE_DETAIL, { origin: freshChildOrigin() });
  assert.equal(list.state, 'list');
  assert.equal(list.origin.binding, 'caller-verified-parent-chat');
  assert.equal(list.origin.chatName, '測試群組');

  const empty = parseLinePollState(OBSERVED_EMPTY_20260911, { origin: freshChildOrigin() });
  assert.equal(empty.state, 'empty');
});

test('a synthetic future detail is context-only: its candidate title, option and vote text stay unknown', () => {
  const result = parseLinePollState(SYNTHETIC_FUTURE_DETAIL, {
    origin: explicitOrigin('explicit-detail-read'),
  });

  assert.equal(result.state, 'detail');
  assert.equal(result.poll.title, null);
  assert.equal(result.poll.options, null);
  assert.equal(result.poll.voterCount, null);
  assert.equal(result.poll.totalVotes, null);
  assert.ok(result.unknownFields.includes('poll.totalVotes'));
  assert.match(result.rawText.text, /7 voters \/ 8 votes/);
});

test('a title-only child window never binds a poll panel to the supplied chat name', () => {
  const result = parseLinePollState(SYNTHETIC_FUTURE_DETAIL, {
    origin: {
      kind: 'newly-opened-child',
      feature: 'polls',
      chatName: '測試群組',
      panelTitle: 'LINE Poll',
      panelTarget: PANEL_TARGET,
    },
  });

  assert.equal(result.state, 'unknown');
  assert.equal(result.origin.binding, 'unverified');
  assert.equal(result.origin.chatName, null);
  assert.equal(result.origin.reason, 'missing-verified-parent-chat-proof');
});

test('a proof capsule with a stale snapshot target is unbound before list/detail inference', () => {
  const origin = freshChildOrigin();
  origin.snapshotTarget = { pid: PANEL_TARGET.pid, window_id: PANEL_TARGET.window_id + 1 };
  const result = parseLinePollState(SYNTHETIC_FUTURE_DETAIL, { origin });

  assert.equal(result.state, 'unknown');
  assert.equal(result.origin.binding, 'unverified');
  assert.equal(result.origin.reason, 'snapshot-target-does-not-match-panel');
});

test('a stale snapshot exposing complete empty and draft signatures is ambiguous, never empty', () => {
  const stale = structuredClone(OBSERVED_DRAFT_20260911);
  stale.elements.push(
    { ...element(30, 'Heading', '投票'), parent_index: 7 },
    { ...element(31, 'Text', '準備好要建立新的投票了嗎？'), parent_index: 7 },
    { ...element(32, 'Button', '建立新投票'), parent_index: 7 },
  );
  const result = parseLinePollState(stale);

  assert.equal(result.state, 'unknown');
  assert.equal(result.published, null);
  assert.equal(result.evidence.stateSignature, 'ambiguous-empty-and-draft');
});

test('readOpenLinePollState reads only the matched poll panel and returns an opaque local-identity proof', async () => {
  const fake = fakeCua();
  const result = await readOpenLinePollState({
    chatName: '測試群組',
    chatRef: POLL_CHAT_REF,
    includeScreenshot: true,
  }, { withClient: fake.withClient });

  assert.equal(result.state, 'draft');
  assert.equal(result.origin.binding, 'local-chat-ref-and-poll-url');
  assert.deepEqual(result.source, {
    proof: 'scoped-poll-url-and-local-chat-ref',
    pollUrlScope: 'https-w-line-me-poll-liff-prefix',
    localIdentity: 'matched',
    screenshot: 'matched-panel-only',
  });
  assert.deepEqual(result.images, [{ type: 'image', mimeType: 'image/png', data: 'matched-panel-only' }]);
  assert.deepEqual(fake.calls.map(call => call.name), ['list_windows', 'get_window_state']);
  assert.equal(fake.calls[1].args.include_screenshot, true);
  assert.equal(fake.calls[1].args.pid, PANEL_TARGET.pid);
  assert.equal(fake.calls[1].args.window_id, PANEL_TARGET.window_id);
  assert.equal(JSON.stringify(result).includes(POLL_CHAT_ID), false);
  assert.equal(JSON.stringify(result).includes('chatId='), false);
});

test('readOpenLinePollState retains the URL/chat-ref guard for a parsed published view', async () => {
  const fake = fakeCua({ state: urlBoundPublished() });
  const result = await readOpenLinePollState(
    { chatName: '測試群組', chatRef: POLL_CHAT_REF, includeScreenshot: true },
    { withClient: fake.withClient },
  );

  assert.equal(result.state, 'published');
  assert.equal(result.origin.binding, 'local-chat-ref-and-poll-url');
  assert.equal(result.poll.voterCount, 0);
  assert.deepEqual(result.poll.observedOptionRows, [
    { position: 1, label: '測試選項 A', votes: 0 },
    { position: 2, label: '測試選項 B', votes: 0 },
  ]);
  assert.deepEqual(fake.calls.map(call => call.name), ['list_windows', 'get_window_state']);
  assert.equal(JSON.stringify(result).includes(POLL_CHAT_ID), false);
  assert.equal(JSON.stringify(result).includes('chatId='), false);
});

test('readOpenLinePollState excludes URL-like descendant labels and values from raw text', async () => {
  const state = urlBoundPublished();
  const secretUrl = `https://w.line.me/poll/liff/view/1234567890123456789?chatId=${POLL_CHAT_ID}`;
  state.elements.push(
    { ...element(30, 'Text', secretUrl), parent_index: 7 },
    { ...element(31, 'Edit', 'untrusted accessibility value', { value: `note?chatId=${POLL_CHAT_ID}` }), parent_index: 7 },
  );
  const fake = fakeCua({ state });
  const result = await readOpenLinePollState(
    { chatName: '測試群組', chatRef: POLL_CHAT_REF },
    { withClient: fake.withClient },
  );

  assert.equal(result.state, 'published');
  assert.equal(result.rawText.text.includes(POLL_CHAT_ID), false);
  assert.equal(result.rawText.text.includes('chatId='), false);
  assert.equal(JSON.stringify(result).includes(POLL_CHAT_ID), false);
  assert.equal(JSON.stringify(result).includes('chatId='), false);
});

test('readOpenLinePollState rejects a wrong local group before returning text or images', async () => {
  const fake = fakeCua();
  const wrongRef = `chat:${'b'.repeat(24)}`;
  await assert.rejects(
    readOpenLinePollState({ chatName: '其他群組', chatRef: wrongRef, includeScreenshot: true }, { withClient: fake.withClient }),
    error => {
      assert.equal(error?.code, 'LINE_POLL_CHAT_REF_MISMATCH');
      assert.equal(JSON.stringify({ message: error?.message, details: error?.details }).includes(POLL_CHAT_ID), false);
      assert.equal(JSON.stringify({ message: error?.message, details: error?.details }).includes('chatId='), false);
      assert.equal('rawText' in error, false);
      assert.equal('images' in error, false);
      return true;
    },
  );
  assert.deepEqual(fake.calls.map(call => call.name), ['list_windows', 'get_window_state']);
});

test('readOpenLinePollState fails closed for an untrusted host or duplicate chatId without input calls', async () => {
  for (const url of [
    `https://example.test/poll/liff/write?chatId=${POLL_CHAT_ID}`,
    `https://W.LINE.ME/poll/liff/write?chatId=${POLL_CHAT_ID}`,
    `https://w.line.me/poll/liff/write?chatId=${POLL_CHAT_ID}&chatId=${POLL_CHAT_ID}`,
  ]) {
    const fake = fakeCua({ state: urlBoundDraft(POLL_CHAT_ID, url) });
    await assert.rejects(
      readOpenLinePollState({ chatName: '測試群組', chatRef: POLL_CHAT_REF, includeScreenshot: true }, { withClient: fake.withClient }),
      error => {
        assert.equal(error?.code, 'LINE_POLL_URL_UNVERIFIED');
        const publicError = JSON.stringify({ message: error?.message, details: error?.details });
        assert.equal(publicError.includes(POLL_CHAT_ID), false);
        assert.equal(publicError.includes('chatId='), false);
        assert.equal('rawText' in error, false);
        assert.equal('images' in error, false);
        return true;
      },
    );
    assert.deepEqual(fake.calls.map(call => call.name), ['list_windows', 'get_window_state']);
  }
});

test('readOpenLinePollState refuses duplicate poll windows before snapshotting either one', async () => {
  const fake = fakeCua({ windows: [pollWindow(), pollWindow({ window_id: 100 })] });
  await assert.rejects(
    readOpenLinePollState({ chatName: '測試群組', chatRef: POLL_CHAT_REF }, { withClient: fake.withClient }),
    { code: 'LINE_POLL_WINDOW_NOT_UNIQUE' },
  );
  assert.deepEqual(fake.calls.map(call => call.name), ['list_windows']);
});

test('readOpenLinePollState omits screenshots unless explicitly requested after a successful identity match', async () => {
  const fake = fakeCua();
  const result = await readOpenLinePollState(
    { chatName: '測試群組', chatRef: POLL_CHAT_REF },
    { withClient: fake.withClient },
  );
  assert.equal('images' in result, false);
  assert.equal(result.source.screenshot, 'not-requested');
  assert.equal(fake.calls[1].args.include_screenshot, false);
});

test('bounds document-scoped visual-assistance text without leaking document URLs', () => {
  const result = parseLinePollState(OBSERVED_DRAFT_20260911, { rawTextLimit: 24 });
  assert.equal(Array.from(result.rawText.text).length, 24);
  assert.equal(result.rawText.truncated, true);
  assert.equal(result.rawText.text.includes('chatId='), false);
  assert.throws(() => parseLinePollState(OBSERVED_EMPTY_20260911, { rawTextLimit: 0 }), /rawTextLimit/);
});
