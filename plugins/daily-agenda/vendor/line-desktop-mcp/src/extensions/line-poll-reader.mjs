import { createHash } from 'node:crypto';

import { lineWindows, snapshot, withCuaClient } from './cua-line-client.mjs';
import { LineToolError, requireChat } from './line-runtime.mjs';

/**
 * Pure, read-only interpretation of a CUA `get_window_state` poll panel.
 *
 * This deliberately recognizes only the state signatures observed in the
 * current Windows LINE client on 2026-09-11: the empty poll landing page, the
 * ordinary-poll editor, an open ordinary poll, and that poll after it ended.
 * List and detail are caller-origin states until a real accessibility capture
 * establishes their own stable signatures.
 *
 * No result from this module is a server read. In particular, a missing voter
 * count is unknown, an enabled `完成` button does not mean a poll was
 * published, and a panel title alone cannot bind the panel to a named chat.
 */

export const LINE_POLL_ORIGIN_KINDS = Object.freeze([
  'newly-opened-child',
  'explicit-list-read',
  'explicit-detail-read',
  'draft-flow',
  'local-chat-ref-and-poll-url',
]);

/** A caller may use this exact marker only after its own fresh target checks. */
export const LINE_POLL_EXPLICIT_PANEL_PROOF = 'same-operation-verified-poll-panel';

const DEFAULT_RAW_TEXT_LIMIT = 1200;
const MAX_RAW_TEXT_LIMIT = 4000;

const OBSERVED = Object.freeze({
  document: 'LINE Poll',
  emptyHeading: '投票',
  emptyText: '準備好要建立新的投票了嗎？',
  createButton: '建立新投票',
  draftQuestion: '請輸入您想要進行投票的問題',
  draftOption: '輸入選項',
  draftCancel: '取消',
  draftComplete: '完成',
  deadlineToggle: '設定結束日期',
  multipleChoice: '一人多票',
  anonymous: '匿名投票',
  allowAddOptions: '允許新增選項',
  publishedEnd: '結束投票',
  endedHeading: '投票結果',
});

const POLL_FIELDS = Object.freeze([
  'title',
  'options',
  'voterCount',
  'totalVotes',
  'statusText',
  'deadline',
  'multipleChoice',
  'anonymous',
  'allowAddOptions',
]);

const DRAFT_FIELDS = Object.freeze([
  'title',
  'options',
  'deadline',
  'multipleChoice',
  'anonymous',
  'allowAddOptions',
]);

const FRESH_CHILD_PROOFS = new Set([
  'exact-feature-child-window-title',
  'exact-feature-child-surface',
]);

/**
 * Parse an already-captured CUA state. `origin` is a proof capsule made by a
 * higher-level UI operation; this parser validates its shape but cannot prove
 * that a caller constructed it honestly. Keep target equality and the fresh
 * parent-chat guard in that higher-level operation.
 *
 * @param {object} snapshot CUA `get_window_state` result with `elements`.
 * @param {{origin?: object, rawTextLimit?: number}} options
 * @returns {object} a conservative poll-panel observation
 */
export function parseLinePollState(snapshot, { origin, rawTextLimit = DEFAULT_RAW_TEXT_LIMIT } = {}) {
  const limit = requireRawTextLimit(rawTextLimit);
  const elements = Array.isArray(snapshot?.elements) ? snapshot.elements.filter(isObject) : [];
  const document = findUniquePollDocument(elements);
  const panelElements = document ? descendantsOf(document, elements) : [];
  const tree = document ? documentTree(snapshot?.tree_markdown, document, panelElements) : blankTree();
  const normalizedOrigin = normalizeOrigin(origin);

  const empty = document ? observeEmptyPanel(panelElements) : null;
  const draft = document ? observeDraftPanel(panelElements) : null;
  const publishedPanel = document ? observePublishedPanel(document, panelElements, tree) : null;
  const endedPanel = document ? observeEndedPanel(document, panelElements, tree) : null;
  const state = chooseState({
    document,
    empty,
    draft,
    publishedPanel,
    endedPanel,
    origin: normalizedOrigin,
  });
  const draftResult = state === 'draft' ? draft.result : blankDraft();
  const pollResult = state === 'published'
    ? publishedPanel.result
    : state === 'ended'
      ? endedPanel.result
      : blankPoll();
  const published = state === 'draft' ? false : ['published', 'ended'].includes(state) ? true : null;
  const unknownFields = collectUnknownFields({
    state,
    poll: pollResult,
    draft: draftResult,
    published,
  });
  const signatures = completedSignatures({ empty, draft, publishedPanel, endedPanel });
  const activePoll = state === 'published' ? publishedPanel : state === 'ended' ? endedPanel : null;

  return {
    state,
    // False only means that the observed UI is an editor. True requires one
    // observed live poll signature; neither value says anything about another
    // poll that is not currently rendered in this panel.
    published,
    origin: normalizedOrigin.public,
    poll: pollResult,
    draft: draftResult,
    unknownFields,
    rawText: document
      ? boundedPanelText(panelElements, limit)
      : { text: '', truncated: false, source: 'poll-document-unavailable' },
    evidence: {
      pollDocument: document ? 'exact-accessibility-document-label' : 'unavailable-or-ambiguous',
      stateSignature: signatures.length > 1 ? `ambiguous-${signatures.join('-and-')}` : state,
      exactMatches: [
        ...(empty?.matches || []),
        ...(draft?.matches || []),
        ...(publishedPanel?.matches || []),
        ...(endedPanel?.matches || []),
      ],
      // These only say that a control was exposed. The current CUA tree has
      // no checked state for them, so no setting value is inferred.
      controlsObserved: draft?.controlsObserved || [],
      ...(activePoll
        ? {
          statusText: activePoll.result.statusText,
          titleEvidence: activePoll.result.title === null
            ? 'unavailable-or-ambiguous-tree-status-title-creator-relation'
            : 'tree-status-title-creator-relation-not-structured-element',
          observedOptionRowsEvidence: state === 'published'
            ? 'document-scoped-structured-count-button-pairs'
            : 'document-scoped-tree-list-item-structured-count-bare-button-relation',
          optionRowsComplete: false,
          totalVotesEvidence: 'unknown-elements-incomplete',
        }
        : {}),
    },
  };
}

/**
 * Read the one visible Poll child window only after its WebView URL's opaque
 * local-chat reference matches the caller's already-scoped local reader
 * identity. This function never opens, selects, changes, votes, or publishes
 * anything. It returns no snapshot text or image until that match succeeds.
 */
export async function readOpenLinePollState(
  { chatName, chatRef, includeScreenshot = false } = {},
  { withClient = withCuaClient } = {},
) {
  requireChat(chatName);
  requireChatRef(chatRef);
  if (typeof includeScreenshot !== 'boolean') {
    throw new LineToolError('LINE_INVALID_ARGUMENT', 'includeScreenshot must be a boolean.');
  }
  if (typeof withClient !== 'function') throw new TypeError('withClient must be a function.');

  return withClient(async api => {
    const panelTarget = await uniqueVisiblePollTarget(api);
    const panelState = await snapshot(api, panelTarget, { screenshot: includeScreenshot });
    const observedChatRef = chatRefFromPollDocument(panelState);
    if (observedChatRef !== chatRef) {
      throw new LineToolError(
        'LINE_POLL_CHAT_REF_MISMATCH',
        'The open LINE poll panel does not match the requested local chat reference.',
      );
    }

    const result = parseLinePollState(panelState, {
      origin: {
        kind: 'local-chat-ref-and-poll-url',
        feature: 'polls',
        chatName,
        expectedChatRef: chatRef,
        observedChatRef,
      },
    });
    return {
      ...result,
      source: {
        proof: 'scoped-poll-url-and-local-chat-ref',
        pollUrlScope: 'https-w-line-me-poll-liff-prefix',
        localIdentity: 'matched',
        screenshot: includeScreenshot ? 'matched-panel-only' : 'not-requested',
      },
      ...(includeScreenshot ? { images: Array.isArray(panelState.images) ? panelState.images : [] } : {}),
    };
  });
}

function chooseState({ document, empty, draft, publishedPanel, endedPanel, origin }) {
  if (!document) return 'unknown';
  // Qt/WebView transitions can briefly expose old and new trees together.
  // Neither state wins until a fresh unambiguous snapshot arrives.
  const signatures = completedSignatures({ empty, draft, publishedPanel, endedPanel });
  if (signatures.length > 1) return 'unknown';
  if (signatures.length === 1) return signatures[0];
  if (!origin.verified) return 'unknown';
  if (origin.kind === 'newly-opened-child' || origin.kind === 'explicit-list-read') return 'list';
  if (origin.kind === 'explicit-detail-read') return 'detail';
  return 'unknown';
}

function completedSignatures({ empty, draft, publishedPanel, endedPanel }) {
  return [
    ...(empty?.complete ? ['empty'] : []),
    ...(draft?.complete ? ['draft'] : []),
    ...(publishedPanel?.complete ? ['published'] : []),
    ...(endedPanel?.complete ? ['ended'] : []),
  ];
}

function observeEmptyPanel(elements) {
  const heading = exactRoleLabel(elements, 'heading', OBSERVED.emptyHeading);
  const text = exactRoleLabel(elements, 'text', OBSERVED.emptyText);
  const button = exactRoleLabel(elements, 'button', OBSERVED.createButton);
  const matches = [
    ...(heading.length === 1 ? [OBSERVED.emptyHeading] : []),
    ...(text.length === 1 ? [OBSERVED.emptyText] : []),
    ...(button.length === 1 ? [OBSERVED.createButton] : []),
  ];
  return { complete: heading.length === 1 && text.length === 1 && button.length === 1, matches };
}

function observeDraftPanel(elements) {
  const question = exactRoleLabel(elements, 'edit', OBSERVED.draftQuestion);
  const options = exactRoleLabel(elements, 'edit', OBSERVED.draftOption);
  const cancel = exactRoleLabel(elements, 'button', OBSERVED.draftCancel);
  const complete = exactRoleLabel(elements, 'button', OBSERVED.draftComplete);
  const controlsObserved = [
    OBSERVED.deadlineToggle,
    OBSERVED.multipleChoice,
    OBSERVED.anonymous,
    OBSERVED.allowAddOptions,
  ].filter(label => exactRoleLabel(elements, 'button', label).length === 1);

  // The signature intentionally needs a document-scoped question input,
  // multiple option inputs, and both terminal controls. A title label or an
  // enabled completion button alone is never enough to call this a draft.
  const completeSignature = question.length === 1
    && options.length >= 2
    && cancel.length === 1
    && complete.length === 1;
  const title = uniqueDirectValue(question);
  const observedOptionRows = options.map((element, index) => ({
    position: index + 1,
    value: directValue(element),
  }));
  return {
    complete: completeSignature,
    controlsObserved,
    matches: [
      ...(question.length === 1 ? [OBSERVED.draftQuestion] : []),
      ...(options.length >= 2 ? [OBSERVED.draftOption] : []),
      ...(cancel.length === 1 ? [OBSERVED.draftCancel] : []),
      ...(complete.length === 1 ? [OBSERVED.draftComplete] : []),
    ],
    result: {
      title,
      // A bounded accessibility snapshot does not prove that every option row
      // was included. Expose seen rows separately instead of fabricating a
      // complete draft option list.
      options: null,
      observedOptionRows,
      deadline: null,
      multipleChoice: null,
      anonymous: null,
      allowAddOptions: null,
    },
  };
}

function observePublishedPanel(document, elements, tree) {
  const status = uniqueTextByPattern(elements, /^剩下[0-9]+天$/u);
  const voters = uniqueVoterCount(elements, /^已有([0-9]+)人參與投票$/u);
  const end = exactRoleLabel(elements, 'button', OBSERVED.publishedEnd);
  const observedOptionRows = voters && end.length === 1
    ? structuredOptionRows(elements, voters.element, end[0])
    : [];
  const title = status ? treeTitleAfterStatus(tree, status.element) : null;
  const complete = hasObservedPollViewRoute(document)
    && status !== null
    && voters !== null
    && end.length === 1
    && observedOptionRows.length >= 2;
  return {
    complete,
    matches: [
      ...(status ? [status.element.label] : []),
      ...(voters ? [voters.element.label] : []),
      ...(end.length === 1 ? [OBSERVED.publishedEnd] : []),
      ...(title === null ? [] : ['tree-status-title-creator-relation']),
      ...(observedOptionRows.length >= 2 ? ['structured-option-count-button-pairs'] : []),
    ],
    result: observedPollResult({ title, status, voters, observedOptionRows }),
  };
}

function observeEndedPanel(document, elements, tree) {
  const status = uniqueTextByPattern(elements, /^已於[0-9]+月[0-9]+日結束$/u);
  const voters = uniqueVoterCount(elements, /^已有([0-9]+)人投票$/u);
  const resultsHeading = tree.nodes.filter(node => node.role === 'text'
    && node.index === null
    && node.label === OBSERVED.endedHeading);
  const end = exactRoleLabel(elements, 'button', OBSERVED.publishedEnd);
  const title = status ? treeTitleAfterStatus(tree, status.element) : null;
  const observedOptionRows = voters ? treeOptionRowsAfterVoterCount(tree, voters.element) : [];
  const complete = hasObservedPollViewRoute(document)
    && status !== null
    && voters !== null
    && resultsHeading.length === 1
    && end.length === 0
    && observedOptionRows.length >= 2;
  return {
    complete,
    matches: [
      ...(resultsHeading.length === 1 ? [OBSERVED.endedHeading] : []),
      ...(status ? [status.element.label] : []),
      ...(voters ? [voters.element.label] : []),
      ...(title === null ? [] : ['tree-status-title-creator-relation']),
      ...(observedOptionRows.length >= 2 ? ['tree-option-structured-count-bare-button-relations'] : []),
    ],
    // The option label is bare tree text, but the count remains a structured
    // CUA element. Each row must bind both to the same tree ListItem before it
    // becomes an observed row; the incomplete snapshot still never proves a
    // complete option list or total vote count.
    result: observedPollResult({ title, status, voters, observedOptionRows }),
  };
}

function observedPollResult({ title, status, voters, observedOptionRows }) {
  return {
    ...blankPoll(),
    title,
    voterCount: voters?.count ?? null,
    statusText: status?.element?.label ?? null,
    observedOptionRows,
  };
}

function uniqueTextByPattern(elements, pattern) {
  const matches = elements.flatMap(element => {
    if (roleName(element) !== 'text') return [];
    return exactLabels(element).some(label => pattern.test(label)) ? [{ element }] : [];
  });
  return matches.length === 1 ? matches[0] : null;
}

function uniqueVoterCount(elements, pattern) {
  const matches = elements.flatMap(element => {
    if (roleName(element) !== 'hyperlink') return [];
    return exactLabels(element).flatMap(label => {
      const match = label.match(pattern);
      const count = match ? visibleInteger(match[1]) : null;
      return count === null ? [] : [{ element, count }];
    });
  });
  return matches.length === 1 ? matches[0] : null;
}

function structuredOptionRows(elements, voterElement, endElement) {
  const ordered = elements
    .map(element => ({ element, index: integer(elementIndex(element)) }))
    .filter(item => item.index !== null)
    .sort((left, right) => left.index - right.index);
  const voterAt = ordered.findIndex(item => item.element === voterElement);
  const endAt = ordered.findIndex(item => item.element === endElement);
  if (voterAt < 0 || endAt < voterAt + 3) return [];

  const rows = [];
  for (let cursor = voterAt + 1; cursor + 1 < endAt; cursor += 2) {
    const count = ordered[cursor];
    const option = ordered[cursor + 1];
    const votes = uniqueVisibleIntegerLabel(count.element, 'hyperlink');
    const label = uniqueVisibleTextLabel(option.element, 'button');
    if (count.index + 1 !== option.index || votes === null || label === null) return [];
    rows.push({ position: rows.length + 1, label, votes });
  }
  return rows;
}

function uniqueVisibleIntegerLabel(element, role) {
  if (roleName(element) !== role) return null;
  const values = [...new Set(exactLabels(element).map(visibleInteger).filter(value => value !== null))];
  return values.length === 1 ? values[0] : null;
}

function uniqueVisibleTextLabel(element, role) {
  if (roleName(element) !== role) return null;
  const values = [...new Set(exactLabels(element).filter(validVisibleText))];
  return values.length === 1 ? values[0] : null;
}

function visibleInteger(value) {
  if (typeof value !== 'string' || !/^(?:0|[1-9][0-9]*)$/u.test(value)) return null;
  const number = Number(value);
  return Number.isSafeInteger(number) ? number : null;
}

function documentTree(treeMarkdown, document, elements) {
  if (typeof treeMarkdown !== 'string' || treeMarkdown.length === 0 || treeMarkdown.length > 80_000) {
    return blankTree();
  }
  const documentIndex = integer(elementIndex(document));
  if (documentIndex === null) return blankTree();
  const lines = treeMarkdown.split(/\r?\n/u);
  const parsed = lines.map(parseTreeLine);
  const roots = parsed.flatMap((node, lineIndex) => node !== null
    && node.index === documentIndex
    && node.role === 'document'
    && node.label === OBSERVED.document
    ? [{ ...node, lineIndex }]
    : []);
  if (roots.length !== 1) return blankTree();

  const root = roots[0];
  const nodes = [];
  for (let index = root.lineIndex + 1; index < parsed.length; index += 1) {
    const node = parsed[index];
    if (node === null) {
      if (lines[index].length === 0 && index === parsed.length - 1) continue;
      return blankTree();
    }
    if (node.indent <= root.indent) break;
    nodes.push(node);
  }
  const indexedElements = elements
    .map(element => [integer(elementIndex(element)), element])
    .filter(([index]) => index !== null);
  const indexCounts = new Map();
  for (const [index] of indexedElements) {
    indexCounts.set(index, (indexCounts.get(index) || 0) + 1);
  }
  return {
    rootIndent: root.indent,
    nodes,
    byIndex: new Map(indexedElements),
    indexCounts,
  };
}

function blankTree() {
  return { rootIndent: null, nodes: [], byIndex: new Map(), indexCounts: new Map() };
}

function parseTreeLine(line) {
  const match = /^([ \t]*)-\s+(?:\[([0-9]+)\]\s+)?([A-Za-z][A-Za-z ]*)(?:\s+"((?:\\.|[^"\\])*)")?(?:\s+\[[^\r\n]*\])?\s*$/u.exec(line);
  if (!match) return null;
  const index = match[2] === undefined ? null : visibleInteger(match[2]);
  if (match[2] !== undefined && index === null) return null;
  const label = match[4] === undefined ? null : decodeTreeLabel(match[4]);
  if (match[4] !== undefined && label === null) return null;
  return {
    indent: match[1].length,
    index,
    role: normalizeRole(match[3]),
    label,
  };
}

function decodeTreeLabel(value) {
  try { return JSON.parse(`"${value}"`); }
  catch { return null; }
}

function treeTitleAfterStatus(tree, statusElement) {
  const statusNodes = tree.nodes.filter(node => treeNodeMatchesElement(node, statusElement));
  if (statusNodes.length !== 1 || !treeNodeMatchesUniqueElement(tree, statusNodes[0])) return null;
  const statusAt = tree.nodes.indexOf(statusNodes[0]);
  const title = tree.nodes[statusAt + 1];
  const creator = tree.nodes[statusAt + 2];
  if (!title || !creator
    || title.role !== 'text'
    || title.index !== null
    || title.indent <= tree.rootIndent
    || ![0, 2].includes(statusNodes[0].indent - title.indent)
    || !validVisibleText(title.label)
    || creator.role !== 'listitem'
    || typeof creator.label !== 'string'
    || !creator.label.startsWith('建立者：')
    || creator.indent !== title.indent + 2
    || treeParentAt(tree.nodes, statusAt + 2) !== statusAt + 1) {
    return null;
  }
  return title.label;
}

function treeOptionRowsAfterVoterCount(tree, voterElement) {
  const voterNodes = tree.nodes.filter(node => treeNodeMatchesElement(node, voterElement));
  if (voterNodes.length !== 1 || !treeNodeMatchesUniqueElement(tree, voterNodes[0])) return [];
  const voterAt = tree.nodes.indexOf(voterNodes[0]);
  const rows = [];
  for (let cursor = voterAt + 1; cursor + 2 < tree.nodes.length; cursor += 3) {
    const item = tree.nodes[cursor];
    const count = tree.nodes[cursor + 1];
    const option = tree.nodes[cursor + 2];
    const votes = count ? visibleInteger(count.label) : null;
    if (!item || treeParentAt(tree.nodes, cursor) !== voterAt) return rows;
    if (!count || !option
      || item.role !== 'listitem'
      || item.indent !== voterNodes[0].indent + 2
      || count.role !== 'hyperlink'
      || !treeNodeMatchesUniqueElement(tree, count)
      || treeParentAt(tree.nodes, cursor + 1) !== cursor
      || option.role !== 'button'
      || option.index !== null
      || treeParentAt(tree.nodes, cursor + 2) !== cursor
      || count.indent !== item.indent + 2
      || option.indent !== item.indent + 2
      || votes === null
      || !validVisibleText(option.label)
      || item.label !== `${count.label}${option.label}`) return [];
    rows.push({ position: rows.length + 1, label: option.label, votes });
  }
  return rows;
}

function treeNodeMatchesUniqueElement(tree, node) {
  if (node.index === null || tree.nodes.filter(candidate => candidate.index === node.index).length !== 1) {
    return false;
  }
  const element = tree.byIndex.get(node.index);
  return tree.indexCounts.get(node.index) === 1
    && element !== undefined
    && treeNodeMatchesElement(node, element);
}

function treeParentAt(nodes, childAt) {
  const child = nodes[childAt];
  if (!child) return null;
  for (let index = childAt - 1; index >= 0; index -= 1) {
    if (nodes[index].indent < child.indent) return index;
  }
  return null;
}

function treeNodeMatchesElement(node, element) {
  const index = integer(elementIndex(element));
  return index !== null
    && node.index === index
    && node.role === roleName(element)
    && exactLabels(element).includes(node.label);
}

function hasObservedPollViewRoute(document) {
  if (typeof document?.value !== 'string') return false;
  let url;
  try { url = new URL(document.value); }
  catch { return false; }
  return document.value.startsWith('https://w.line.me/')
    && url.protocol === 'https:'
    && url.hostname === 'w.line.me'
    && url.port === ''
    && url.username === ''
    && url.password === ''
    && url.hash === ''
    && /^\/poll\/liff\/view\/[0-9]+$/u.test(url.pathname);
}

function blankPoll() {
  return {
    ...Object.fromEntries(POLL_FIELDS.map(field => [field, null])),
    // The live CUA snapshots were incomplete, so these are visible rows, not
    // a claim that every poll option is present.
    observedOptionRows: [],
  };
}

function blankDraft() {
  return {
    title: null,
    options: null,
    observedOptionRows: [],
    deadline: null,
    multipleChoice: null,
    anonymous: null,
    allowAddOptions: null,
  };
}

function collectUnknownFields({ state, poll, draft, published }) {
  const unknown = POLL_FIELDS
    .filter(field => poll[field] === null)
    .map(field => `poll.${field}`);
  if (published === null) unknown.push('published');
  if (['published', 'ended'].includes(state) && poll.observedOptionRows.length === 0) {
    unknown.push('poll.observedOptionRows');
  }
  if (state === 'draft') {
    for (const field of DRAFT_FIELDS) {
      if (draft[field] === null) unknown.push(`draft.${field}`);
    }
    for (const row of draft.observedOptionRows) {
      if (row.value === null) unknown.push(`draft.observedOptionRows[${row.position - 1}].value`);
    }
  }
  return unknown;
}

function normalizeOrigin(origin) {
  const unverified = (reason) => ({
    kind: null,
    verified: false,
    public: { kind: null, chatName: null, binding: 'unverified', reason },
  });
  if (!isObject(origin)) return unverified('no-caller-origin');
  if (!LINE_POLL_ORIGIN_KINDS.includes(origin.kind)) return unverified('unsupported-origin-kind');
  if (origin.feature !== 'polls') return unverified('origin-not-for-polls');
  if (!exactChatName(origin.chatName)) return unverified('missing-or-invalid-chat-name');

  if (origin.kind === 'local-chat-ref-and-poll-url') {
    if (!validChatRef(origin.expectedChatRef) || !validChatRef(origin.observedChatRef)
      || origin.expectedChatRef !== origin.observedChatRef) {
      return unverified('local-chat-reference-mismatch');
    }
    return {
      kind: origin.kind,
      verified: true,
      public: {
        kind: origin.kind,
        chatName: origin.chatName,
        binding: 'local-chat-ref-and-poll-url',
        panelRelation: 'scoped-poll-url-and-local-identity',
      },
    };
  }

  if (!validParentProof(origin.parentChatVerification) || !validTarget(origin.parentTarget)) {
    return unverified('missing-verified-parent-chat-proof');
  }
  // `snapshotTarget` is the target used for the actual get_window_state call;
  // requiring it to match avoids treating a remembered child title as the
  // current panel. The caller still owns the live CUA target comparison.
  if (!validTarget(origin.panelTarget)) return unverified('missing-panel-target');
  if (!validTarget(origin.snapshotTarget) || !sameTarget(origin.panelTarget, origin.snapshotTarget)) {
    return unverified('snapshot-target-does-not-match-panel');
  }

  if (origin.kind === 'newly-opened-child') {
    if (origin.opened !== true || !FRESH_CHILD_PROOFS.has(origin.featureVerification)) {
      return unverified('missing-fresh-child-feature-proof');
    }
    if (sameTarget(origin.parentTarget, origin.panelTarget)) {
      return unverified('new-child-target-matches-parent');
    }
  } else if (origin.panelVerification !== LINE_POLL_EXPLICIT_PANEL_PROOF) {
    return unverified('missing-explicit-panel-origin-proof');
  }

  return {
    kind: origin.kind,
    verified: true,
    public: {
      kind: origin.kind,
      chatName: origin.chatName,
      binding: 'caller-verified-parent-chat',
      ...(origin.kind === 'newly-opened-child'
        ? { panelRelation: 'fresh-child-window' }
        : { panelRelation: 'same-operation-explicit-panel' }),
    },
  };
}

function validParentProof(value) {
  return typeof value === 'string' && (
    value === 'exact-top-level-window-title'
    || value.startsWith('exact-chat-pane-')
    || value.startsWith('grounded-ocr-main-')
    || value === 'cached-caller-confirmed-main-header-crop'
  );
}

function validTarget(value) {
  return Number.isInteger(value?.pid) && value.pid >= 0
    && Number.isInteger(value?.window_id) && value.window_id >= 0;
}

function sameTarget(left, right) {
  return left.pid === right.pid && left.window_id === right.window_id;
}

function exactChatName(value) {
  return typeof value === 'string'
    && value.length > 0
    && value.length <= 200
    && value === value.trim()
    && !/[\r\n\t\0]/u.test(value);
}

function validVisibleText(value) {
  return typeof value === 'string'
    && value.length > 0
    && value.length <= 400
    && value === value.trim()
    && !/[\r\n\t\0]/u.test(value);
}

function requireChatRef(value) {
  if (!validChatRef(value)) {
    throw new LineToolError('LINE_INVALID_ARGUMENT', 'chatRef must be an opaque local chat reference.');
  }
  return value;
}

function validChatRef(value) {
  return typeof value === 'string' && /^chat:[0-9a-f]{24}$/u.test(value);
}

async function uniqueVisiblePollTarget(api) {
  const candidates = (await lineWindows(api)).filter(window => window?.title === '投票' || window?.title === 'Polls');
  if (candidates.length !== 1) {
    throw new LineToolError('LINE_POLL_WINDOW_NOT_UNIQUE', 'Expected exactly one open LINE poll window.');
  }
  const window = candidates[0];
  if (window.minimized === true || window.is_on_screen !== true) {
    throw new LineToolError('LINE_POLL_WINDOW_UNAVAILABLE', 'The LINE poll window is not visible for a fresh read.');
  }
  if (!validTarget(window)) {
    throw new LineToolError('LINE_UI_BACKEND_PROTOCOL', 'CUA returned an invalid LINE poll window target.');
  }
  return { pid: window.pid, window_id: window.window_id };
}

function chatRefFromPollDocument(snapshot) {
  const elements = Array.isArray(snapshot?.elements) ? snapshot.elements.filter(isObject) : [];
  const document = findUniquePollDocument(elements);
  if (!document || typeof document.value !== 'string') {
    throw new LineToolError('LINE_POLL_URL_UNVERIFIED', 'The LINE poll panel did not expose one verifiable poll document URL.');
  }
  const rawUrl = document.value;
  let url;
  try { url = new URL(rawUrl); }
  catch {
    throw new LineToolError('LINE_POLL_URL_UNVERIFIED', 'The LINE poll panel did not expose one verifiable poll document URL.');
  }
  const chatIds = url.searchParams.getAll('chatId');
  if (!rawUrl.startsWith('https://w.line.me/')
    || url.protocol !== 'https:'
    || url.hostname !== 'w.line.me'
    || url.port !== ''
    || url.username !== ''
    || url.password !== ''
    || url.hash !== ''
    || !url.pathname.startsWith('/poll/liff/')
    || chatIds.length !== 1
    || !validPollChatId(chatIds[0])) {
    throw new LineToolError('LINE_POLL_URL_UNVERIFIED', 'The LINE poll panel did not expose one verifiable poll document URL.');
  }
  return opaqueChatRef(chatIds[0]);
}

function validPollChatId(value) {
  return typeof value === 'string' && /^c[0-9a-f]{32}$/u.test(value);
}

function opaqueChatRef(chatId) {
  return `chat:${createHash('sha256').update(JSON.stringify([chatId]), 'utf8').digest('hex').slice(0, 24)}`;
}

function findUniquePollDocument(elements) {
  const matches = elements.filter(element => isDocumentLike(element)
    && exactLabels(element).includes(OBSERVED.document));
  return matches.length === 1 ? matches[0] : null;
}

function descendantsOf(root, elements) {
  const rootIndex = integer(elementIndex(root));
  if (rootIndex === null) return [];
  const byIndex = new Map(elements
    .map(element => [integer(elementIndex(element)), element])
    .filter(([index]) => index !== null));
  return elements.filter(element => element === root || isDescendantOf(element, rootIndex, byIndex));
}

function isDescendantOf(element, rootIndex, byIndex) {
  const seen = new Set();
  let current = integer(element.parent_index);
  while (current !== null && !seen.has(current) && seen.size < 50) {
    if (current === rootIndex) return true;
    seen.add(current);
    current = integer(byIndex.get(current)?.parent_index);
  }
  return false;
}

function exactRoleLabel(elements, role, label) {
  return elements.filter(element => roleName(element) === role && exactLabels(element).includes(label));
}

function isDocumentLike(element) {
  const roles = [element?.role, element?.semantic_role, element?.semanticRole]
    .map(normalizeRole);
  return roles.includes('document') || roles.includes('rootwebarea') || roles.includes('webarea');
}

function roleName(element) {
  return normalizeRole(element?.role);
}

function normalizeRole(value) {
  return typeof value === 'string' ? value.trim().toLowerCase().replace(/[\s_-]+/gu, '') : '';
}

function exactLabels(element) {
  return [element?.label, element?.name]
    .filter(value => typeof value === 'string')
    .map(value => value.trim());
}

function directValue(element) {
  return typeof element?.value === 'string' && element.value.length > 0 && !sensitivePollReference(element.value)
    ? element.value
    : null;
}

function uniqueDirectValue(elements) {
  const values = [...new Set(elements.map(directValue).filter(value => value !== null))];
  return values.length === 1 ? values[0] : null;
}

function boundedPanelText(elements, limit) {
  const chunks = [];
  const seen = new Set();
  for (const element of elements) {
    for (const text of [...exactLabels(element), ...(isEditableText(element) ? [directValue(element)] : [])]) {
      if (typeof text !== 'string' || !text || sensitivePollReference(text) || seen.has(text)) continue;
      seen.add(text);
      chunks.push(text);
    }
  }
  return boundText(chunks.join('\n'), limit);
}

function isEditableText(element) {
  return ['edit', 'textarea', 'combobox', 'text'].includes(roleName(element));
}

function boundText(value, limit) {
  const codePoints = Array.from(value);
  if (codePoints.length <= limit) return { text: value, truncated: false, source: 'document-scoped-accessibility-text' };
  return {
    text: `${codePoints.slice(0, Math.max(0, limit - 1)).join('')}…`,
    truncated: true,
    source: 'document-scoped-accessibility-text',
  };
}

function looksLikeUrl(value) {
  return /^https?:\/\//iu.test(value);
}

function sensitivePollReference(value) {
  return typeof value === 'string'
    && (looksLikeUrl(value) || /(?:^|[?&\s])chatId=/iu.test(value));
}

function requireRawTextLimit(value) {
  if (!Number.isInteger(value) || value < 1 || value > MAX_RAW_TEXT_LIMIT) {
    throw new TypeError(`rawTextLimit must be an integer from 1 through ${MAX_RAW_TEXT_LIMIT}.`);
  }
  return value;
}

function elementIndex(element) {
  return element?.element_index;
}

function integer(value) {
  return Number.isInteger(value) ? value : null;
}

function isObject(value) {
  return !!value && typeof value === 'object' && !Array.isArray(value);
}
