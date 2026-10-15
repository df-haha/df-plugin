import test from 'node:test';
import assert from 'node:assert/strict';

import { createForwardUi } from '../src/extensions/line-forward-ui.mjs';
import { replySourceVisualView } from '../src/extensions/line-ui.mjs';
import { LineToolError } from '../src/extensions/line-runtime.mjs';

const target = { pid: 321, window_id: 654 };
const window = { ...target, app_name: 'LINE.exe', title: 'LINE',
  bounds: { x: 0, y: 0, width: 400, height: 600 },
  is_on_screen: true, minimized: false };
const binding = {
  source: { chatName: '來源', chatType: 'direct', chatRef: `chat:${'a'.repeat(24)}`,
    sourceRef: `message:${'b'.repeat(24)}`, digest: 'c'.repeat(64),
    sourceTimestamp: 1_700_000_000_000, contentType: 0, text: '完整原文',
    date: '2023-11-14', time: '22:13', sender: '甲' },
  recipient: { chatName: '收件群', chatType: 'group', chatRef: `chat:${'d'.repeat(24)}` },
  ownSenderRef: `sender:${'e'.repeat(24)}`,
};

function fixture(request = binding, sourceState = null, { recipientKind = request.recipient.chatType } = {}) {
  let phase = 'source';
  let selectedName = request.recipient.chatName;
  const calls = [];
  let beforeClick = false;
  const source = { role: 'message', label: request.source.text,
    contentType: request.source.contentType, date: request.source.date, time: request.source.time,
    sender: request.source.sender,
    fileName: request.source.attachment?.fileName,
    declaredFileBytes: request.source.attachment?.declaredFileBytes, element_index: 1 };
  const dialog = { role: 'dialog', label: 'Share', element_index: 1 };
  const search = { role: 'edit', label: 'Search', element_index: 2 };
  const result = { role: 'recipient', label: request.recipient.chatName,
    chatType: recipientKind, selected: false, element_index: 3 };
  const selected = () => ({ ...result, label: selectedName, selected: true });
  const share = { role: 'button', label: 'Share', enabled: true, element_index: 4 };
  const state = () => ({ snapshot_id: `${phase}-snapshot`, elements: {
    source: sourceState?.elements ?? [source], menu: [source, { role: 'menuitem', label: 'Share', element_index: 5 }],
    dialog: [dialog, search], results: [dialog, search, result],
    selected: [dialog, search, selected(), share], sent: [dialog],
  }[phase], selectedCount: phase === 'selected' ? 1 : 0,
    images: phase === 'source' ? sourceState?.images ?? [] : [],
    ...(phase === 'source' ? { screenshot_width: sourceState?.screenshot_width,
      screenshot_height: sourceState?.screenshot_height } : {}) });
  const api = { tools: new Set(['press_key']), call: async (name, args) => {
    calls.push({ name, args, phase });
    if (name === 'list_windows') return { windows: [window] };
    if (name === 'get_window_state') return state();
    if (name === 'click' && phase === 'source' && sourceState?.inlineShare === true) {
      phase = 'dialog'; return {};
    }
    if (name === 'right_click' && phase === 'source') { phase = 'menu'; return {}; }
    if (name === 'click' && phase === 'menu') { phase = 'dialog'; return {}; }
    if (name === 'set_value' && phase === 'dialog') { phase = 'results'; return {}; }
    if (name === 'click' && phase === 'results') { phase = 'selected'; return {}; }
    if (name === 'click' && phase === 'selected') {
      if (!beforeClick) throw new Error('final Share clicked before journal commit');
      phase = 'sent'; return {};
    }
    if (name === 'press_key' && phase === 'selected' && args.key === 'escape') {
      phase = 'source'; return {};
    }
    throw new Error(`unexpected ${name} during ${phase}`);
  } };
  const ui = {
    withClient: callback => callback(api),
    readChatIdentity: async ({ chatName }) => ({ chatName,
      chatRef: request.recipient.chatRef,
      chatIdentity: { kind: request.recipient.chatType, displayName: chatName,
        guiDisplayNameUnique: true, uiIdentityVerified: false },
      scope: { kind: 'local_gui_chat_identity' }, count: 0, messages: [] }),
    withForwardSourceChat: (_binding, callback) => callback(api,
      { target, window, lineWindows: [window], state: state(), guard: undefined }),
    ocr: {},
  };
  return { ui, calls, get phase() { return phase; }, set phase(value) { phase = value; },
    set selectedName(value) { selectedName = value; },
    commit() { beforeClick = true; } };
}

test('prepares original-message Share and clicks final Share only after journal callback', async () => {
  const fake = fixture();
  const forward = createForwardUi(fake.ui);
  const prepared = await forward.prepare(binding);
  assert.equal(prepared.stage, 'PREPARED');
  assert.equal(fake.phase, 'selected');
  assert.deepEqual(fake.calls.filter(call => call.name === 'click').map(call => call.phase),
    ['menu', 'results']);
  const ready = await forward.assertReady(binding, prepared.session);
  assert.equal(ready.evidence.selectedCount, 1);
  const sent = await forward.dispatch(binding, prepared.session, async () => fake.commit());
  assert.equal(sent.dispatched, true);
  assert.equal(fake.phase, 'sent');
  assert.deepEqual(fake.calls.filter(call => call.name === 'click').map(call => call.phase),
    ['menu', 'results', 'selected']);
  await assert.rejects(forward.dispatch(binding, prepared.session, async () => fake.commit()),
    { code: 'LINE_FORWARD_PREPARED_STALE' });
});

test('tampered or process-restored session cannot authorize final Share', async () => {
  const fake = fixture();
  const forward = createForwardUi(fake.ui);
  const { session } = await forward.prepare(binding);
  await assert.rejects(forward.assertReady(binding, { ...session, dialogTitle: 'other' }),
    { code: 'LINE_FORWARD_PREPARED_STALE' });
  const restarted = createForwardUi(fake.ui);
  await assert.rejects(restarted.dispatch(binding, session, async () => fake.commit()),
    { code: 'LINE_FORWARD_PREPARED_STALE' });
  assert.equal(fake.calls.filter(call => call.name === 'click' && call.phase === 'selected').length, 0);
});

test('wrong selected recipient and stale window refuse before callback and final input', async () => {
  const fake = fixture();
  const forward = createForwardUi(fake.ui);
  const { session } = await forward.prepare(binding);
  let committed = false;
  const wrong = { ...binding, recipient: { ...binding.recipient, chatName: '別的群' } };
  await assert.rejects(forward.dispatch(wrong, session, async () => { committed = true; }),
    { code: 'LINE_FORWARD_PREPARED_STALE' });
  fake.phase = 'results';
  await assert.rejects(forward.dispatch(binding, session, async () => { committed = true; }));
  assert.equal(committed, false);
  assert.equal(fake.calls.filter(call => call.name === 'click' && call.phase === 'selected').length, 0);
});

test('selected recipient changing after preparation refuses final Share', async () => {
  const fake = fixture();
  const forward = createForwardUi(fake.ui);
  const { session } = await forward.prepare(binding);
  fake.selectedName = '別的群';
  let committed = false;
  await assert.rejects(forward.dispatch(binding, session, async () => { committed = true; }),
    { code: 'LINE_FORWARD_RECIPIENT_MISMATCH' });
  assert.equal(committed, false);
  assert.equal(fake.calls.filter(call => call.name === 'click' && call.phase === 'selected').length, 0);
});

test('empty-text file source can use exact accessible file metadata', async () => {
  const file = { ...binding, source: { ...binding.source, contentType: 14, text: null,
    attachment: { fileName: '報表.pdf', declaredFileBytes: 12345 } } };
  const fake = fixture(file);
  const forward = createForwardUi(fake.ui);
  const prepared = await forward.prepare(file);
  assert.equal(prepared.stage, 'PREPARED');
  assert.equal(fake.phase, 'selected');
  assert.equal(fake.calls.filter(call => call.name === 'click' && call.phase === 'selected').length, 0);
});

test('one exact recipient name inherits kind only from the unique local binding', async () => {
  const fake = fixture(binding, null, { recipientKind: undefined });
  const forward = createForwardUi(fake.ui);
  const prepared = await forward.prepare(binding);
  assert.equal(prepared.stage, 'PREPARED');
  assert.equal(prepared.challenge.recipient.chatType, 'group');
});

test('explicit UI recipient kind conflicting with the binding refuses selection', async () => {
  const fake = fixture(binding, null, { recipientKind: 'direct' });
  const forward = createForwardUi(fake.ui);
  const result = await forward.prepare(binding);
  assert.equal(result.stage, 'PREPARE_NOT_READY');
  assert.equal(result.currentStage, 'RECIPIENT_SELECTION_REQUIRED');
  assert.equal(fake.calls.filter(call => call.name === 'click' && call.phase === 'results').length, 0);
});

test('GUI recipient name-family ambiguity refuses before source input and before final dispatch', async () => {
  const fake = fixture();
  const original = fake.ui.readChatIdentity;
  fake.ui.readChatIdentity = async args => ({ ...await original(args),
    chatIdentity: { ...(await original(args)).chatIdentity, guiDisplayNameUnique: false } });
  const forward = createForwardUi(fake.ui);
  const refused = await forward.prepare(binding);
  assert.equal(refused.reason, 'LINE_FORWARD_RECIPIENT_IDENTITY_UNVERIFIED');
  assert.equal(fake.calls.filter(call => ['right_click', 'click'].includes(call.name)).length, 0);

  fake.ui.readChatIdentity = original;
  const prepared = await forward.prepare(binding);
  assert.equal(prepared.stage, 'PREPARED');
  fake.ui.readChatIdentity = async args => ({ ...await original(args),
    chatIdentity: { ...(await original(args)).chatIdentity, guiDisplayNameUnique: false } });
  let committed = false;
  await assert.rejects(forward.dispatch(binding, prepared.session, async () => { committed = true; }),
    { code: 'LINE_FORWARD_RECIPIENT_IDENTITY_UNVERIFIED' });
  assert.equal(committed, false);
  assert.equal(fake.calls.filter(call => call.name === 'click' && call.phase === 'selected').length, 0);
});

test('custom-drawn file card uses fresh exact OCR filename, bytes, sender and time geometry', async () => {
  const file = { ...binding, source: { ...binding.source, contentType: 14, text: null,
    attachment: { fileName: '報表.pdf', declaredFileBytes: 12345 } } };
  const e = (index, role, x, y, w, h, parent_index, extra = {}) =>
    ({ element_index: index, role, frame: { x, y, w, h }, parent_index, ...extra });
  const sourceState = { elements: [
    e(0, 'Window', 0, 0, 400, 600, undefined),
    e(40, 'Group', 100, 0, 300, 600, 0),
    e(41, 'Group', 100, 0, 300, 50, 40),
    e(1, 'Header', 110, 10, 80, 19, 41, { label: file.source.chatName }),
    e(42, 'Group', 100, 50, 300, 550, 40),
    e(2, 'Edit', 110, 500, 280, 60, 42, { semantic_role: 'composer', value: '' }),
    e(5, 'Group', 110, 500, 280, 60, 2, { label: 'qt_scrollarea_viewport' }),
  ], images: [{ type: 'image', mimeType: 'image/png', data: 'synthetic-png' }],
  screenshot_width: 400, screenshot_height: 600 };
  const fake = fixture(file, sourceState);
  fake.ui.visual = { imageDimensions: () => ({ width: 400, height: 600 }),
    fingerprintRegion: async (_image, region) => ({ sha256: 'f'.repeat(64), region }) };
  fake.ui.ocr = { recognizeImage: async () => ({ coordinateSpace: 'input-png-pixels',
    scaleFactor: 1, width: 400, height: 600, lines: [
      { text: file.source.date, x: 150, y: 95, width: 90, height: 15 },
      { text: file.source.sender, x: 150, y: 116, width: 20, height: 15 },
      { text: file.source.attachment.fileName, x: 150, y: 140, width: 95, height: 18 },
      { text: '12345 B', x: 150, y: 166, width: 70, height: 15 },
      { text: file.source.time, x: 150, y: 188, width: 44, height: 15 },
    ] }) };
  fake.ui.withForwardSourceChat = (_binding, callback) => fake.ui.withClient(api => callback(api,
    { target, window, lineWindows: [window], state: { ...sourceState,
      snapshot_id: 'source-snapshot' }, guard: undefined }));
  const forward = createForwardUi(fake.ui);
  const result = await forward.prepare(file);
  assert.equal(result.stage, 'PREPARED');
  assert.equal(result.evidence.sourceUiProof, 'fresh-file-card-ocr-and-pixel-fingerprint');
  assert.equal(fake.calls.filter(call => call.name === 'right_click').length, 1);
});

test('outgoing file uses localized timestamp and complete chat tail without a visible sender or date', async () => {
  const own = `sender:${'e'.repeat(24)}`;
  const fileSource = { ...binding.source, senderRef: own, contentType: 14, text: null,
    time: '21:23',
    attachment: { fileName: 'line-mcp-test.txt', declaredFileBytes: 68 } };
  const next = { sourceRef: `message:${'f'.repeat(24)}`, senderRef: own,
    date: fileSource.date, time: '21:24', sourceTimestamp: fileSource.sourceTimestamp + 60_000,
    contentType: 1, text: null, attachment: null };
  const file = { ...binding, source: fileSource,
    uiAnchors: { latestSourceWindowComplete: true,
      messages: [{ sourceRef: fileSource.sourceRef, senderRef: own,
        date: fileSource.date, time: fileSource.time,
        sourceTimestamp: fileSource.sourceTimestamp, contentType: 14,
        text: null, attachment: fileSource.attachment }, next] } };
  const e = (index, role, x, y, w, h, parent_index, extra = {}) =>
    ({ element_index: index, role, frame: { x, y, w, h }, parent_index, ...extra });
  const sourceState = { elements: [
    e(0, 'Window', 1100, 150, 740, 1050, undefined),
    e(11, 'Group', 1100, 178, 740, 1022, 0),
    e(12, 'Group', 1100, 182, 740, 52, 11),
    e(14, 'Header', 1168, 198, 49, 19, 12, { label: fileSource.chatName }),
    e(22, 'Group', 1100, 250, 740, 950, 11),
    e(23, 'Custom', 1100, 250, 740, 950, 22),
    e(24, 'Group', 1100, 1080, 740, 120, 23),
    e(31, 'Group', 1100, 1080, 740, 82, 24),
    e(32, 'Edit', 1110, 1086, 722, 76, 31, { value: '' }),
    e(33, 'Group', 1110, 1086, 722, 76, 32, { label: 'qt_scrollarea_viewport' }),
    e(34, 'Group', 1100, 250, 740, 828, 23),
    e(35, 'Group', 1100, 250, 740, 828, 34),
    e(37, 'List', 1100, 306, 740, 772, 35),
    e(43, 'ListItem', 1100, 759, 733, 124, 37),
    e(44, 'ListItem', 1100, 883, 733, 195, 37),
  ], inlineShare: true,
  images: [{ type: 'image', mimeType: 'image/png', data: 'synthetic-detached' }],
  screenshot_width: 740, screenshot_height: 1050 };
  assert.ok(replySourceVisualView(sourceState,
    { ...window, title: fileSource.chatName,
      bounds: { x: 1100, y: 150, width: 740, height: 1050 } },
    { kind: 'detached-title', target },
    { imageDimensions: () => ({ width: 740, height: 1050 }) }));
  const fake = fixture(file, sourceState);
  fake.ui.visual = { imageDimensions: () => ({ width: 740, height: 1050 }),
    fingerprintRegion: async (_image, region, options) => ({ sha256: 'f'.repeat(64), region,
      ...(options?.includeImage ? { image: { type: 'image', mimeType: 'image/png', data: 'crop' } } : {}) }) };
  fake.ui.ocr = { recognizeImage: async image => image.data === 'crop'
    ? { coordinateSpace: 'input-png-pixels', scaleFactor: 1, width: 733, height: 124,
      lines: [{ text: '大 小 : 68Bytes', x: 548, y: 55, width: 125, height: 15 }] }
    : ({ coordinateSpace: 'input-png-pixels',
    scaleFactor: 1, width: 740, height: 1050, lines: [
      { text: fileSource.attachment.fileName, x: 548, y: 630, width: 110, height: 17 },
      { text: '下載期限：9月30日 下午 9:23', x: 548, y: 649, width: 140, height: 14 },
      { text: '大 小 : ö8Bytes', x: 548, y: 665, width: 125, height: 15 },
      { text: '下 午 9 : 23', x: 425, y: 673, width: 49, height: 14 },
      { text: '開啟資料夾 | 分 享 | 傳送至Keep筆記', x: 529, y: 698, width: 184, height: 14,
        words: [{ text: '|', x: 584, y: 698, width: 1, height: 10 },
          { text: '分', x: 591, y: 698, width: 11, height: 11 },
          { text: '享', x: 603, y: 698, width: 11, height: 11 },
          { text: '|', x: 621, y: 698, width: 1, height: 10 }] },
      { text: '下 午 9 : 24', x: 408, y: 867, width: 49, height: 14 },
    ] }) };
  const detached = { ...window, title: fileSource.chatName,
    bounds: { x: 1100, y: 150, width: 740, height: 1050 } };
  const baseClient = fake.ui.withClient;
  fake.ui.withClient = callback => baseClient(api => callback({ ...api,
    call: (name, args) => name === 'list_windows'
      ? Promise.resolve({ windows: [detached] }) : api.call(name, args) }));
  fake.ui.withForwardSourceChat = (_binding, callback) => fake.ui.withClient(api => callback(api,
    { target, window: detached,
      lineWindows: [detached], state: { ...sourceState, snapshot_id: 'source-snapshot' },
      guard: { kind: 'detached-title', chatName: fileSource.chatName,
        target, chatType: 'direct' } }));
  const forward = createForwardUi(fake.ui);
  const result = await forward.prepare(file);
  assert.equal(result.stage, 'PREPARED', JSON.stringify(result));
  assert.equal(result.evidence.sourceUiProof, 'fresh-file-card-ocr-and-pixel-fingerprint');
  assert.equal(result.evidence.shareMenuProof, 'exact-source-row-inline-share');
  assert.equal(fake.calls.filter(call => call.name === 'right_click').length, 0);
});

test('journal callback failure leaves final Share untouched', async () => {
  const fake = fixture();
  const forward = createForwardUi(fake.ui);
  const { session } = await forward.prepare(binding);
  await assert.rejects(forward.dispatch(binding, session, async () => { throw new Error('disk failure'); }),
    /disk failure/u);
  assert.equal(fake.phase, 'selected');
  assert.equal(fake.calls.filter(call => call.name === 'click' && call.phase === 'selected').length, 0);
});

test('cancel targets the prepared dialog with one Escape and proves it closed', async () => {
  const fake = fixture();
  const forward = createForwardUi(fake.ui);
  const { session } = await forward.prepare(binding);
  const canceled = await forward.cancel(binding, session);
  assert.deepEqual(canceled, { closed: true });
  assert.equal(fake.phase, 'source');
  assert.equal(fake.calls.filter(call => call.name === 'press_key').length, 1);
  assert.equal(fake.calls.filter(call => call.name === 'click' && call.phase === 'selected').length, 0);
  await assert.rejects(forward.dispatch(binding, session, async () => {}),
    { code: 'LINE_FORWARD_PREPARED_STALE' });
});

test('cancel refuses a stale or unrelated window without Escape', async () => {
  const fake = fixture();
  const forward = createForwardUi(fake.ui);
  const { session } = await forward.prepare(binding);
  fake.phase = 'source';
  await assert.rejects(forward.cancel(binding, session));
  assert.equal(fake.calls.filter(call => call.name === 'press_key').length, 0);
});

test('source without exact visible time refuses before any input', async () => {
  const fake = fixture();
  fake.ui.withForwardSourceChat = (_binding, callback) => callback({ call: async (name, args) => {
    if (name === 'get_window_state') return { snapshot_id: 'bad', elements: [], images: [] };
    if (name === 'list_windows') return { windows: [window] };
    throw new Error(`unexpected ${name}`);
  } }, { target, window, lineWindows: [window],
    state: { snapshot_id: 'bad', elements: [{ role: 'message', label: binding.source.text,
      contentType: 0, element_index: 1 }], images: [] }, guard: undefined });
  const forward = createForwardUi(fake.ui);
  const result = await forward.prepare(binding);
  assert.equal(result.stage, 'PREPARE_NOT_READY');
  assert.equal(result.currentStage, 'SOURCE_SELECTION_REQUIRED');
  assert.equal(result.evidence.sendDispatched, false);
});

test('Qt selector proves one selected recipient and journals before one foreground Share click', async () => {
  for (const popupAcknowledged of [true, false]) {
  const dialogTarget = { pid: target.pid, window_id: 777 };
  const qtWindow = { ...window, ...dialogTarget,
    bounds: { x: 900, y: 0, width: 352, height: 600 } };
  const menuWindow = { ...window, window_id: 778,
    bounds: { x: 500, y: 200, width: 153, height: 205 } };
  const e = (index, role, x, y, w, h, parent_index, extra = {}) => ({
    element_index: index, role, frame: { x: x + 900, y, w, h }, parent_index, ...extra,
  });
  let phase = 'source';
  let journaled = false;
  const calls = [];
  const sourceState = { snapshot_id: 'source', elements: [{ role: 'message',
    label: binding.source.text, contentType: 0, date: binding.source.date,
    time: binding.source.time, sender: binding.source.sender, element_index: 1 }] };
  const selector = () => {
    const selected = phase === 'selected';
    const searched = phase === 'results' || selected;
    return { snapshot_id: `qt-${phase}`, window_bounds: qtWindow.bounds,
      images: [{ type: 'image', mimeType: 'image/png', data: `qt-${phase}` }],
      elements: [e(0, 'Window', 0, 0, 352, 600),
        e(10, 'Edit', 14, 86, 324, 38, 0,
          { value: searched ? binding.recipient.chatName : '' }),
        ...(searched ? [e(18, 'ListItem', 0, 165, 352, 50, 0, { selected }),
          e(20, 'ListItem', 0, 250, 352, 50, 0, { selected: false })] : []),
        ...(selected ? [e(22, 'Group', 0, 492, 352, 48, 0,
          { label: 'qt_scrollarea_viewport' }),
          e(23, 'Group', 0, 492, 154, 48, 22),
          e(29, 'Group', 85, 550, 88, 30, 0)] : [])] };
  };
  const api = { tools: new Set(['press_key']), call: async (name, args) => {
    calls.push({ name, args, phase });
    if (name === 'list_windows') return { windows: phase === 'source' ? [window]
      : phase === 'menu' && !popupAcknowledged ? [window, menuWindow]
        : phase === 'menu' ? [window] : [window, qtWindow] };
    if (name === 'get_window_state') return args.window_id === dialogTarget.window_id
      ? selector() : args.window_id === menuWindow.window_id
        ? { snapshot_id: 'popup', elements: [{ role: 'menuitem', label: 'Share', element_index: 5 }] }
        : phase === 'menu' && popupAcknowledged
        ? { ...sourceState, snapshot_id: 'menu', elements: [...sourceState.elements,
          { role: 'menuitem', label: 'Share', element_index: 5 }] } : sourceState;
    if (name === 'right_click' && phase === 'source') { phase = 'menu'; return {}; }
    if (name === 'click' && phase === 'menu') {
      phase = 'dialog';
      if (!popupAcknowledged) throw new LineToolError('LINE_UI_ACTION_REFUSED',
        'CUA click: foreground_unavailable: exact target HWND closed after click',
        { operationMayHaveCompleted: true, backendCode: null });
      return {};
    }
    if (name === 'set_value' && phase === 'dialog') { phase = 'results'; return {}; }
    if (name === 'click' && phase === 'results') { phase = 'selected'; return {}; }
    if (name === 'click' && phase === 'selected') {
      assert.equal(journaled, true);
      assert.equal(args.delivery_mode, 'foreground');
      phase = 'sent'; return {};
    }
    throw new Error(`unexpected ${name} in ${phase}`);
  } };
  const ui = { withClient: callback => callback(api),
    readChatIdentity: async ({ chatName }) => ({ chatName,
      chatRef: binding.recipient.chatRef,
      chatIdentity: { kind: binding.recipient.chatType, displayName: chatName,
        guiDisplayNameUnique: true, uiIdentityVerified: false },
      scope: { kind: 'local_gui_chat_identity' }, count: 0, messages: [] }),
    withForwardSourceChat: (_binding, callback) => callback(api,
      { target, window, lineWindows: [window], state: sourceState }),
    visual: { imageDimensions: () => ({ width: 350, height: 598 }) },
    ocr: { recognizeImage: async image => ({ coordinateSpace: 'input-png-pixels',
      scaleFactor: 1, width: 350, height: 598,
      lines: [{ text: '選 擇 傳 送 對 象', x: 125, y: 10, width: 105, height: 18 },
        ...(image.data === 'qt-dialog' ? [] : [
          { text: binding.recipient.chatName, x: 80, y: 180, width: 150, height: 18 },
          { text: binding.recipient.chatName, x: 80, y: 265, width: 150, height: 18 }]),
        ...(image.data === 'qt-selected'
          ? [{ text: '分 享 ( 1 )', x: 116, y: 557, width: 34, height: 18 }] : [])] }) } };
  const forward = createForwardUi(ui);
  const prepared = await forward.prepare(binding);
  assert.equal(prepared.stage, 'PREPARED', JSON.stringify(prepared));
  assert.equal(prepared.session.dialogMode, 'qt');
  assert.equal(calls.filter(call => call.name === 'click' && call.phase === 'selected').length, 0);
  await forward.assertReady(binding, prepared.session);
  await forward.dispatch(binding, prepared.session, async () => { journaled = true; });
  assert.equal(phase, 'sent');
  assert.equal(calls.filter(call => call.name === 'click' && call.phase === 'selected').length, 1);
  }
});
