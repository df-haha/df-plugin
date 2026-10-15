import assert from 'node:assert/strict';
import test from 'node:test';

import { LineUi } from '../src/extensions/line-ui.mjs';

const CHAT = 'Fixture room';
const WINDOW = {
  app_name: 'LINE.exe', title: CHAT, pid: 42, window_id: 99,
  bounds: { x: 1120, y: 422, width: 375, height: 588 },
  is_on_screen: true, minimized: false,
};

function element(element_index, parent_index, role, x, y, w, h, label) {
  return {
    element_index, parent_index, role, frame: { x, y, w, h },
    element_token: `token-${element_index}`, enabled: true,
    ...(label ? { label } : {}),
  };
}

function detachedElements({ duplicateHeader = false, brokenToolbar = false } = {}) {
  const items = [
    element(0, undefined, 'Window', 1120, 422, 375, 588),
    element(9, 0, 'Custom', 1120, 445, 375, 565),
    element(10, 9, 'Group', 1120, 445, 375, 565),
    element(11, 10, 'Group', 1120, 450, 375, 560),
    element(12, 11, 'Group', 1120, 454, 375, 52),
    element(17, 12, 'Group', 1378, 468, 24, 24),
    element(19, 12, 'Group', 1407, 468, 24, 24),
    element(20, 12, 'Group', 1437, 468, 24, 24),
    element(21, 12, 'Group', brokenToolbar ? 1470 : 1466, 468, 16, 24),
    // The announcement strip is a sibling between the title and message body.
    element(22, 11, 'Group', 1120, 512, 375, 10),
    element(23, 11, 'Group', 1120, 522, 375, 488),
  ];
  if (duplicateHeader) items.push(
    element(50, 11, 'Group', 1120, 500, 375, 52),
    element(51, 50, 'Group', 1378, 514, 24, 24),
    element(52, 50, 'Group', 1407, 514, 24, 24),
    element(53, 50, 'Group', 1437, 514, 24, 24),
    element(54, 50, 'Group', 1466, 514, 16, 24),
  );
  return items;
}

function detachedSearchBar({ withoutIcon = false, duplicateEdit = false } = {}) {
  const items = [
    element(80, 23, 'Custom', 1120, 522, 375, 488),
    element(81, 80, 'Group', 1120, 522, 375, 366),
    element(82, 81, 'Group', 1120, 522, 375, 56),
    element(83, 82, 'Group', 1131, 534, 292, 32),
    element(85, 83, 'Edit', 1161, 535, 261, 30),
    element(86, 83, 'Group', 1422, 534, 1, 32),
    element(87, 82, 'Group', 1434, 540, 20, 20),
    element(88, 82, 'Group', 1464, 540, 20, 20),
    element(90, 23, 'Edit', 1130, 896, 357, 76),
    element(91, 90, 'Group', 1130, 896, 357, 76, 'qt_scrollarea_viewport'),
  ];
  if (!withoutIcon) items.push(element(84, 83, 'Group', 1137, 538, 24, 24));
  if (duplicateEdit) items.push(element(89, 83, 'Edit', 1161, 535, 261, 30));
  return items;
}

function snapshot(elements, suffix) {
  return {
    snapshot_id: suffix, window_title: CHAT, elements,
    images: [{ type: 'image', mimeType: 'image/png', data: 'synthetic', width: 375, height: 588 }],
    screenshot_width: 375, screenshot_height: 588,
  };
}

function environment(states, window = WINDOW, windowList = () => [window], ocrHelpers = {}, chatType = 'group') {
  const queue = [...states];
  const calls = [];
  const activations = [];
  const api = {
    tools: new Set(['list_windows', 'get_window_state', 'click', 'hotkey']),
    async call(name, args) {
      calls.push({ name, args });
      if (name === 'list_windows') return { windows: windowList(calls) };
      if (name === 'get_window_state') {
        assert.ok(queue.length, 'unexpected snapshot');
        return queue.shift();
      }
      return { effect: 'confirmed', route: 'pixel' };
    },
  };
  const ui = new LineUi({
    withClient: callback => callback(api),
    runOperation: (_kind, callback) => callback(),
    automation: { activateLine: async target => {
      activations.push(target);
      return { success: true };
    } },
    readNamedIdentity: async () => ({ chatRef: 'chat:0123456789abcdef01234567', kind: chatType }),
    imageDimensions: image => ({ width: image.width, height: image.height }),
    ...ocrHelpers,
  });
  return { ui, calls, activations };
}

test('detached feature More uses the unique title toolbar above announcements', async () => {
  const base = detachedElements();
  const menu = element(60, 0, 'MenuItem', 1200, 510, 80, 24, 'Albums');
  const surface = element(61, 0, 'Dialog', 1140, 540, 200, 200, 'Albums');
  const { ui, calls, activations } = environment([
    snapshot(base, 'inspect'), snapshot(base, 'preflight'), snapshot(base, 'before-more'),
    snapshot([...base, menu], 'after-more'), snapshot([...base, menu], 'guard'),
    snapshot([...base, menu], 'before-item'), snapshot([...base, surface], 'after-item'),
    snapshot([...base, surface], 'outcome'),
  ]);
  const result = await ui.openFeature({ chatName: CHAT, feature: 'albums', deliveryMode: 'foreground' });
  assert.equal(result.opened, true);
  assert.deepEqual(activations, [{ pid: WINDOW.pid, window_id: WINDOW.window_id, title: CHAT }]);
  const clicks = calls.filter(call => call.name === 'click');
  assert.equal(clicks.length, 2);
  assert.deepEqual([clicks[0].args.x, clicks[0].args.y], [354, 58]);
  assert.equal('element_token' in clicks[0].args, false);
  assert.equal(clicks[1].args.element_token, 'token-60');
});

test('More discovers a Qt popup HWND that was hidden before the click', async () => {
  const base = detachedElements();
  const popup = { ...WINDOW, title: 'LINE', window_id: 100,
    bounds: { x: 1300, y: 480, width: 199, height: 446 } };
  const menu = element(60, 0, 'MenuItem', 1320, 520, 80, 24, 'Files');
  const surface = element(61, 0, 'Dialog', 1140, 540, 200, 200, 'Files');
  const { ui, calls } = environment([
    snapshot(base, 'inspect'), snapshot(base, 'preflight'), snapshot(base, 'before-more'),
    snapshot(base, 'after-more'),
    { ...snapshot([menu], 'popup-discovery'), window_title: 'LINE' },
    snapshot(base, 'guard-before-item'),
    { ...snapshot([menu], 'popup-before-item'), window_title: 'LINE' },
    { ...snapshot([], 'popup-after-item'), window_title: 'LINE' },
    snapshot([...base, surface], 'outcome'),
  ], WINDOW, observedCalls => [WINDOW, {
    ...popup,
    is_on_screen: observedCalls.some(call => call.name === 'click'),
  }]);
  const result = await ui.openFeature({ chatName: CHAT, feature: 'files' });
  assert.equal(result.opened, true);
  const clicks = calls.filter(call => call.name === 'click');
  assert.equal(clicks.length, 2);
  assert.equal(clicks[1].args.window_id, popup.window_id);
  assert.equal(clicks[1].args.element_token, 'token-60');
});

test('unlabelled Qt menu uses only its unique popup-sized screenshot for OCR', async () => {
  const base = detachedElements();
  const popup = { ...WINDOW, title: 'LINE', window_id: 100,
    bounds: { x: 1456, y: 482, width: 199, height: 446 } };
  const popupElements = [
    element(0, undefined, 'Window', 1456, 482, 199, 446, 'LINE'),
    element(2, 0, 'Group', 1466, 492, 179, 426),
    element(15, 2, 'Group', 1466, 644, 179, 27),
    element(16, 15, 'Group', 1485, 649, 26, 17),
  ];
  const popupState = suffix => ({
    ...snapshot(popupElements, suffix), window_title: 'LINE',
    images: [
      { type: 'image', width: 375, height: 588, data: 'main' },
      { type: 'image', width: 11, height: 446, data: 'decoration' },
      { type: 'image', width: 199, height: 446, data: 'popup' },
    ],
  });
  const surface = element(61, 0, 'Dialog', 1140, 540, 200, 200, 'Files');
  const recognized = [];
  const ocrHelpers = {
    recognizeImage: async image => {
      recognized.push(image.data);
      return {
        coordinateSpace: 'input-png-pixels', scaleFactor: 1,
        width: image.width, height: image.height,
        lines: image.data === 'popup' ? [{ text: 'Files', x: 30, y: 160, width: 50, height: 17 }] : [],
      };
    },
    findImageLabel: (ocr, labels) => {
      const lines = ocr.lines.filter(line => labels.includes(line.text));
      return lines.length === 1 ? { label: lines[0].text, lines,
        x: lines[0].x, y: lines[0].y, width: lines[0].width, height: lines[0].height } : null;
    },
  };
  const { ui, calls } = environment([
    snapshot(base, 'inspect'), snapshot(base, 'preflight'), snapshot(base, 'before-more'),
    snapshot(base, 'after-more'), popupState('popup-discovery'),
    snapshot(base, 'guard-before-item'), popupState('popup-before-item'),
    popupState('popup-after-item'), snapshot([...base, surface], 'outcome'),
  ], WINDOW, observedCalls => [WINDOW, {
    ...popup,
    is_on_screen: observedCalls.filter(call => call.name === 'click').length === 1,
  }], ocrHelpers);
  const result = await ui.openFeature({ chatName: CHAT, feature: 'files' });
  assert.equal(result.opened, true);
  const clicks = calls.filter(call => call.name === 'click');
  assert.equal(clicks.length, 2);
  assert.deepEqual([clicks[1].args.window_id, clicks[1].args.x, clicks[1].args.y], [100, 55, 169]);
  assert.deepEqual(recognized.filter(value => value !== 'synthetic'), ['popup', 'popup']);
});

test('unlabelled Qt menu refuses ambiguous popup screenshots before item input', async () => {
  const base = detachedElements();
  const popup = { ...WINDOW, title: 'LINE', window_id: 100,
    bounds: { x: 1456, y: 482, width: 199, height: 446 } };
  const popupState = suffix => ({
    ...snapshot([element(0, undefined, 'Window', 1456, 482, 199, 446)], suffix),
    window_title: 'LINE',
    images: [
      { type: 'image', width: 199, height: 446, data: 'popup-one' },
      { type: 'image', width: 199, height: 446, data: 'popup-two' },
    ],
  });
  let ocrCalls = 0;
  const { ui, calls } = environment([
    snapshot(base, 'inspect'), snapshot(base, 'preflight'), snapshot(base, 'before-more'),
    snapshot(base, 'after-more'), popupState('first-popup'), popupState('second-popup'),
  ], WINDOW, observedCalls => [WINDOW, {
    ...popup, is_on_screen: observedCalls.some(call => call.name === 'click'),
  }], {
    recognizeImage: async image => {
      ocrCalls += 1;
      return { coordinateSpace: 'input-png-pixels', scaleFactor: 1,
        width: image.width, height: image.height, lines: [] };
    },
    findImageLabel: () => null,
  });
  await assert.rejects(ui.openFeature({ chatName: CHAT, feature: 'files' }),
    error => error?.code === 'LINE_FEATURE_UNAVAILABLE' && error?.operationMayHaveCompleted === true);
  assert.equal(calls.filter(call => call.name === 'click').length, 1);
  assert.equal(ocrCalls, 2); // Only the original chat snapshots are read.
});

test('a shortcut recognizes its previously hidden feature window after activation', async () => {
  const base = detachedElements();
  const popup = { ...WINDOW, title: 'Stickers', window_id: 101,
    bounds: { x: 1280, y: 480, width: 250, height: 300 } };
  const { ui, calls } = environment([
    snapshot(base, 'inspect'), snapshot(base, 'preflight'), snapshot(base, 'before-shortcut'),
    snapshot(base, 'after-shortcut'), snapshot(base, 'outcome'),
    { ...snapshot([], 'feature-window'), window_title: 'Stickers' },
  ], WINDOW, observedCalls => [WINDOW, {
    ...popup,
    is_on_screen: observedCalls.some(call => call.name === 'hotkey'),
  }]);
  const result = await ui.openFeature({ chatName: CHAT, feature: 'stickers' });
  assert.equal(result.opened, true);
  assert.equal(result.verification, 'exact-feature-child-window-title');
  assert.equal(calls.filter(call => call.name === 'hotkey').length, 1);
});

test('detached Search uses the same header and fresh screenshot mapping', async () => {
  const base = detachedElements();
  const opened = [...base, ...detachedSearchBar()];
  const { ui, calls } = environment([
    snapshot(base, 'inspect'), snapshot(base, 'preflight'), snapshot(base, 'before-search'),
    snapshot(opened, 'after-search'), snapshot(opened, 'outcome'),
  ]);
  const result = await ui.openFeature({ chatName: CHAT, feature: 'search' });
  assert.equal(result.opened, true);
  assert.equal(result.verification, 'structural-detached-chat-search-bar');
  const clicks = calls.filter(call => call.name === 'click');
  assert.equal(clicks.length, 1);
  assert.deepEqual([clicks[0].args.x, clicks[0].args.y], [270, 58]);
});

test('already-open detached Search is idempotent despite the separate composer Edit', async () => {
  const opened = [...detachedElements(), ...detachedSearchBar()];
  const { ui, calls } = environment([snapshot(opened, 'inspect'), snapshot(opened, 'preflight')]);
  const result = await ui.openFeature({ chatName: CHAT, feature: 'search' });
  assert.equal(result.opened, false);
  assert.equal(result.alreadyOpen, true);
  assert.equal(result.verification, 'structural-detached-chat-search-bar');
  assert.equal(calls.some(call => call.name === 'click'), false);
});

test('detached draft finds the unique rich composer while Search adds another Edit', async () => {
  const opened = [...detachedElements(), ...detachedSearchBar()];
  for (const chatType of ['direct', 'group']) {
    const { ui, calls } = environment([
      snapshot(opened, 'inspect'), snapshot(opened, 'draft-read'),
    ], WINDOW, () => [WINDOW], {}, chatType);
    const result = await ui.getDraft({ chatName: CHAT, chatType });
    assert.equal(result.draft, '');
    assert.equal(result.verification.draft, 'detached-composer-empty-structure');
    assert.equal(calls.some(call => ['click', 'set_value'].includes(call.name)), false);
  }
});

test('detached draft writing selects the rich composer, not the Search Edit', async () => {
  const opened = [...detachedElements(), ...detachedSearchBar()];
  const written = opened.map(item => item.element_index === 90
    ? { ...item, value: 'draft only' } : item);
  const { ui, calls } = environment([
    snapshot(opened, 'inspect'), snapshot(opened, 'initial-read'),
    snapshot(opened, 'write-before'), snapshot(written, 'write-after'),
  ]);
  const result = await ui.setDraft({ chatName: CHAT, message: 'draft only' });
  assert.equal(result.changed, true);
  const writes = calls.filter(call => call.name === 'set_value');
  assert.equal(writes.length, 1);
  assert.equal(writes[0].args.element_token, 'token-90');
});

test('detached draft refuses two rich editors even with Search open', async () => {
  const opened = [
    ...detachedElements(), ...detachedSearchBar(),
    element(92, 23, 'Edit', 1130, 795, 357, 76),
    element(93, 92, 'Group', 1130, 795, 357, 76, 'qt_scrollarea_viewport'),
  ];
  const { ui, calls } = environment([
    snapshot(opened, 'inspect'), snapshot(opened, 'draft-read'),
  ]);
  await assert.rejects(ui.getDraft({ chatName: CHAT, chatType: 'group' }),
    { code: 'LINE_COMPOSER_UNVERIFIED' });
  assert.equal(calls.some(call => ['click', 'set_value'].includes(call.name)), false);
});

test('detached Search refuses an unstructured Edit as an already-open proof', async () => {
  for (const options of [{ withoutIcon: true }, { duplicateEdit: true }]) {
    const base = [...detachedElements(), ...detachedSearchBar(options)];
    const opened = [...detachedElements(), ...detachedSearchBar()];
    const { ui, calls } = environment([
      snapshot(base, 'inspect'), snapshot(base, 'preflight'), snapshot(base, 'before-search'),
      snapshot(opened, 'after-search'), snapshot(opened, 'outcome'),
    ]);
    const result = await ui.openFeature({ chatName: CHAT, feature: 'search' });
    assert.equal(result.opened, true);
    assert.equal(calls.filter(call => call.name === 'click').length, 1);
  }
});

test('detached ambiguous or malformed header refuses before input', async () => {
  for (const options of [{ duplicateHeader: true }, { brokenToolbar: true }]) {
    const base = detachedElements(options);
    const { ui, calls } = environment([
      snapshot(base, 'inspect'), snapshot(base, 'preflight'), snapshot(base, 'before'),
    ]);
    await assert.rejects(ui.openFeature({ chatName: CHAT, feature: 'albums' }),
      { code: 'LINE_CONTROL_NOT_UNIQUE' });
    assert.equal(calls.some(call => call.name === 'click'), false);
  }
});

test('detached toolbar refuses a missing or mismatched fresh screenshot', async () => {
  const base = detachedElements();
  for (const invalid of [
    { images: [] },
    { screenshot_width: 374 },
  ]) {
    const { ui, calls } = environment([
      snapshot(base, 'inspect'), snapshot(base, 'preflight'),
      { ...snapshot(base, 'before'), ...invalid },
    ]);
    await assert.rejects(ui.openFeature({ chatName: CHAT, feature: 'search' }),
      { code: 'LINE_UI_SELECTOR_UNAVAILABLE' });
    assert.equal(calls.some(call => call.name === 'click'), false);
  }
});
