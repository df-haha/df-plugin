import assert from 'node:assert/strict';
import test from 'node:test';
import fs from 'node:fs';

import { LineUi } from '../src/extensions/line-ui.mjs';

const CHAT = '測試♋️';
const CHAT_REF = 'chat:0123456789abcdef01234567';
const LIVE_MAIN_SHELL = JSON.parse(fs.readFileSync(
  new URL('./fixtures/live-window-structure-v2-20260928.json', import.meta.url), 'utf8'))
  .find(item => item.window.window_id === 68616);
const liveMainShellState = () => ({
  elements: LIVE_MAIN_SHELL.elements,
  ...LIVE_MAIN_SHELL.stateMeta,
});

function localGuiIdentity(chatName = CHAT, kind = 'direct', chatRef = CHAT_REF) {
  return {
    chatName, chatRef, count: 0, messages: [],
    chatIdentity: { kind, displayName: chatName, uiIdentityVerified: false, guiDisplayNameUnique: true },
    scope: { kind: 'local_gui_chat_identity' },
  };
}
const TARGET = {
  app_name: 'LINE.exe',
  title: 'LINE',
  pid: 42,
  window_id: 99,
  bounds: { x: 0, y: 0, width: 400, height: 600 },
  is_on_screen: true,
  minimized: false,
};

// Sanitized geometry from the current main LINE window. Its right-pane header
// reaches one UIA pixel beyond the screenshot root (1385 vs 1384).
const CURRENT_MAIN_TARGET = {
  ...TARGET,
  bounds: { x: 645, y: 293, width: 739, height: 777 },
};

function element(index, values = {}) {
  return {
    element_index: index,
    element_token: `token-${index}-${values.tokenSuffix ?? ''}`,
    enabled: true,
    ...values,
  };
}

function header() {
  return headerFor(CHAT);
}

function headerFor(chatName) {
  return element(1, {
    label: chatName,
    role: 'Header',
    parent_index: 52,
    frame: { x: 110, y: 10, w: 80, h: 19 },
  });
}

function composer(value = '') {
  return element(2, { role: 'Edit', semantic_role: 'composer', value });
}

function message(text, index = 3) {
  return element(index, { label: text, semantic_role: 'message', role: 'Text' });
}

function quote(text, index = 4) {
  return element(index, { label: text, semantic_role: 'reply-quote', role: 'Text' });
}

function state(elements, suffix = '') {
  return {
    snapshot_id: `snapshot-${suffix}`,
    elements,
  };
}

function replySourceVisualState({
  text,
  chatType = 'direct',
  chatLabel,
  hash = 'a',
  suffix = '',
  includeMessage = false,
  duplicateMessage = false,
  includeQuote = false,
  menu,
  draft = '',
} = {}) {
  const elements = [
    element(0, { role: 'Window', frame: { x: 0, y: 0, w: 400, h: 600 } }),
    element(40, { role: 'Group', parent_index: 0, frame: { x: 100, y: 0, w: 300, h: 600 } }),
    element(41, { role: 'Group', parent_index: 40, frame: { x: 100, y: 0, w: 300, h: 50 } }),
    element(1, { label: chatLabel ?? (chatType === 'group' ? `${CHAT} (2)` : CHAT), role: 'Header', parent_index: 41, frame: { x: 110, y: 10, w: 80, h: 19 } }),
    element(42, { role: 'Group', parent_index: 40, frame: { x: 100, y: 50, w: 300, h: 550 } }),
    element(2, { role: 'Edit', semantic_role: 'composer', parent_index: 42, frame: { x: 110, y: 500, w: 280, h: 60 }, value: draft }),
    element(5, { role: 'Group', label: 'qt_scrollarea_viewport', parent_index: 2, frame: { x: 110, y: 500, w: 280, h: 60 } }),
  ];
  if (includeMessage) {
    elements.push(element(3, {
      label: text,
      semantic_role: 'message',
      role: 'Text',
      parent_index: 42,
      frame: { x: 160, y: 180, w: 140, h: 72 },
    }));
  }
  if (duplicateMessage) {
    elements.push(element(4, {
      label: text,
      semantic_role: 'message',
      role: 'Text',
      parent_index: 42,
      frame: { x: 180, y: 190, w: 120, h: 72 },
    }));
  }
  if (includeQuote) elements.push(quote(text, 6));
  if (menu) elements.push(element(8, { label: menu, role: 'MenuItem' }));
  return {
    ...state(elements, suffix),
    mainStructure: false,
    images: [visualImage(hash)],
    screenshot_width: 400,
    screenshot_height: 600,
  };
}

function mainStructure() {
  return [
    element(47, { role: 'Group', frame: { x: 0, y: 0, w: 400, h: 600 } }),
    element(48, { role: 'Group', parent_index: 47, frame: { x: 100, y: 0, w: 300, h: 600 } }),
    element(52, { role: 'Group', parent_index: 48, frame: { x: 100, y: 0, w: 300, h: 50 } }),
    element(62, { role: 'Group', parent_index: 48, frame: { x: 100, y: 50, w: 300, h: 550 } }),
    element(72, { role: 'Edit', parent_index: 62, frame: { x: 110, y: 500, w: 280, h: 60 } }),
    element(73, { role: 'Group', label: 'qt_scrollarea_viewport', parent_index: 72 }),
  ];
}

function mainHeaderToolbar({ includeSeparator = false, includeUnrelatedMore = false } = {}) {
  const controls = [
    header(),
    element(10, { role: 'Group', parent_index: 52, frame: { x: 280, y: 10, w: 24, h: 24 } }),
    element(11, { role: 'Group', parent_index: 52, frame: { x: 310, y: 10, w: 24, h: 24 } }),
    element(12, { role: 'Group', parent_index: 52, frame: { x: 340, y: 10, w: 24, h: 24 } }),
    element(13, { role: 'Group', parent_index: 52, frame: { x: 370, y: 10, w: 16, h: 24 } }),
  ];
  if (includeSeparator) controls.push(
    element(61, { role: 'Group', parent_index: 48, frame: { x: 100, y: 49, w: 300, h: 1 } }),
  );
  if (includeUnrelatedMore) controls.push(
    element(30, { label: 'More', role: 'Button', parent_index: 47, frame: { x: 10, y: 10, w: 24, h: 24 } }),
  );
  return controls;
}

function mainSearchBar() {
  return [
    element(140, { role: 'Group', parent_index: 62, frame: { x: 100, y: 50, w: 300, h: 450 } }),
    element(141, { role: 'Group', parent_index: 140, frame: { x: 100, y: 50, w: 300, h: 56 } }),
    element(142, { role: 'Group', parent_index: 141, frame: { x: 110, y: 62, w: 230, h: 32 } }),
    element(143, { role: 'Group', parent_index: 142, frame: { x: 116, y: 66, w: 24, h: 24 } }),
    element(144, { role: 'Edit', parent_index: 142, frame: { x: 140, y: 63, w: 200, h: 30 } }),
    element(145, { role: 'Group', parent_index: 142, frame: { x: 340, y: 62, w: 1, h: 32 } }),
  ];
}

function outsideMainSearchEdit() {
  return element(160, { role: 'Edit', parent_index: 47, frame: { x: 8, y: 60, w: 80, h: 30 } });
}

function visualImage(hash = 'a') {
  return {
    type: 'image',
    mimeType: 'image/png',
    data: `source-${hash}`,
    width: 400,
    height: 600,
    hash,
  };
}

function screenshotState(elements, suffix = '') {
  return {
    ...state(elements, suffix),
    images: [visualImage(suffix || 'a')],
    screenshot_width: 400,
    screenshot_height: 600,
  };
}

function nestedMainElements(draft = '') {
  return [
    element(0, { role: 'Window', frame: { x: 0, y: 0, w: 400, h: 600 } }),
    element(51, { role: 'Group', parent_index: 0, frame: { x: 100, y: 50, w: 300, h: 550 } }),
    element(52, { role: 'Group', parent_index: 51, frame: { x: 100, y: 50, w: 300, h: 50 } }),
    element(53, { role: 'Group', parent_index: 52, frame: { x: 112, y: 65, w: 72, h: 19 } }),
    element(54, { role: 'Group', parent_index: 52, frame: { x: 184, y: 65, w: 23, h: 19 } }),
    element(62, { role: 'Group', parent_index: 51, frame: { x: 100, y: 100, w: 300, h: 1 } }),
    element(63, { role: 'Group', parent_index: 51, frame: { x: 100, y: 100, w: 300, h: 500 } }),
    element(64, { role: 'Custom', parent_index: 63, frame: { x: 100, y: 100, w: 300, h: 500 } }),
    element(65, { role: 'Group', parent_index: 64, frame: { x: 100, y: 450, w: 300, h: 150 } }),
    element(72, { role: 'Group', parent_index: 65, frame: { x: 100, y: 460, w: 300, h: 80 } }),
    element(73, { role: 'Edit', semantic_role: 'composer', parent_index: 72, frame: { x: 110, y: 465, w: 280, h: 60 }, value: draft }),
    element(74, { role: 'Group', label: 'qt_scrollarea_viewport', parent_index: 73 }),
  ];
}

function nestedVisualState(draft = '', suffix = '', hash = 'a') {
  return {
    ...state(nestedMainElements(draft), suffix),
    mainStructure: false,
    images: [visualImage(hash)],
    screenshot_width: 400,
    screenshot_height: 600,
  };
}

function currentMainVisualImage(hash = 'a') {
  return {
    type: 'image',
    mimeType: 'image/png',
    data: `current-main-${hash}`,
    width: 739,
    height: 777,
    hash,
  };
}

function currentMainVisualState({
  suffix = '',
  hash = 'a',
  headerWidth = 375,
  editorValue,
  editorLabel,
  includeGlobalSearch = false,
  extraMainComposer = false,
  includeModal = false,
} = {}) {
  const editor = {
    role: 'Edit',
    parent_index: 71,
    frame: { x: 1020, y: 956, w: 357, h: 76 },
    ...(editorValue === undefined ? {} : { value: editorValue }),
    ...(editorLabel === undefined ? {} : { label: editorLabel }),
  };
  const elements = [
    element(0, { role: 'Window', frame: { x: 645, y: 293, w: 739, h: 777 } }),
    element(51, { role: 'Group', parent_index: 0, frame: { x: 1010, y: 344, w: 375, h: 726 } }),
    element(52, { role: 'Group', parent_index: 51, frame: { x: 1010, y: 348, w: headerWidth, h: 52 } }),
    element(53, { role: 'Group', parent_index: 52, frame: { x: 1022, y: 364, w: 49, h: 19 } }),
    element(54, { role: 'Group', parent_index: 52, frame: { x: 1071, y: 364, w: 20, h: 19 } }),
    element(61, { role: 'Group', parent_index: 51, frame: { x: 1010, y: 400, w: 375, h: 1 } }),
    element(62, { role: 'Group', parent_index: 51, frame: { x: 1010, y: 401, w: 375, h: 669 } }),
    element(63, { role: 'Custom', parent_index: 62, frame: { x: 1010, y: 401, w: 375, h: 669 } }),
    element(64, { role: 'Group', parent_index: 63, frame: { x: 1010, y: 950, w: 375, h: 120 } }),
    element(71, { role: 'Group', parent_index: 64, frame: { x: 1010, y: 950, w: 375, h: 82 } }),
    element(72, editor),
    element(73, { role: 'Group', label: 'qt_scrollarea_viewport', parent_index: 72, frame: { x: 1020, y: 956, w: 357, h: 76 } }),
  ];
  if (extraMainComposer) {
    elements.push(
      element(80, { role: 'Edit', parent_index: 71, frame: { x: 1020, y: 956, w: 300, h: 76 } }),
      element(81, { role: 'Group', label: 'qt_scrollarea_viewport', parent_index: 80, frame: { x: 1020, y: 956, w: 300, h: 76 } }),
    );
  }
  if (includeGlobalSearch) {
    elements.push(
      element(108, { role: 'Group', parent_index: 0, frame: { x: 707, y: 344, w: 302, h: 576 } }),
      element(109, { role: 'Edit', parent_index: 108, frame: { x: 719, y: 356, w: 264, h: 38 }, value: 'sidebar query' }),
    );
  }
  if (includeModal) elements.push(element(90, { role: 'Dialog', parent_index: 0 }));
  return {
    ...state(elements, suffix),
    mainStructure: false,
    images: [currentMainVisualImage(hash)],
    screenshot_width: 739,
    screenshot_height: 777,
  };
}

function visualHelpers() {
  return {
    recognizeImage: async image => ({
      coordinateSpace: 'input-png-pixels',
      scaleFactor: 1,
      width: image.width,
      height: image.height,
      lines: [],
    }),
    imageDimensions: image => ({ width: image.width, height: image.height }),
    fingerprintRegion: async (image, region, { includeImage = false } = {}) => ({
      sha256: image.hash.repeat(64),
      width: region.width,
      height: region.height,
      region: { ...region },
      ...(includeImage ? {
        image: {
          type: 'image',
          mimeType: 'image/png',
          data: `crop-${image.hash}`,
          width: region.width,
          height: region.height,
          hash: image.hash,
        },
      } : {}),
    }),
  };
}

function fakeEnvironment({
  states = [],
  windows = [TARGET],
  handlers = {},
  tools = [
    'list_windows',
    'get_window_state',
    'click',
    'right_click',
    'set_value',
    'hotkey',
    'clipboard_read',
  ],
  recognizeImage,
  findImageLabel,
  fingerprintRegion,
  imageDimensions,
  randomToken,
  now,
  innerAutomation,
  activateLine,
  trace,
  readChatIdentity,
} = {}) {
  const calls = [];
  const automationCalls = [];
  const locks = [];
  const queue = [...states];
  const api = {
    tools: new Set(tools),
    serverVersion: 'fake-cua',
    async call(name, args) {
      trace?.push(name);
      calls.push({ name, args });
      if (handlers[name]) return handlers[name](args, calls);
      if (name === 'list_windows') return { windows };
      if (name === 'get_window_state') {
        if (queue.length === 0) throw new Error('unexpected get_window_state');
        const next = queue.shift();
        if (args.window_id === TARGET.window_id && next?.mainStructure !== false) {
          const { mainStructure: _ignored, ...result } = next;
          return { ...result, elements: [...result.elements, ...mainStructure()] };
        }
        return next;
      }
      return { effect: 'confirmed', route: 'accessibility' };
    },
  };
  const automation = {
    async activateLine() {
      automationCalls.push('activate');
      return activateLine ? activateLine() : { success: true };
    },
    async selectChat(name) {
      automationCalls.push(['select', name]);
      return true;
    },
    async pageUp(times) {
      automationCalls.push(['pageUp', times]);
    },
    ...(innerAutomation ? { automation: innerAutomation } : {}),
  };
  const mockedReadChatIdentity = readChatIdentity ?? (async ({ chatName }) => localGuiIdentity(chatName,
    states[0]?.elements?.some(item => item.label === `${chatName} (2)`) ? 'group' : 'direct'));
  const ui = new LineUi({
    automation,
    readChatIdentity: mockedReadChatIdentity,
    readNamedIdentity: async ({ chatName }) => {
      const result = await mockedReadChatIdentity({ chatName });
      if (result?.chatName !== chatName || !/^chat:[0-9a-f]{24}$/u.test(result?.chatRef ?? '')
          || !['direct', 'group'].includes(result?.chatIdentity?.kind)
          || result.chatIdentity.displayName !== chatName || result.chatIdentity.uiIdentityVerified !== false
          || result.chatIdentity.guiDisplayNameUnique !== true || result.count !== 0
          || result.scope?.kind !== 'local_gui_chat_identity'
          || !Array.isArray(result.messages) || result.messages.length !== 0) {
        throw Object.assign(new Error('Synthetic identity proof is incomplete.'), { code: 'LINE_CHAT_IDENTITY_UNVERIFIED' });
      }
      return { chatRef: result.chatRef, kind: result.chatIdentity.kind };
    },
    withClient: async callback => callback(api),
    runOperation: async (kind, callback) => {
      locks.push(kind);
      return callback();
    },
    ...(recognizeImage ? { recognizeImage } : {}),
    ...(findImageLabel ? { findImageLabel } : {}),
    ...(fingerprintRegion ? { fingerprintRegion } : {}),
    ...(imageDimensions ? { imageDimensions } : {}),
    ...(randomToken ? { randomToken } : {}),
    ...(now ? { now } : {}),
  });
  return { ui, calls, automation, automationCalls, locks, api };
}

function actionCalls(calls) {
  return calls.filter(call => !['list_windows', 'get_window_state'].includes(call.name));
}

test('ambiguous or unavailable local GUI identity refuses every chat entry before CUA or legacy actions', async () => {
  for (const code of ['CHAT_AMBIGUOUS', 'GUI_IDENTITY_UNAVAILABLE', 'ENGINE_DLL_UNCONFIGURED']) {
    for (const method of ['openChat', 'getState', 'getDraft', 'setDraft', 'clearDraft', 'readLegacyHistory', 'stageFile', 'openFeature', 'messageAction', 'getReplySourceTarget']) {
      const { ui, calls, automationCalls } = fakeEnvironment({
        states: Array.from({ length: 10 }, () => state([header(), composer('')])),
        readChatIdentity: async () => { throw Object.assign(new Error('identity refused'), { code }); },
      });
      await assert.rejects(ui[method]({
        chatName: CHAT, message: 'authorized draft', expectedDraft: '', autoSend: false,
        filePath: 'C:\\safe-test.txt', feature: 'search', messageText: 'source', action: 'copy',
        chatType: 'direct', chatRef: CHAT_REF,
        ...(method === 'getReplySourceTarget' ? {
          source: { sourceRef: 'message:0123456789abcdef01234567', text: 'source', sender: 'Tester', date: '2026-09-11', time: '10:22:33' },
        } : {}),
      }), { code });
      assert.deepEqual(actionCalls(calls), [], `${method} must not perform CUA input`);
      assert.deepEqual(automationCalls, [], `${method} must not call the legacy backend`);
    }
  }
});

test('malformed GUI identity proof cannot be used as a unique chat', async () => {
  for (const defect of [
    { chatRef: 'raw-private-id' },
    { chatName: 'Different chat' },
    { chatIdentity: { ...localGuiIdentity().chatIdentity, guiDisplayNameUnique: false } },
    { count: 1, messages: [{ text: 'unexpected' }] },
    { scope: { kind: 'local_chat_identity' } },
  ]) {
    const { ui, calls } = fakeEnvironment({
      states: [state([header()])],
      readChatIdentity: async () => ({ ...localGuiIdentity(), ...defect }),
    });
    await assert.rejects(ui.openChat({ chatName: CHAT }), { code: 'LINE_CHAT_IDENTITY_UNVERIFIED' });
    assert.deepEqual(calls, []);
  }
});

test('does not treat a sidebar or global-search hit as an active chat header', async () => {
  const { ui, calls, automationCalls, locks } = fakeEnvironment({
    windows: [LIVE_MAIN_SHELL.window],
    states: [liveMainShellState(), liveMainShellState(),
      ...Array.from({ length: 4 }, (_, index) => state([
        element(1, { label: CHAT, role: 'ListItem', semantic_role: 'search-result' }),
        element(2, { label: CHAT, role: 'Edit', value: CHAT }),
      ], `generic-${index}`))],
  });

  await assert.rejects(ui.openChat({ chatName: CHAT }), { code: 'LINE_CHAT_UNVERIFIED' });
  assert.deepEqual(automationCalls, ['activate']);
  assert.deepEqual(locks, ['ui-open-chat']);
  assert.deepEqual(actionCalls(calls), []);
});

test('unverified GUI entry points never open the first unverified search result', async () => {
  for (const method of ['openChat', 'getState', 'getDraft', 'readLegacyHistory']) {
    const { ui, calls, automationCalls } = fakeEnvironment({
      windows: method === 'openChat' ? [LIVE_MAIN_SHELL.window] : [TARGET],
      states: [...(method === 'openChat' ? [liveMainShellState(), liveMainShellState()] : []),
        ...Array.from({ length: 6 }, (_, index) =>
          state([headerFor('Another chat'), composer('')], `unverified-${index}`))],
    });
    await assert.rejects(ui[method]({ chatName: CHAT, message: 'not staged', autoSend: false }),
      error => ['LINE_CHAT_UNVERIFIED', 'LINE_REPLY_SOURCE_CHAT_TYPE_UNVERIFIED'].includes(error?.code));
    assert.equal(automationCalls.every(call => call === 'activate'), true, method);
    assert.deepEqual(actionCalls(calls), [], method);
  }
});

test('accepts an exact detached LINE window title without mistaking a sidebar entry for a header', async () => {
  const detached = { ...TARGET, title: CHAT, window_id: 100 };
  const { ui } = fakeEnvironment({
    windows: [TARGET, detached],
    states: [state([], 'detached')],
  });

  const result = await ui.openChat({ chatName: CHAT });
  assert.equal(result.success, true);
  assert.equal(result.verification, 'exact-top-level-window-title');
  assert.equal(result.confidence, 'high');
});

test('uses the observed detached rich-composer structure to distinguish an empty draft from unreadable UI', async () => {
  const detached = { ...TARGET, title: CHAT, window_id: 100 };
  const emptyComposer = () => [
    element(12, { role: 'Edit' }),
    element(13, { role: 'Group', label: 'qt_scrollarea_viewport', parent_index: 12 }),
  ];
  const draftedComposer = () => [
    element(12, { role: 'Edit', label: 'draft preserved by readback', value: 'draft preserved by readback' }),
    element(13, { role: 'Group', label: 'qt_scrollarea_viewport', parent_index: 12 }),
  ];
  const { ui, calls } = fakeEnvironment({
    windows: [detached],
    states: [
      state(emptyComposer(), 'inspect'),
      state(emptyComposer(), 'initial-read'),
      state(emptyComposer(), 'write-before'),
      state(draftedComposer(), 'write-after'),
    ],
  });

  const result = await ui.setDraft({ chatName: CHAT, message: 'draft preserved by readback' });
  assert.equal(result.changed, true);
  assert.equal(result.verification.draft, 'detached-composer-value-readback');
  const write = calls.find(call => call.name === 'set_value');
  assert.match(write.args.element_token, /^token-12/);
  assert.equal('delivery_mode' in write.args, false);
});

test('getStatus returns only metadata, not window titles or UI state', async () => {
  const privateTitle = { ...TARGET, title: 'private chat title' };
  const { ui, locks } = fakeEnvironment({ windows: [privateTitle] });

  const result = await ui.getStatus();
  assert.equal(result.appName, 'LINE.exe');
  assert.equal(result.lineWindowCount, 1);
  assert.equal(result.visibleLineWindowCount, 1);
  assert.equal(JSON.stringify(result).includes('private chat title'), false);
  assert.deepEqual(locks, ['ui-get-status']);
});

test('setDraft rejects drift before calling a mutating CUA tool', async () => {
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header(), composer('existing draft')], 'inspect'),
      state([header(), composer('existing draft')], 'write-before'),
    ],
  });

  await assert.rejects(
    ui.setDraft({ chatName: CHAT, message: 'replacement', expectedDraft: 'stale draft' }),
    { code: 'LINE_DRAFT_CONFLICT' },
  );
  assert.equal(calls.some(call => call.name === 'set_value'), false);
  assert.equal(calls.some(call => call.name === 'press_key'), false);
});

test('setDraft uses snapshot-bound background set_value and verifies the full readback without Enter', async () => {
  const { ui, calls, locks } = fakeEnvironment({
    states: [
      state([header(), composer('')], 'inspect'),
      state([header(), composer('')], 'initial-read'),
      state([header(), composer('')], 'write-before'),
      state([header(), composer('ready to review')], 'write-after'),
    ],
  });

  const result = await ui.setDraft({ chatName: CHAT, message: 'ready to review' });
  assert.equal(result.changed, true);
  assert.equal(result.verification.draft, 'exact-composer-value');
  const write = calls.find(call => call.name === 'set_value');
  assert.deepEqual(write.args.value, 'ready to review');
  assert.equal('delivery_mode' in write.args, false);
  assert.match(write.args.element_token, /^token-2/);
  assert.equal(calls.some(call => call.name === 'press_key'), false);
  assert.deepEqual(locks, ['ui-set-draft']);
  assert.deepEqual(calls.map(call => call.name), [
    'list_windows', 'get_window_state', 'get_window_state', 'get_window_state', 'set_value', 'get_window_state',
  ]);
});

test('a no-op draft write is not reported as success', async () => {
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header(), composer('')], 'inspect'),
      state([header(), composer('')], 'initial-read'),
      state([header(), composer('')], 'write-before'),
      state([header(), composer('')], 'write-after'),
    ],
  });

  await assert.rejects(
    ui.setDraft({ chatName: CHAT, message: 'not reflected' }),
    error => error?.code === 'LINE_DRAFT_WRITE_UNVERIFIED' && error?.operationMayHaveCompleted === true,
  );
  assert.equal(calls.filter(call => call.name === 'set_value').length, 1);
  assert.equal(calls.some(call => call.name === 'press_key'), false);
});

test('a CUA write failure stops without an ungrounded follow-up action', async () => {
  const expected = new Error('driver refused write');
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header(), composer('')], 'inspect'),
      state([header(), composer('')], 'initial-read'),
      state([header(), composer('')], 'write-before'),
    ],
    handlers: {
      set_value: () => { throw expected; },
    },
  });

  await assert.rejects(ui.setDraft({ chatName: CHAT, message: 'will not retry' }), error => error === expected);
  assert.deepEqual(calls.map(call => call.name), [
    'list_windows', 'get_window_state', 'get_window_state', 'get_window_state', 'set_value',
  ]);
});

test('clearDraft requires an exact optimistic-concurrency value', async () => {
  const { ui, calls } = fakeEnvironment();
  await assert.rejects(ui.clearDraft({ chatName: CHAT }), { code: 'LINE_INVALID_ARGUMENT' });
  assert.deepEqual(calls, []);
});

test('message actions refuse duplicate exact message text before opening a context menu', async () => {
  const text = 'same visible message';
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header()], 'inspect'),
      state([header(), message(text, 3), message(text, 4)], 'message-before'),
    ],
  });

  await assert.rejects(
    ui.messageAction({ chatName: CHAT, messageText: text, action: 'copy' }),
    { code: 'LINE_MESSAGE_NOT_UNIQUE' },
  );
  assert.equal(calls.some(call => call.name === 'right_click'), false);
});

test('copy verifies the clipboard after a fresh exact context-menu action', async () => {
  const text = 'copy this exact message';
  const menu = element(8, { label: 'Copy', role: 'MenuItem' });
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header()], 'inspect'),
      state([header(), message(text)], 'right-before'),
      state([header(), message(text), menu], 'right-after'),
      state([header(), message(text), menu], 'guard-before-copy'),
      state([header(), message(text), menu], 'copy-before'),
      state([header(), message(text)], 'copy-after'),
    ],
    handlers: {
      clipboard_read: () => ({ text }),
    },
  });

  const result = await ui.messageAction({ chatName: CHAT, messageText: text, action: 'copy' });
  assert.equal(result.clipboardVerified, true);
  assert.deepEqual(calls.map(call => call.name), [
    'list_windows', 'get_window_state', 'get_window_state', 'right_click', 'get_window_state',
    'get_window_state', 'get_window_state', 'click', 'get_window_state', 'clipboard_read',
  ]);
  for (const call of actionCalls(calls).filter(call => ['right_click', 'click'].includes(call.name))) {
    assert.equal(call.args.delivery_mode, 'background');
    assert.ok(call.args.element_token);
  }
});

test('a caller-confirmed visual source binds one custom-drawn reply point and preserves the local sourceRef limitation', async () => {
  const text = '完整引用來源';
  const replyText = '只暫存，不送出';
  const source = {
    sourceRef: 'message:0123456789abcdef01234567',
    text,
    sender: '測試員',
    date: '2026-09-11',
    time: '10:22:33',
  };
  const sourceRect = { x: 60, y: 130, width: 140, height: 72 };
  const sourcePoint = { x: 120, y: 164 };
  const visual = (suffix, options = {}) => replySourceVisualState({ text, hash: 'a', suffix, ...options });
  const { ui, calls } = fakeEnvironment({
    states: [
      visual('source-inspect'),
      visual('source-capture'),
      visual('source-confirm-window'),
      visual('source-confirm-fresh'),
      visual('reply-inspect'),
      visual('reply-before'),
      visual('reply-after-menu', { menu: 'Reply' }),
      visual('reply-guard', { menu: 'Reply' }),
      visual('reply-menu-before', { menu: 'Reply' }),
      visual('reply-fresh-main', { menu: 'Reply' }),
      visual('reply-after', { includeQuote: true }),
      visual('draft-before', { includeQuote: true }),
      visual('draft-after', { includeQuote: true, draft: replyText }),
    ],
    ...visualHelpers(),
  });

  const pending = await ui.getReplySourceTarget({ chatRef: CHAT_REF, chatName: CHAT, chatType: 'direct', source });
  assert.equal(pending.localSourceRef, source.sourceRef);
  assert.equal(pending.uiSourceRefVerified, false);
  assert.equal(pending.images.length, 1);
  assert.deepEqual(pending.replySourceTarget.imageBounds, { x: 0, y: 0, width: 300, height: 448 });
  assert.deepEqual(pending.replySourceTarget.messageBounds, pending.replySourceTarget.imageBounds);
  assert.equal(pending.images[0].width, 300);
  assert.equal(pending.images[0].height, 448);
  assert.notEqual(pending.images[0].data, 'source-a');
  const confirmed = await ui.confirmReplySourceTarget({
    chatName: CHAT,
    token: pending.replySourceTarget.token,
    observedSource: source,
    sourceRect,
    sourcePoint,
  });
  assert.equal(confirmed.sourceIdentityVerification, 'caller-confirmed-fresh-visual-source-region');
  assert.equal(confirmed.uiSourceRefVerified, false);

  const result = await ui.messageAction({
    chatName: CHAT,
    messageText: text,
    action: 'reply',
    replyText,
    source,
    sourceToken: confirmed.replySourceTarget.token,
  });
  const rightClick = calls.find(call => call.name === 'right_click');
  assert.equal(result.quoteVerified, true);
  assert.equal(result.draftStaged, true);
  assert.equal(result.localSourceRef, source.sourceRef);
  assert.equal(result.uiSourceRefVerified, false);
  assert.equal(result.sourceIdentityVerification, 'caller-confirmed-fresh-visual-source-region');
  assert.deepEqual(confirmed.replySourceTarget.sourceRect, sourceRect);
  assert.deepEqual(confirmed.replySourceTarget.sourcePoint, sourcePoint);
  assert.equal(rightClick.args.x, sourcePoint.x + 100);
  assert.equal(rightClick.args.y, sourcePoint.y + 50);
  assert.equal('element_token' in rightClick.args, false);
});

test('reply source refuses a crop helper that returns full-window pixels or a different region', async () => {
  const source = {
    sourceRef: 'message:0123456789abcdef01234567', text: '完整引用來源',
    sender: '測試員', date: '2026-09-11', time: '10:22:33',
  };
  for (const defect of ['full-window', 'wrong-region']) {
    const helpers = visualHelpers();
    const { ui, calls } = fakeEnvironment({
      states: ['inspect', 'capture'].map(suffix => replySourceVisualState({ text: source.text, suffix })),
      ...helpers,
      fingerprintRegion: async (image, region, options) => {
        const result = await helpers.fingerprintRegion(image, region, options);
        return defect === 'full-window'
          ? { ...result, image }
          : { ...result, region: { ...region, x: region.x + 1 } };
      },
    });
    await assert.rejects(ui.getReplySourceTarget({ chatRef: CHAT_REF, chatName: CHAT, chatType: 'direct', source }),
      { code: 'LINE_REPLY_SOURCE_UNVERIFIED' });
    assert.equal(calls.some(call => ['right_click', 'set_value'].includes(call.name)), false);
  }
});

test('reply source refuses an unproven or opposite direct/group chat before name-only navigation', async () => {
  const source = {
    sourceRef: 'message:0123456789abcdef01234567',
    text: '完整引用來源',
    sender: '測試員',
    date: '2026-09-11',
    time: '10:22:33',
  };
  for (const [expectedChatType, observedChatType] of [['direct', 'group'], ['group', 'direct']]) {
    const { ui, calls, automationCalls } = fakeEnvironment({
      states: [replySourceVisualState({ text: source.text, chatType: observedChatType, suffix: `${expectedChatType}-wrong-kind` })],
    });
    await assert.rejects(
      ui.getReplySourceTarget({ chatRef: CHAT_REF, chatName: CHAT, chatType: expectedChatType, source }),
      { code: 'LINE_REPLY_SOURCE_CHAT_TYPE_MISMATCH' },
    );
    assert.deepEqual(automationCalls, []);
    assert.equal(calls.some(call => ['right_click', 'click', 'set_value'].includes(call.name)), false);
  }

  const detached = { ...TARGET, title: CHAT, window_id: 100 };
  const { ui, calls, automationCalls } = fakeEnvironment({
    windows: [detached],
    states: [state([], 'detached-kind-unknown'), state([], 'detached-kind-unknown-after')],
  });
  await assert.rejects(
    ui.getReplySourceTarget({ chatRef: CHAT_REF, chatName: CHAT, chatType: 'direct', source }),
    { code: 'LINE_REPLY_SOURCE_UNVERIFIED' },
  );
  assert.deepEqual(automationCalls, []);
  assert.equal(calls.some(call => ['right_click', 'click', 'set_value'].includes(call.name)), false);
});

test('a type-bound reply source token rejects same-name direct/group flips before right-click', async () => {
  const source = {
    sourceRef: 'message:0123456789abcdef01234567',
    text: '完整引用來源',
    sender: '測試員',
    date: '2026-09-11',
    time: '10:22:33',
  };
  const sourceRect = { x: 60, y: 130, width: 140, height: 72 };
  const sourcePoint = { x: 120, y: 164 };
  for (const [chatType, changedChatType] of [['direct', 'group'], ['group', 'direct']]) {
    const view = (suffix, type) => replySourceVisualState({ text: source.text, chatType: type, hash: 'a', suffix });
    const { ui, calls } = fakeEnvironment({
      states: [
        view(`${chatType}-source-inspect`, chatType),
        view(`${chatType}-source-capture`, chatType),
        view(`${chatType}-confirm-window`, chatType),
        view(`${chatType}-confirm-fresh`, chatType),
        view(`${chatType}-changed-before-stage`, changedChatType),
      ],
      ...visualHelpers(),
    });
    const pending = await ui.getReplySourceTarget({ chatRef: CHAT_REF, chatName: CHAT, chatType, source });
    await ui.confirmReplySourceTarget({
      chatName: CHAT,
      token: pending.replySourceTarget.token,
      observedSource: source,
      sourceRect,
      sourcePoint,
    });
    await assert.rejects(
      ui.messageAction({
        chatName: CHAT,
        messageText: source.text,
        action: 'reply',
        replyText: '不可寫入另一種聊天室',
        source,
        sourceToken: pending.replySourceTarget.token,
      }),
      { code: 'LINE_REPLY_SOURCE_CHAT_TYPE_MISMATCH' },
    );
    assert.equal(calls.some(call => ['right_click', 'click', 'set_value'].includes(call.name)), false);
  }
});

test('a type-bound reply source token refuses an unproven current header before legacy selection', async () => {
  const source = {
    sourceRef: 'message:0123456789abcdef01234567',
    text: '完整引用來源',
    sender: '測試員',
    date: '2026-09-11',
    time: '10:22:33',
  };
  const view = (suffix, options = {}) => replySourceVisualState({ text: source.text, hash: 'a', suffix, ...options });
  const { ui, calls, automationCalls } = fakeEnvironment({
    states: [
      view('unknown-source-inspect'),
      view('unknown-source-capture'),
      view('unknown-confirm-window'),
      view('unknown-confirm-fresh'),
      view('unknown-before-stage', { chatLabel: '目前聊天室未驗證' }),
      view('unknown-before-stage-ocr', { chatLabel: '目前聊天室未驗證' }),
    ],
    ...visualHelpers(),
  });
  const pending = await ui.getReplySourceTarget({ chatRef: CHAT_REF, chatName: CHAT, chatType: 'direct', source });
  await ui.confirmReplySourceTarget({
    chatName: CHAT,
    token: pending.replySourceTarget.token,
    observedSource: source,
    sourceRect: { x: 60, y: 130, width: 140, height: 72 },
    sourcePoint: { x: 120, y: 164 },
  });
  await assert.rejects(
    ui.messageAction({
      chatName: CHAT,
      messageText: source.text,
      action: 'reply',
      replyText: '不可導覽到未驗證聊天室',
      source,
      sourceToken: pending.replySourceTarget.token,
    }),
    { code: 'LINE_REPLY_SOURCE_CHAT_TYPE_UNVERIFIED' },
  );
  assert.deepEqual(automationCalls, []);
  assert.equal(calls.some(call => ['right_click', 'click', 'set_value'].includes(call.name)), false);
});

test('a caller-confirmed visual header treats a member-count suffix as group proof for reply source', async () => {
  const source = {
    sourceRef: 'message:0123456789abcdef01234567',
    text: '完整引用來源',
    sender: '測試員',
    date: '2026-09-11',
    time: '10:22:33',
  };
  const { ui, calls, automationCalls } = fakeEnvironment({
    windows: [CURRENT_MAIN_TARGET],
    states: Array.from({ length: 6 }, (_, index) => currentMainVisualState({ suffix: `visual-group-${index}` })),
    ...visualHelpers(),
    randomToken: () => 'visual-group-token',
    readChatIdentity: async () => localGuiIdentity(CHAT, 'group'),
    now: () => 1_000_000,
  });
  const pending = await ui.getState({ chatName: CHAT, includeScreenshot: true });
  await ui.confirmChat({
    chatName: CHAT,
    token: pending.visualVerification.token,
    observedHeader: `${CHAT} (7)`,
  });
  await assert.rejects(
    ui.getReplySourceTarget({ chatRef: CHAT_REF, chatName: CHAT, chatType: 'direct', source }),
    { code: 'LINE_REPLY_SOURCE_CHAT_TYPE_MISMATCH' },
  );
  assert.deepEqual(automationCalls, []);
  assert.equal(calls.some(call => ['right_click', 'click', 'set_value'].includes(call.name)), false);
});

test('a visually bound reply never fakes quote verification when the post-action UI lacks an accessible quote marker', async () => {
  const text = '完整引用來源';
  const source = {
    sourceRef: 'message:0123456789abcdef01234567',
    text,
    sender: '測試員',
    date: '2026-09-11',
    time: '10:22:33',
  };
  const visual = (suffix, options = {}) => replySourceVisualState({ text, hash: 'a', suffix, ...options });
  const { ui, calls } = fakeEnvironment({
    states: [
      visual('quote-pending-inspect'),
      visual('quote-pending-capture'),
      visual('quote-pending-confirm-window'),
      visual('quote-pending-confirm-fresh'),
      visual('quote-pending-reply-inspect'),
      visual('quote-pending-reply-before'),
      visual('quote-pending-after-menu', { menu: 'Reply' }),
      visual('quote-pending-guard', { menu: 'Reply' }),
      visual('quote-pending-menu-before', { menu: 'Reply' }),
      visual('quote-pending-fresh-main', { menu: 'Reply' }),
      visual('quote-pending-after'),
    ],
    ...visualHelpers(),
  });
  const pending = await ui.getReplySourceTarget({ chatRef: CHAT_REF, chatName: CHAT, chatType: 'direct', source });
  await ui.confirmReplySourceTarget({
    chatName: CHAT,
    token: pending.replySourceTarget.token,
    observedSource: source,
    sourceRect: { x: 60, y: 130, width: 140, height: 72 },
    sourcePoint: { x: 120, y: 164 },
  });
  const result = await ui.messageAction({
    chatName: CHAT,
    messageText: text,
    action: 'reply',
    replyText: '不得自行補寫草稿',
    source,
    sourceToken: pending.replySourceTarget.token,
  });
  assert.equal(result.quoteVerified, false);
  assert.equal(result.draftStaged, false);
  assert.equal(result.requiresVisualQuoteConfirmation, true);
  assert.equal(result.operationMayHaveCompleted, true);
  assert.equal(result.images.length, 1);
  assert.equal(result.images[0].width, 300);
  assert.equal(result.images[0].height, 550);
  assert.notEqual(result.images[0].data, 'source-a');
  assert.equal(calls.some(call => call.name === 'set_value'), false);
});

test('post-Reply visual fallback refuses changed chat identity and uncropped helper output', async () => {
  const source = {
    sourceRef: 'message:0123456789abcdef01234567', text: '完整引用來源',
    sender: '測試員', date: '2026-09-11', time: '10:22:33',
  };
  for (const defect of ['wrong-chat', 'full-window']) {
    const helpers = visualHelpers();
    const view = (suffix, options = {}) => replySourceVisualState({ text: source.text, suffix, ...options });
    const { ui, calls } = fakeEnvironment({
      states: [
        view('inspect'), view('capture'), view('confirm-window'), view('confirm-fresh'),
        view('reply-inspect'), view('reply-before'), view('after-menu', { menu: 'Reply' }),
        view('guard', { menu: 'Reply' }), view('menu-before', { menu: 'Reply' }),
        view('fresh-main', { menu: 'Reply' }),
        view('after', defect === 'wrong-chat' ? { chatLabel: '另一個聊天室' } : {}),
      ],
      ...helpers,
      fingerprintRegion: async (image, region, options) => {
        const result = await helpers.fingerprintRegion(image, region, options);
        return defect === 'full-window' && options?.includeImage && region.height === 550
          ? { ...result, image } : result;
      },
    });
    const pending = await ui.getReplySourceTarget({ chatRef: CHAT_REF, chatName: CHAT, chatType: 'direct', source });
    await ui.confirmReplySourceTarget({
      chatName: CHAT, token: pending.replySourceTarget.token, observedSource: source,
      sourceRect: { x: 60, y: 130, width: 140, height: 72 }, sourcePoint: { x: 120, y: 164 },
    });
    await assert.rejects(ui.messageAction({
      chatName: CHAT, messageText: source.text, action: 'reply', replyText: '不可寫入',
      source, sourceToken: pending.replySourceTarget.token,
    }), error => {
      assert.equal(error.code, defect === 'wrong-chat' ? 'LINE_CHAT_STALE' : 'LINE_REPLY_UNVERIFIED');
      assert.equal(error.operationMayHaveCompleted, true);
      assert.equal(error.images, undefined);
      return true;
    });
    assert.equal(calls.some(call => call.name === 'set_value'), false);
  }
});

test('reply-source confirmation rejects sender and timestamp drift before it can select a LINE bubble', async () => {
  const text = '完整引用來源';
  const source = {
    sourceRef: 'message:0123456789abcdef01234567',
    text,
    sender: '測試員',
    date: '2026-09-11',
    time: '10:22:33',
  };
  const { ui, calls } = fakeEnvironment({
    states: [
      replySourceVisualState({ text, hash: 'a', suffix: 'drift-inspect' }),
      replySourceVisualState({ text, hash: 'a', suffix: 'drift-capture' }),
    ],
    ...visualHelpers(),
  });
  const pending = await ui.getReplySourceTarget({ chatRef: CHAT_REF, chatName: CHAT, chatType: 'direct', source });
  for (const observedSource of [
    { ...source, sender: '另一位' },
    { ...source, time: '10:22:34' },
  ]) {
    await assert.rejects(
      ui.confirmReplySourceTarget({
        chatName: CHAT,
        token: pending.replySourceTarget.token,
        observedSource,
        sourceRect: { x: 60, y: 130, width: 140, height: 72 },
        sourcePoint: { x: 120, y: 164 },
      }),
      { code: 'LINE_REPLY_SOURCE_CONFIRMATION_INVALID' },
    );
  }
  assert.equal(calls.some(call => call.name === 'right_click'), false);
});

test('reply-source confirmation refuses visually ambiguous duplicate accessible bubbles', async () => {
  const text = 'same visible message';
  const source = {
    sourceRef: 'message:0123456789abcdef01234567',
    text,
    sender: '測試員',
    date: '2026-09-11',
    time: '10:22:33',
  };
  const duplicate = suffix => replySourceVisualState({
    text,
    hash: 'a',
    suffix,
    includeMessage: true,
    duplicateMessage: true,
  });
  const { ui, calls } = fakeEnvironment({
    states: [
      duplicate('duplicate-inspect'),
      duplicate('duplicate-capture'),
      duplicate('duplicate-confirm-window'),
      duplicate('duplicate-confirm-fresh'),
    ],
    ...visualHelpers(),
  });
  const pending = await ui.getReplySourceTarget({ chatRef: CHAT_REF, chatName: CHAT, chatType: 'direct', source });
  await assert.rejects(
    ui.confirmReplySourceTarget({
      chatName: CHAT,
      token: pending.replySourceTarget.token,
      observedSource: source,
      sourceRect: { x: 60, y: 130, width: 140, height: 72 },
      sourcePoint: { x: 120, y: 164 },
    }),
    { code: 'LINE_REPLY_SOURCE_AMBIGUOUS' },
  );
  assert.equal(calls.some(call => call.name === 'right_click'), false);
});

test('a changed source crop invalidates a reply-source token before right-click and the token is single-use', async () => {
  const text = '完整引用來源';
  const source = {
    sourceRef: 'message:0123456789abcdef01234567',
    text,
    sender: '測試員',
    date: '2026-09-11',
    time: '10:22:33',
  };
  const stable = suffix => replySourceVisualState({ text, hash: 'a', suffix });
  const { ui, calls } = fakeEnvironment({
    states: [
      stable('stale-inspect'),
      stable('stale-capture'),
      stable('stale-confirm-window'),
      stable('stale-confirm-fresh'),
      stable('stale-reply-inspect'),
      replySourceVisualState({ text, hash: 'b', suffix: 'stale-reply-before' }),
      stable('single-use-reply-inspect'),
    ],
    ...visualHelpers(),
  });
  const pending = await ui.getReplySourceTarget({ chatRef: CHAT_REF, chatName: CHAT, chatType: 'direct', source });
  await ui.confirmReplySourceTarget({
    chatName: CHAT,
    token: pending.replySourceTarget.token,
    observedSource: source,
    sourceRect: { x: 60, y: 130, width: 140, height: 72 },
    sourcePoint: { x: 120, y: 164 },
  });
  const args = {
    chatName: CHAT,
    messageText: text,
    action: 'reply',
    replyText: '草稿',
    source,
    sourceToken: pending.replySourceTarget.token,
  };
  await assert.rejects(ui.messageAction(args), { code: 'LINE_REPLY_SOURCE_STALE' });
  await assert.rejects(ui.messageAction(args), { code: 'LINE_REPLY_SOURCE_CONFIRMATION_INVALID' });
  assert.equal(calls.some(call => call.name === 'right_click'), false);
});

test('an expired reply-source token is rejected before a fresh screenshot can be treated as confirmation', async () => {
  const text = '完整引用來源';
  const source = {
    sourceRef: 'message:0123456789abcdef01234567',
    text,
    sender: '測試員',
    date: '2026-09-11',
    time: '10:22:33',
  };
  let clock = 1_000;
  const { ui, calls } = fakeEnvironment({
    states: [
      replySourceVisualState({ text, hash: 'a', suffix: 'expiry-inspect' }),
      replySourceVisualState({ text, hash: 'a', suffix: 'expiry-capture' }),
    ],
    ...visualHelpers(),
    now: () => clock,
  });
  const pending = await ui.getReplySourceTarget({ chatRef: CHAT_REF, chatName: CHAT, chatType: 'direct', source });
  clock += 120_001;
  await assert.rejects(
    ui.confirmReplySourceTarget({
      chatName: CHAT,
      token: pending.replySourceTarget.token,
      observedSource: source,
      sourceRect: { x: 60, y: 130, width: 140, height: 72 },
      sourcePoint: { x: 120, y: 164 },
    }),
    { code: 'LINE_REPLY_SOURCE_CONFIRMATION_INVALID' },
  );
  assert.equal(calls.some(call => call.name === 'right_click'), false);
});

test('reply stages an exact quote and draft once without sending', async () => {
  const text = 'reply source';
  const replyText = 'draft only';
  const menu = element(8, { label: 'Reply', role: 'MenuItem' });
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header()], 'inspect'),
      state([header(), message(text), composer('')], 'right-before'),
      state([header(), message(text), composer(''), menu], 'right-after'),
      state([header(), message(text), composer(''), menu], 'guard-before-reply'),
      state([header(), message(text), composer(''), menu], 'reply-menu-before'),
      state([header(), message(text), composer(''), menu], 'fresh-main-before-reply-click'),
      state([header(), message(text), quote(text), composer('')], 'reply-after'),
      state([header(), message(text), quote(text), composer('')], 'draft-before'),
      state([header(), message(text), quote(text), composer(replyText)], 'draft-after'),
    ],
  });

  const result = await ui.messageAction({ chatName: CHAT, messageText: text, action: 'reply', replyText });
  assert.equal(result.staged, true);
  assert.equal(result.sent, false);
  assert.equal(result.quoteVerified, true);
  assert.equal(result.draftStaged, true);
  assert.deepEqual(actionCalls(calls).map(call => call.name), ['right_click', 'click', 'set_value']);
  assert.equal(calls.some(call => call.name === 'press_key'), false);
});

test('reply refuses an initial nonempty composer before opening its context menu', async () => {
  const text = 'reply source';
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header()], 'inspect'),
      state([header(), message(text), composer('existing draft')], 'right-before'),
    ],
  });

  await assert.rejects(
    ui.messageAction({ chatName: CHAT, messageText: text, action: 'reply', replyText: 'draft only' }),
    error => error?.code === 'LINE_DRAFT_CONFLICT' && error?.operationMayHaveCompleted === false,
  );
  assert.deepEqual(actionCalls(calls), []);
});

test('reply preserves a detectable existing quoted context before opening its context menu', async () => {
  const text = 'reply source';
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header()], 'inspect'),
      state([header(), message(text), quote('an earlier source'), composer('')], 'right-before'),
    ],
  });

  await assert.rejects(
    ui.messageAction({ chatName: CHAT, messageText: text, action: 'reply' }),
    error => error?.code === 'LINE_REPLY_CONTEXT_CONFLICT' && error?.operationMayHaveCompleted === false,
  );
  assert.deepEqual(actionCalls(calls), []);
});

test('reply refuses a draft that appears after its context menu opens without clicking Reply', async () => {
  const text = 'reply source';
  const menu = element(8, { label: 'Reply', role: 'MenuItem' });
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header()], 'inspect'),
      state([header(), message(text), composer('')], 'right-before'),
      state([header(), message(text), composer(''), menu], 'right-after'),
      state([header(), message(text), composer(''), menu], 'guard-before-reply'),
      state([header(), message(text), composer(''), menu], 'reply-menu-before'),
      state([header(), message(text), composer('racing draft'), menu], 'fresh-main-before-reply-click'),
    ],
  });

  await assert.rejects(
    ui.messageAction({ chatName: CHAT, messageText: text, action: 'reply', replyText: 'draft only' }),
    error => error?.code === 'LINE_DRAFT_CONFLICT'
      && error?.operationMayHaveCompleted === true
      && error?.details?.contextMenuMayBeOpen === true
      && error?.details?.actionSelected === false
      && error?.details?.replyContextMayBeOpen === false
      && error?.details?.draftMayBeStaged === false
      && !error.message.includes('left unchanged'),
  );
  assert.deepEqual(actionCalls(calls).map(call => call.name), ['right_click']);
});

test('reply reobserves the main chat after selecting a detached Qt context menu item', async () => {
  const text = 'reply source';
  const menu = element(8, { label: 'Reply', role: 'MenuItem' });
  const popup = { ...TARGET, title: 'LINE', window_id: 100 };
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header()], 'inspect'),
      state([header(), message(text), composer('')], 'right-before'),
      state([header(), message(text), composer('')], 'right-after'),
      state([menu], 'popup-menu'),
      state([header(), message(text), composer('')], 'guard-before-reply'),
      state([menu], 'popup-before-click'),
      state([header(), message(text), composer('')], 'fresh-main-before-reply-click'),
      state([], 'popup-after-click'),
      state([header(), message(text), quote(text), composer('')], 'main-after-reply'),
    ],
    handlers: {
      list_windows: (_args, calls) => ({
        windows: calls.filter(call => call.name === 'list_windows').length < 3
          ? [TARGET]
          : [TARGET, popup],
      }),
    },
  });

  const result = await ui.messageAction({ chatName: CHAT, messageText: text, action: 'reply' });
  assert.equal(result.quoteVerified, true);
  assert.equal(result.sent, false);
  assert.deepEqual(actionCalls(calls).map(call => call.name), ['right_click', 'click']);
});

test('reply reports uncertainty without staging when its selected quote is missing', async () => {
  const text = 'reply source';
  const menu = element(8, { label: 'Reply', role: 'MenuItem' });
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header()], 'inspect'),
      state([header(), message(text), composer('')], 'right-before'),
      state([header(), message(text), composer(''), menu], 'right-after'),
      state([header(), message(text), composer(''), menu], 'guard-before-reply'),
      state([header(), message(text), composer(''), menu], 'reply-menu-before'),
      state([header(), message(text), composer(''), menu], 'fresh-main-before-reply-click'),
      state([header(), message(text), composer('')], 'reply-after-no-quote'),
    ],
  });

  await assert.rejects(
    ui.messageAction({ chatName: CHAT, messageText: text, action: 'reply', replyText: 'draft only' }),
    error => error?.code === 'LINE_REPLY_UNVERIFIED'
      && error?.operationMayHaveCompleted === true
      && error?.details?.contextMenuMayBeOpen === true
      && error?.details?.actionSelected === true
      && error?.details?.replyContextMayBeOpen === true
      && error?.details?.draftMayBeStaged === false,
  );
  assert.deepEqual(actionCalls(calls).map(call => call.name), ['right_click', 'click']);
});

test('reply reports uncertainty without staging when its selected quote is ambiguous', async () => {
  const text = 'reply source';
  const menu = element(8, { label: 'Reply', role: 'MenuItem' });
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header()], 'inspect'),
      state([header(), message(text), composer('')], 'right-before'),
      state([header(), message(text), composer(''), menu], 'right-after'),
      state([header(), message(text), composer(''), menu], 'guard-before-reply'),
      state([header(), message(text), composer(''), menu], 'reply-menu-before'),
      state([header(), message(text), composer(''), menu], 'fresh-main-before-reply-click'),
      state([header(), message(text), quote(text, 4), quote(text, 5), composer('')], 'reply-after-ambiguous-quote'),
    ],
  });

  await assert.rejects(
    ui.messageAction({ chatName: CHAT, messageText: text, action: 'reply', replyText: 'draft only' }),
    error => error?.code === 'LINE_REPLY_UNVERIFIED'
      && error?.operationMayHaveCompleted === true
      && error?.details?.actionSelected === true
      && error?.details?.replyContextMayBeOpen === true
      && error?.details?.draftMayBeStaged === false,
  );
  assert.deepEqual(actionCalls(calls).map(call => call.name), ['right_click', 'click']);
});

test('reply reports uncertainty and does not retry when a staged quote changes', async () => {
  const text = 'reply source';
  const replyText = 'draft only';
  const menu = element(8, { label: 'Reply', role: 'MenuItem' });
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header()], 'inspect'),
      state([header(), message(text), composer('')], 'right-before'),
      state([header(), message(text), composer(''), menu], 'right-after'),
      state([header(), message(text), composer(''), menu], 'guard-before-reply'),
      state([header(), message(text), composer(''), menu], 'reply-menu-before'),
      state([header(), message(text), composer(''), menu], 'fresh-main-before-reply-click'),
      state([header(), message(text), quote(text), composer('')], 'reply-after'),
      state([header(), message(text), quote(text), composer('')], 'draft-before'),
      state([header(), message(text), quote('another source'), composer(replyText)], 'draft-after-quote-changed'),
    ],
  });

  await assert.rejects(
    ui.messageAction({ chatName: CHAT, messageText: text, action: 'reply', replyText }),
    error => error?.code === 'LINE_REPLY_UNVERIFIED'
      && error?.operationMayHaveCompleted === true
      && error?.details?.actionSelected === true
      && error?.details?.replyContextMayBeOpen === true
      && error?.details?.draftMayBeStaged === true,
  );
  assert.deepEqual(actionCalls(calls).map(call => call.name), ['right_click', 'click', 'set_value']);
});

test('reply reports uncertainty and does not retry when its staged text differs', async () => {
  const text = 'reply source';
  const replyText = 'draft only';
  const menu = element(8, { label: 'Reply', role: 'MenuItem' });
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header()], 'inspect'),
      state([header(), message(text), composer('')], 'right-before'),
      state([header(), message(text), composer(''), menu], 'right-after'),
      state([header(), message(text), composer(''), menu], 'guard-before-reply'),
      state([header(), message(text), composer(''), menu], 'reply-menu-before'),
      state([header(), message(text), composer(''), menu], 'fresh-main-before-reply-click'),
      state([header(), message(text), quote(text), composer('')], 'reply-after'),
      state([header(), message(text), quote(text), composer('')], 'draft-before'),
      state([header(), message(text), quote(text), composer('different text')], 'draft-after-text-changed'),
    ],
  });

  await assert.rejects(
    ui.messageAction({ chatName: CHAT, messageText: text, action: 'reply', replyText }),
    error => error?.code === 'LINE_DRAFT_WRITE_UNVERIFIED'
      && error?.operationMayHaveCompleted === true
      && error?.details?.actionSelected === true
      && error?.details?.replyContextMayBeOpen === true
      && error?.details?.draftMayBeStaged === true,
  );
  assert.deepEqual(actionCalls(calls).map(call => call.name), ['right_click', 'click', 'set_value']);
});

test('forward stops at a verified recipient dialog and never sends', async () => {
  const text = 'forward source';
  const menu = element(8, { label: 'Forward', role: 'MenuItem' });
  const dialog = element(9, { label: 'Forward', role: 'Dialog', semantic_role: 'forward-dialog' });
  const recipients = element(10, { role: 'List', semantic_role: 'recipient-selector' });
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header()], 'inspect'),
      state([header(), message(text)], 'right-before'),
      state([header(), message(text), menu], 'right-after'),
      state([header(), message(text), menu], 'guard-before-forward'),
      state([header(), message(text), menu], 'forward-before'),
      state([header(), message(text), dialog, recipients], 'forward-after'),
    ],
  });

  const result = await ui.messageAction({ chatName: CHAT, messageText: text, action: 'forward' });
  assert.equal(result.staged, true);
  assert.equal(result.sent, false);
  assert.equal(calls.some(call => ['type_text', 'set_value', 'press_key', 'hotkey'].includes(call.name)), false);
});

test('feature opening verifies a newly added scoped main-chat search bar and ignores a global Edit', async () => {
  const root = element(7, { role: 'Window' });
  const { ui, calls } = fakeEnvironment({
    states: [
      screenshotState([...mainHeaderToolbar(), root, outsideMainSearchEdit()], 'inspect'),
      screenshotState([...mainHeaderToolbar(), root, outsideMainSearchEdit()], 'search-preflight'),
      screenshotState([...mainHeaderToolbar(), root, outsideMainSearchEdit()], 'search-before'),
      screenshotState([...mainHeaderToolbar(), root, outsideMainSearchEdit(), ...mainSearchBar()], 'search-after'),
      screenshotState([...mainHeaderToolbar(), root, outsideMainSearchEdit(), ...mainSearchBar()], 'outcome-main'),
    ],
    ...visualHelpers(),
  });

  const result = await ui.openFeature({ chatName: CHAT, feature: 'search' });
  const click = calls.find(call => call.name === 'click');
  assert.equal(result.opened, true);
  assert.equal(result.verification, 'structural-main-chat-search-bar');
  assert.equal(click.args.x, 292);
  assert.equal(click.args.y, 22);
  assert.equal('element_token' in click.args, false);
  assert.equal(click.args.delivery_mode, 'background');
  assert.equal(calls.some(call => call.name === 'hotkey'), false);
});

test('an already-open scoped main-chat search bar is idempotent and is never clicked closed', async () => {
  const root = element(7, { role: 'Window' });
  const { ui, calls } = fakeEnvironment({
    states: [
      screenshotState([...mainHeaderToolbar(), root, ...mainSearchBar()], 'search-open-inspect'),
      screenshotState([...mainHeaderToolbar(), root, ...mainSearchBar()], 'search-open-preflight'),
    ],
    ...visualHelpers(),
  });

  const result = await ui.openFeature({ chatName: CHAT, feature: 'search' });
  assert.equal(result.opened, false);
  assert.equal(result.alreadyOpen, true);
  assert.equal(result.verification, 'structural-main-chat-search-bar');
  assert.equal(calls.some(call => call.name === 'click'), false);
});

test('a Search bar that appears after preflight short-circuits the authoritative input snapshot', async () => {
  const root = element(7, { role: 'Window' });
  const { ui, calls } = fakeEnvironment({
    states: [
      screenshotState([...mainHeaderToolbar(), root], 'search-race-inspect'),
      screenshotState([...mainHeaderToolbar(), root], 'search-race-preflight'),
      screenshotState([...mainHeaderToolbar(), root, ...mainSearchBar()], 'search-race-input-before'),
    ],
    ...visualHelpers(),
  });

  const result = await ui.openFeature({ chatName: CHAT, feature: 'search' });
  assert.equal(result.opened, false);
  assert.equal(result.alreadyOpen, true);
  assert.equal(result.verification, 'structural-main-chat-search-bar');
  assert.equal(calls.some(call => call.name === 'click'), false);
});

test('a structural More toolbar ignores the direct one-pixel separator and opens its verified feature path', async () => {
  const menu = element(20, { label: 'Albums', role: 'MenuItem' });
  const surface = element(21, { label: 'Albums', role: 'Dialog' });
  const toolbar = () => mainHeaderToolbar({ includeSeparator: true, includeUnrelatedMore: true });
  const { ui, calls, automationCalls } = fakeEnvironment({
    states: [
      screenshotState(toolbar(), 'albums-inspect'),
      screenshotState(toolbar(), 'albums-preflight'),
      screenshotState(toolbar(), 'albums-more-before'),
      screenshotState([...toolbar(), menu], 'albums-more-after'),
      screenshotState([...toolbar(), menu], 'albums-main-guard'),
      screenshotState([...toolbar(), menu], 'albums-item-before'),
      screenshotState([...toolbar(), surface], 'albums-item-after'),
      screenshotState([...toolbar(), surface], 'albums-outcome'),
    ],
    ...visualHelpers(),
  });

  const result = await ui.openFeature({ chatName: CHAT, feature: 'albums', deliveryMode: 'foreground' });
  const clicks = calls.filter(call => call.name === 'click');
  assert.equal(result.opened, true);
  assert.equal(result.verification, 'exact-feature-surface');
  assert.equal(clicks[0].args.x, 378);
  assert.equal(clicks[0].args.y, 22);
  assert.equal('element_token' in clicks[0].args, false);
  assert.equal(clicks[0].args.delivery_mode, 'foreground');
  assert.match(clicks[1].args.element_token, /^token-20/);
  assert.equal(clicks[1].args.delivery_mode, 'foreground');
  assert.deepEqual(automationCalls, ['activate']);
});

test('feature shortcuts accept explicit foreground delivery without changing normal input routes', async () => {
  const surface = element(40, { label: 'Notes', role: 'Dialog' });
  const { ui, calls, automationCalls } = fakeEnvironment({
    states: [
      state([header()], 'notes-inspect'),
      state([header()], 'notes-preflight'),
      state([header()], 'notes-before'),
      state([header(), surface], 'notes-after'),
      state([header(), surface], 'notes-outcome'),
    ],
  });

  const result = await ui.openFeature({ chatName: CHAT, feature: 'notes', deliveryMode: 'foreground' });
  const hotkey = calls.find(call => call.name === 'hotkey');
  assert.equal(result.opened, true);
  assert.deepEqual(hotkey.args.keys, ['ctrl', 'n']);
  assert.equal(hotkey.args.delivery_mode, 'foreground');
  assert.equal(calls.some(call => ['set_value', 'press_key'].includes(call.name)), false);
  assert.deepEqual(automationCalls, ['activate']);
});

test('foreground feature navigation refuses an unverified facade activation before CUA input', async () => {
  const { ui, calls, automationCalls } = fakeEnvironment({
    states: [state([header()], 'foreground-activation-inspect')],
    activateLine: async () => ({ success: false }),
  });
  await assert.rejects(
    ui.openFeature({ chatName: CHAT, feature: 'notes', deliveryMode: 'foreground' }),
    { code: 'LINE_FOCUS_UNAVAILABLE' },
  );
  assert.deepEqual(automationCalls, ['activate']);
  assert.equal(calls.some(call => ['click', 'hotkey'].includes(call.name)), false);
});

test('openFeature rejects an unsupported delivery mode before inspecting LINE', async () => {
  const { ui, calls } = fakeEnvironment();
  await assert.rejects(
    ui.openFeature({ chatName: CHAT, feature: 'search', deliveryMode: 'automatic' }),
    { code: 'LINE_INVALID_ARGUMENT' },
  );
  assert.deepEqual(calls, []);
});

test('openFeature refuses unavailable members navigation before inspecting LINE', async () => {
  const { ui, calls } = fakeEnvironment();
  await assert.rejects(
    ui.openFeature({ chatName: CHAT, feature: 'members' }),
    { code: 'LINE_INVALID_ARGUMENT' },
  );
  assert.deepEqual(calls, []);
});

test('anonymous main-header navigation refuses missing screenshots and invalid bounds before any click', async () => {
  const missingScreenshot = fakeEnvironment({
    states: [
      state(mainHeaderToolbar(), 'missing-screenshot-inspect'),
      state(mainHeaderToolbar(), 'missing-screenshot-preflight'),
      state(mainHeaderToolbar(), 'missing-screenshot-before'),
    ],
    ...visualHelpers(),
  });
  await assert.rejects(
    missingScreenshot.ui.openFeature({ chatName: CHAT, feature: 'search' }),
    { code: 'LINE_UI_SELECTOR_UNAVAILABLE' },
  );
  assert.equal(missingScreenshot.calls.some(call => call.name === 'click'), false);

  const invalidBounds = fakeEnvironment({
    windows: [{ ...TARGET, bounds: { x: 0, y: 0, width: 0, height: 600 } }],
    states: [
      screenshotState(mainHeaderToolbar(), 'invalid-bounds-inspect'),
      screenshotState(mainHeaderToolbar(), 'invalid-bounds-preflight'),
      screenshotState(mainHeaderToolbar(), 'invalid-bounds-before'),
    ],
    ...visualHelpers(),
  });
  await assert.rejects(
    invalidBounds.ui.openFeature({ chatName: CHAT, feature: 'search' }),
    { code: 'LINE_UI_SELECTOR_UNAVAILABLE' },
  );
  assert.equal(invalidBounds.calls.some(call => call.name === 'click'), false);
});

test('a failed menu resolution after More reports that navigation may have completed', async () => {
  const toolbar = () => mainHeaderToolbar({ includeSeparator: true });
  const { ui, calls } = fakeEnvironment({
    states: [
      screenshotState(toolbar(), 'more-failure-inspect'),
      screenshotState(toolbar(), 'more-failure-preflight'),
      screenshotState(toolbar(), 'more-failure-before'),
      screenshotState(toolbar(), 'more-failure-after'),
    ],
    ...visualHelpers(),
  });
  await assert.rejects(
    ui.openFeature({ chatName: CHAT, feature: 'albums' }),
    error => error?.code === 'LINE_FEATURE_UNAVAILABLE' && error?.operationMayHaveCompleted === true,
  );
  assert.equal(calls.filter(call => call.name === 'click').length, 1);
});

test('getState keeps image content out unless the caller requested it', async () => {
  const image = { type: 'image', data: 'base64-not-real' };
  const { ui } = fakeEnvironment({
    states: [{ ...state([header()], 'state'), images: [image], screenshot_data: 'raw' }],
  });

  const result = await ui.getState({ chatName: CHAT, includeScreenshot: false });
  assert.equal('images' in result, false);
  assert.equal('screenshot_data' in result.raw, false);
});

test('visual header crop clips one Qt UIA outer-edge pixel but refuses a wider overflow', async () => {
  const accepted = fakeEnvironment({
    windows: [CURRENT_MAIN_TARGET],
    states: Array.from({ length: 2 }, (_, index) => currentMainVisualState({ suffix: `edge-${index}` })),
    ...visualHelpers(),
  });
  const pending = await accepted.ui.getState({ chatName: CHAT, includeScreenshot: true });
  assert.equal(pending.verification, 'visual-header-confirmation-pending');
  assert.equal(pending.images[0].data, 'crop-a');

  const rejected = fakeEnvironment({
    windows: [CURRENT_MAIN_TARGET],
    states: Array.from({ length: 2 }, (_, index) => currentMainVisualState({
      suffix: `wide-edge-${index}`,
      headerWidth: 376,
    })),
    ...visualHelpers(),
  });
  await assert.rejects(
    rejected.ui.getState({ chatName: CHAT, includeScreenshot: true }),
    { code: 'LINE_CHAT_UNVERIFIED' },
  );
});

test('visual header confirmation returns only a title crop and carries a fresh cache into guarded reads', async () => {
  const { ui, automationCalls, locks } = fakeEnvironment({
    states: Array.from({ length: 7 }, (_, index) => nestedVisualState('', `visual-${index}`)),
    ...visualHelpers(),
    randomToken: () => 'pending-header-token',
    now: () => 1_000_000,
  });

  const pending = await ui.getState({ chatName: CHAT, includeScreenshot: true });
  assert.equal(pending.success, true);
  assert.equal(pending.verification, 'visual-header-confirmation-pending');
  assert.equal(pending.visualVerification.token, 'pending-header-token');
  assert.equal('raw' in pending, false);
  assert.equal(pending.images.length, 1);
  assert.equal(pending.images[0].data, 'crop-a');
  assert.deepEqual(automationCalls, []);

  const confirmed = await ui.confirmChat({
    chatName: CHAT,
    token: pending.visualVerification.token,
    observedHeader: CHAT,
  });
  assert.equal(confirmed.verification, 'caller-confirmed-fresh-header-crop');

  const draft = await ui.getDraft({ chatName: CHAT });
  assert.equal(draft.draft, '');
  assert.equal(draft.verification.chat, 'cached-caller-confirmed-main-header-crop');
  assert.deepEqual(locks, ['ui-get-state', 'ui-confirm-chat-view', 'ui-get-draft']);
});

test('header confirmation and its cache reject a replacement local chat identity', async () => {
  for (const replaceAt of ['confirm', 'use']) {
    let currentRef = CHAT_REF;
    const { ui, calls } = fakeEnvironment({
      states: Array.from({ length: 9 }, (_, index) => nestedVisualState('', `identity-${index}`)),
      ...visualHelpers(),
      readChatIdentity: async () => localGuiIdentity(CHAT, 'direct', currentRef),
    });
    const pending = await ui.getState({ chatName: CHAT, includeScreenshot: true });
    const confirm = () => ui.confirmChat({ chatName: CHAT, token: pending.visualVerification.token, observedHeader: CHAT });
    if (replaceAt === 'use') await confirm();
    currentRef = 'chat:ffffffffffffffffffffffff';
    const priorCalls = calls.length;
    await assert.rejects(replaceAt === 'confirm' ? confirm() : ui.getDraft({ chatName: CHAT }),
      { code: 'LINE_CHAT_CONFIRMATION_STALE' });
    if (replaceAt === 'confirm') assert.equal(calls.length, priorCalls);
    assert.deepEqual(actionCalls(calls), []);
  }
});

test('reply confirmation and use bind the fresh local chatRef as well as unchanged pixels', async () => {
  const source = { sourceRef: 'message:0123456789abcdef01234567', text: 'bound source', sender: 'Tester', date: '2026-09-11', time: '10:22:33' };
  for (const replaceAt of ['source-read', 'confirm', 'use']) {
    let currentRef = replaceAt === 'source-read' ? 'chat:ffffffffffffffffffffffff' : CHAT_REF;
    const { ui, calls } = fakeEnvironment({
      states: Array.from({ length: 8 }, (_, index) => replySourceVisualState({ text: source.text, hash: 'a', suffix: `ref-${index}` })),
      ...visualHelpers(),
      readChatIdentity: async () => localGuiIdentity(CHAT, 'direct', currentRef),
    });
    const capture = () => ui.getReplySourceTarget({ chatRef: CHAT_REF, chatName: CHAT, chatType: 'direct', source });
    if (replaceAt === 'source-read') {
      await assert.rejects(capture(), { code: 'LINE_REPLY_SOURCE_STALE' });
      assert.deepEqual(actionCalls(calls), []);
      continue;
    }
    const pending = await capture();
    const confirm = () => ui.confirmReplySourceTarget({
      chatName: CHAT, token: pending.replySourceTarget.token, observedSource: source,
      sourceRect: { x: 60, y: 130, width: 140, height: 72 }, sourcePoint: { x: 120, y: 164 },
    });
    if (replaceAt === 'use') await confirm();
    currentRef = 'chat:ffffffffffffffffffffffff';
    const priorCalls = calls.length;
    await assert.rejects(replaceAt === 'confirm' ? confirm() : ui.messageAction({
      chatName: CHAT, messageText: source.text, action: 'reply', source, sourceToken: pending.replySourceTarget.token,
    }), { code: 'LINE_REPLY_SOURCE_STALE' });
    if (replaceAt === 'confirm') assert.equal(calls.length, priorCalls);
    assert.deepEqual(actionCalls(calls), []);
  }
});

test('a caller-confirmed main header accepts an unlabeled empty rich composer only inside its unique body', async () => {
  const { ui } = fakeEnvironment({
    windows: [CURRENT_MAIN_TARGET],
    states: Array.from({ length: 7 }, (_, index) => currentMainVisualState({ suffix: `main-empty-${index}` })),
    ...visualHelpers(),
    randomToken: () => 'main-empty-token',
    now: () => 1_000_000,
  });

  const pending = await ui.getState({ chatName: CHAT, includeScreenshot: true });
  await ui.confirmChat({ chatName: CHAT, token: pending.visualVerification.token, observedHeader: CHAT });
  const draft = await ui.getDraft({ chatName: CHAT });
  assert.equal(draft.draft, '');
  assert.equal(draft.verification.draft, 'main-composer-empty-structure');
});

test('a main structural composer does not write a global-search Edit', async () => {
  const message = 'only the main composer may receive this';
  const states = Array.from({ length: 8 }, (_, index) => currentMainVisualState({
    suffix: `scoped-write-${index}`,
    includeGlobalSearch: true,
  }));
  states.push(currentMainVisualState({
    suffix: 'scoped-write-after',
    editorValue: message,
    includeGlobalSearch: true,
  }));
  const { ui, calls } = fakeEnvironment({
    windows: [CURRENT_MAIN_TARGET],
    states,
    ...visualHelpers(),
    randomToken: () => 'scoped-write-token',
    now: () => 1_000_000,
  });

  const pending = await ui.getState({ chatName: CHAT, includeScreenshot: true });
  await ui.confirmChat({ chatName: CHAT, token: pending.visualVerification.token, observedHeader: CHAT });
  const result = await ui.setDraft({ chatName: CHAT, message });
  const write = calls.find(call => call.name === 'set_value');
  assert.equal(result.changed, true);
  assert.match(write.args.element_token, /^token-72/);
  assert.doesNotMatch(write.args.element_token, /^token-109/);
});

test('ambiguous rich editors in the verified main body are refused', async () => {
  const { ui } = fakeEnvironment({
    windows: [CURRENT_MAIN_TARGET],
    states: Array.from({ length: 7 }, (_, index) => currentMainVisualState({
      suffix: `ambiguous-main-${index}`,
      extraMainComposer: true,
    })),
    ...visualHelpers(),
    randomToken: () => 'ambiguous-main-token',
    now: () => 1_000_000,
  });

  const pending = await ui.getState({ chatName: CHAT, includeScreenshot: true });
  await ui.confirmChat({ chatName: CHAT, token: pending.visualVerification.token, observedHeader: CHAT });
  await assert.rejects(ui.getDraft({ chatName: CHAT }), { code: 'LINE_COMPOSER_UNVERIFIED' });
});

test('a semantic main header switch before set_value refuses the mutation', async () => {
  const { ui, calls } = fakeEnvironment({
    states: [
      state([header(), composer('')], 'inspect'),
      state([header(), composer('')], 'read-before-write'),
      state([headerFor('另一個聊天室'), composer('')], 'switched-before-set-value'),
    ],
  });

  await assert.rejects(
    ui.setDraft({ chatName: CHAT, message: 'must not reach another chat' }),
    { code: 'LINE_CHAT_STALE' },
  );
  assert.equal(calls.some(call => call.name === 'set_value'), false);
});

test('a chat switch after set_value cannot verify a draft in another chat', async () => {
  const text = 'the same draft text does not prove the recipient';
  for (const method of ['setDraft']) {
    const { ui, calls } = fakeEnvironment({
      states: [
        state([header(), composer('')], 'inspect'),
        state([header(), composer('')], 'write-initial'),
        state([header(), composer('')], 'write-before'),
        state([headerFor('另一個聊天室'), composer(text)], 'switched-after-write'),
      ],
    });
    await assert.rejects(ui[method]({ chatName: CHAT, message: text, autoSend: false }), error =>
      error?.code === 'LINE_UI_POSTCONDITION_UNAVAILABLE'
      && error?.operationMayHaveCompleted === true
      && error?.details?.previousCode === 'LINE_CHAT_STALE'
      && !/No action was taken/u.test(error.message));
    assert.equal(calls.filter(call => call.name === 'set_value').length, 1);
    assert.equal(calls.some(call => call.name === 'press_key'), false);
  }
});

test('file-picker failure after an optional draft is reported as partial local state', async () => {
  const { ui } = fakeEnvironment({
    states: [
      state([header(), composer('')], 'inspect'),
      state([header(), composer('')], 'stage-initial'),
      state([header(), composer('')], 'optional-write-initial'),
      state([header(), composer('')], 'optional-write-before'),
      state([header(), composer('attachment note')], 'optional-write-after'),
      state([header(), composer('attachment note')], 'fresh-before-picker'),
    ],
    innerAutomation: {
      async stageFileManual() {
        return { success: false, error: 'picker did not verify' };
      },
    },
  });

  await assert.rejects(
    ui.stageFile({
      chatName: CHAT,
      filePath: 'C:\\example\\attachment.pdf',
      optionalMessage: 'attachment note',
    }),
    error => error?.code === 'LINE_STAGE_UNVERIFIED'
      && error?.operationMayHaveCompleted === true
      && error?.details?.draftMayBeStaged === true
      && error?.details?.pickerMayBeOpen === true,
  );
});

test('legacy history holds one UI lock and guards the exact main chat around every raw helper', async () => {
  const trace = [];
  const { ui, locks } = fakeEnvironment({
    states: [
      state([header(), composer('')], 'history-inspect'),
      state([header(), composer('')], 'history-before-page'),
      state([header(), composer('')], 'history-after-page'),
      state([header(), composer('')], 'history-after-copy'),
    ],
    trace,
    innerAutomation: {
      async pageUp(times) {
        trace.push(['raw-page-up', times]);
      },
      async copyAllChatToClipboard() {
        trace.push('raw-copy');
        return 'verified fixture history';
      },
    },
  });

  assert.equal(await ui.readLegacyHistory({ chatName: CHAT, pageUpTimes: 50 }), 'verified fixture history');
  assert.deepEqual(locks, ['ui-read-legacy-history']);
  assert.deepEqual(trace, [
    'list_windows',
    'get_window_state',
    'get_window_state',
    ['raw-page-up', 50],
    'get_window_state',
    'raw-copy',
    'get_window_state',
  ]);
});

test('legacy history validates its bounded page count before opening a UI session', async () => {
  for (const pageUpTimes of [0, 51, 1.5, '5']) {
    const trace = [];
    const { ui, locks, automationCalls } = fakeEnvironment({ trace });
    await assert.rejects(
      ui.readLegacyHistory({ chatName: CHAT, pageUpTimes }),
      { code: 'LINE_INVALID_ARGUMENT' },
    );
    assert.deepEqual(trace, []);
    assert.deepEqual(locks, []);
    assert.deepEqual(automationCalls, []);
  }
});

test('legacy history refuses post-page chat drift before clipboard copy', async () => {
  const rawCalls = [];
  const { ui } = fakeEnvironment({
    states: [
      state([header(), composer('')], 'history-inspect'),
      state([header(), composer('')], 'history-before-page'),
      state([headerFor('另一個聊天室'), composer('')], 'history-after-page-drift'),
    ],
    innerAutomation: {
      async pageUp(times) {
        rawCalls.push(['page-up', times]);
      },
      async copyAllChatToClipboard() {
        rawCalls.push('copy');
        return 'must not be copied';
      },
    },
  });

  await assert.rejects(
    ui.readLegacyHistory({ chatName: CHAT, pageUpTimes: 5 }),
    { code: 'LINE_CHAT_STALE' },
  );
  assert.deepEqual(rawCalls, [['page-up', 5]]);
});

test('legacy history discards copied text when the post-copy chat guard drifts', async () => {
  const secret = 'text copied from a no-longer-proven chat';
  const rawCalls = [];
  const { ui } = fakeEnvironment({
    states: [
      state([header(), composer('')], 'history-inspect'),
      state([header(), composer('')], 'history-before-page'),
      state([header(), composer('')], 'history-after-page'),
      state([headerFor('另一個聊天室'), composer('')], 'history-after-copy-drift'),
    ],
    innerAutomation: {
      async pageUp() {
        rawCalls.push('page-up');
      },
      async copyAllChatToClipboard() {
        rawCalls.push('copy');
        return secret;
      },
    },
  });

  await assert.rejects(
    ui.readLegacyHistory({ chatName: CHAT, pageUpTimes: 10 }),
    error => error?.code === 'LINE_CHAT_STALE' && !JSON.stringify(error).includes(secret),
  );
  assert.deepEqual(rawCalls, ['page-up', 'copy']);
});

test('legacy history leaves a verified detached chat unchanged because raw AHK targets only the main window', async () => {
  const detached = { ...TARGET, title: CHAT, window_id: 100 };
  const rawCalls = [];
  const { ui } = fakeEnvironment({
    windows: [detached],
    states: [state([composer('')], 'detached-history')],
    innerAutomation: {
      async pageUp() {
        rawCalls.push('page-up');
      },
      async copyAllChatToClipboard() {
        rawCalls.push('copy');
        return 'detached text';
      },
    },
  });

  await assert.rejects(
    ui.readLegacyHistory({ chatName: CHAT, pageUpTimes: 5 }),
    error => error?.code === 'LINE_HISTORY_TARGET_UNVERIFIED'
      && error?.operationMayHaveCompleted === false,
  );
  assert.deepEqual(rawCalls, []);
});

test('missing CUA or an unverified main header cannot reach legacy copy helpers', async () => {
  const rawCalls = [];
  const unavailable = Object.assign(new Error('CUA unavailable'), {
    code: 'LINE_UI_BACKEND_UNAVAILABLE',
    operationMayHaveCompleted: false,
  });
  const automation = {
    automation: {
      async pageUp() { rawCalls.push('page-up'); },
      async copyAllChatToClipboard() { rawCalls.push('copy'); return 'text'; },
      async sendMessage() { rawCalls.push('send'); return { success: true }; },
    },
  };
  const missingUi = new LineUi({
    automation,
    withClient: async () => { throw unavailable; },
    runOperation: async (_kind, callback) => callback(),
  });
  await assert.rejects(missingUi.readLegacyHistory({ chatName: CHAT, pageUpTimes: 5 }), error => error === unavailable);

  const unverified = fakeEnvironment({
    states: Array.from({ length: 4 }, (_, index) => state([
      element(1, { label: CHAT, role: 'ListItem', semantic_role: 'search-result' }),
      composer(''),
    ], `unverified-history-${index}`)),
    innerAutomation: automation.automation,
  });
  await assert.rejects(
    unverified.ui.readLegacyHistory({ chatName: CHAT, pageUpTimes: 5 }),
    { code: 'LINE_CHAT_UNVERIFIED' },
  );
  assert.deepEqual(rawCalls, []);
});
