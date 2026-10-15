import test from 'node:test';
import assert from 'node:assert/strict';

import { createLineExtensions } from '../src/extensions/line-extensions.mjs';
import { boundDirectScope, boundDirectTail, visibleDate, matchBoundDirectRows, prepareBoundDirect } from '../src/extensions/line-bound-direct.mjs';

const NOW = Date.parse('2026-09-24T02:03:00Z');
const chatName = 'Synthetic contact';
const chatRef = `chat:${'a'.repeat(24)}`;
const ownSenderRef = `sender:${'b'.repeat(24)}`;
const peerSenderRef = `sender:${'a'.repeat(24)}`;
const args = { chatName, chatType: 'direct', expectedChatRef: chatRef, expectedOwnSenderRef: ownSenderRef };
const body = result => JSON.parse(result.content[0].text);
const stamp = minute => Date.parse(`2026-09-24T02:${minute}:00Z`);
const message = (text, minute, senderRef, ref) => ({
  contentType: 0, text, sourceRef: `message:${ref.repeat(24)}`,
  sourceTimestamp: stamp(minute), date: '2026-09-24', time: `10:${minute}:00`, senderRef,
});
const local = () => ({ ok: true, chatName, chatRef, ownSenderRef,
  chatIdentity: { kind: 'direct', knownNameUnique: true, guiDisplayNameUnique: false },
  freshness: { snapshotCapturedAt: new Date(NOW).toISOString(), clockOrderValid: true },
  scope: { kind: 'local_database' }, pagination: { limitedBy: null },
  messages: [message('First synthetic text', '01', peerSenderRef, 'c'),
    message('Second synthetic text', '02', ownSenderRef, 'd')],
});
const line = (text, x, y, width = 130, height = 20) => ({ text, x, y, width, height });
const rows = () => [
  { frame: { x: 0, y: 609, width: 733, height: 124 },
    lines: [line('First synthetic text', 60, 650, 160), line('上午10:01', 260, 650, 65)] },
  { frame: { x: 0, y: 733, width: 733, height: 195 },
    lines: [line('Second synthetic text', 530, 770, 175), line('上午10:02', 450, 770, 65)] },
];

test('public prepare validates paired direct refs and sanitizes arbitrary backend errors', async () => {
  let called = 0;
  const extension = createLineExtensions({}, { ui: { async prepareSendTarget() {
    called++; throw new Error('private synthetic diagnostic');
  } } });
  for (const bad of [{ ...args, expectedOwnSenderRef: undefined },
    { ...args, chatType: 'group' }, { ...args, unexpected: true }]) {
    const result = await extension.call('prepare_line_send_target', bad);
    assert.equal(body(result).code, 'LINE_INVALID_ARGUMENT');
  }
  assert.equal(called, 0);
  const result = await extension.call('prepare_line_send_target', args);
  assert.equal(body(result).code, 'LINE_PREPARE_FAILED');
  assert.equal(body(result).sendDispatched, false);
  assert.ok(!JSON.stringify(result).includes('private synthetic diagnostic'));
  assert.equal(called, 1);
});

test('paired public identity check distinguishes global uniqueness from bound UI requirement', async () => {
  let globalNameUnique = true;
  const scopes = [];
  const extension = createLineExtensions({}, { ui: {}, now: () => new Date(NOW),
    boundIdentityReader: async scope => {
      scopes.push(scope);
      return { chatRef, ownSenderRef, chatIdentity: { kind: 'direct', knownNameUnique: true,
        globalNameUnique }, count: 0, messages: [] };
    },
    localIdentityReader: async () => assert.fail('paired check must use bound identity reader'),
  });
  const unique = body(await extension.call('check_line_send_target', args));
  assert.equal(unique.status, 'IDENTITY_UNIQUE');
  assert.equal(unique.guiVerified, false);
  globalNameUnique = false;
  const bound = body(await extension.call('check_line_send_target', args));
  assert.equal(bound.status, 'IDENTITY_BOUND_REQUIRES_UI');
  assert.equal(bound.readOnly, true);
  assert.equal(scopes.length, 2);
  assert.equal(scopes[0].messageLimit, 30);
  assert.equal(scopes[0].mediaMode, 'metadata');
});

test('tail validates current paired identity and unique recent tuples before GUI access', async () => {
  const scope = boundDirectScope(args, NOW);
  const valid = local();
  assert.deepEqual(boundDirectTail(valid, scope, NOW).map(item => item.direction), ['incoming', 'outgoing']);
  for (const [change, code] of [
    [value => { value.chatRef = `chat:${'f'.repeat(24)}`; }, 'CHAT_IDENTITY_CHANGED'],
    [value => { value.ownSenderRef = `sender:${'f'.repeat(24)}`; }, 'CHAT_ACCOUNT_CHANGED'],
    [value => { value.messages.push({ ...value.messages[1], sourceRef: `message:${'e'.repeat(24)}` }); }, 'LINE_BOUND_CONTEXT_UNVERIFIED'],
  ]) {
    const value = local(); change(value);
    assert.throws(() => boundDirectTail(value, scope, NOW), error => error.code === code);
  }
  let uiCalls = 0;
  const ui = { withClient: async () => { uiCalls++; assert.fail('GUI accessed after identity mismatch'); } };
  for (const [changed, code] of [
    [{ chatRef: `chat:${'f'.repeat(24)}` }, 'CHAT_IDENTITY_CHANGED'],
    [{ ownSenderRef: `sender:${'f'.repeat(24)}` }, 'CHAT_ACCOUNT_CHANGED'],
  ]) {
    await assert.rejects(prepareBoundDirect(ui, args, { now: () => NOW,
      readMessages: async () => ({ ...local(), ...changed }) }), error => error.code === code);
  }
  assert.equal(uiCalls, 0);
});

test('visible dates and message rows need matching weekday, direction, minute, and geometry', () => {
  const tail = boundDirectTail(local(), boundDirectScope(args, NOW), NOW);
  const bounds = { x: 0, y: 100, width: 740, height: 834 };
  assert.equal(visibleDate('9月24日（四）', tail[0].date, NOW), '2026-09-24');
  assert.equal(visibleDate('9月24日（三）', tail[0].date, NOW), null);
  assert.equal(visibleDate('9月31日', tail[0].date, NOW), null);
  assert.deepEqual(matchBoundDirectRows(rows(), tail, tail[0].date, bounds).minuteSources,
    ['observed', 'observed']);
  const wrongSide = rows(); wrongSide[1].lines[0].x = 250;
  assert.throws(() => matchBoundDirectRows(wrongSide, tail, tail[0].date, bounds));
  const wrongMinute = rows(); wrongMinute[1].lines[1].text = '上午10:09';
  assert.throws(() => matchBoundDirectRows(wrongMinute, tail, tail[0].date, bounds));
  const clipped = rows(); clipped[1].frame.height = 250;
  assert.throws(() => matchBoundDirectRows(clipped, tail, tail[0].date, bounds));
});

function detachedFixture({ pixelsChanged = false, draft = '', staleWindow = false } = {}) {
  const target = { pid: 321, window_id: 654 };
  const window = { ...target, app_name: 'LINE.exe', title: chatName,
    bounds: { x: 1100, y: 150, width: 740, height: 1050 }, is_on_screen: true, minimized: false };
  const e = (index, role, x, y, w, h, parent_index, extra = {}) =>
    ({ element_index: index, role, frame: { x, y, w, h }, parent_index, ...extra });
  const elements = [
    e(0, 'Window', 1100, 150, 740, 1050, undefined),
    e(11, 'Group', 1100, 178, 740, 1022, 0),
    e(12, 'Group', 1100, 182, 740, 52, 11),
    e(14, 'Header', 1168, 198, 80, 19, 12, { label: chatName }),
    e(22, 'Group', 1100, 250, 740, 950, 11),
    e(23, 'Custom', 1100, 250, 740, 950, 22),
    e(24, 'Group', 1100, 1080, 740, 120, 23),
    e(31, 'Group', 1100, 1080, 740, 82, 24),
    e(32, 'Edit', 1110, 1086, 722, 76, 31, { value: draft }),
    e(33, 'Group', 1110, 1086, 722, 76, 32, { label: 'qt_scrollarea_viewport' }),
    e(34, 'Group', 1100, 250, 740, 828, 23),
    e(35, 'Group', 1100, 250, 740, 828, 34),
    e(37, 'List', 1100, 306, 740, 772, 35),
    e(40, 'ListItem', 1100, 700, 733, 40, 37),
    e(43, 'ListItem', 1100, 759, 733, 124, 37),
    e(44, 'ListItem', 1100, 883, 733, 195, 37),
  ];
  const lines = [line('9月24日（四）', 325, 565, 90, 20),
    ...rows().flatMap(row => row.lines)];
  let snapshots = 0;
  const calls = [];
  const api = { call: async (name, options) => {
    calls.push({ name, options });
    if (name === 'list_windows') return { windows: [{ ...window,
      bounds: staleWindow && snapshots > 0 ? { ...window.bounds, x: 1101 } : window.bounds }] };
    if (name === 'get_window_state') {
      snapshots++;
      return { snapshot_id: `synthetic-${snapshots}`, elements,
        images: [{ type: 'image', mimeType: 'image/png', data: pixelsChanged && snapshots > 1 ? 'changed' : 'same' }],
        screenshot_width: 740, screenshot_height: 1050 };
    }
    assert.fail(`Unexpected input operation: ${name}`);
  } };
  const ui = { withClient: callback => callback(api),
    visual: { imageDimensions: () => ({ width: 740, height: 1050 }),
      fingerprintRegion: async (image, region) => ({ region, sha256: image.data === 'same' ? 'a'.repeat(64) : 'b'.repeat(64) }) },
    ocr: { recognizeImage: async () => ({ coordinateSpace: 'input-png-pixels', scaleFactor: 1,
      width: 740, height: 1050, lines }) },
  };
  return { ui, calls, get snapshots() { return snapshots; } };
}

test('synthetic detached prepare uses two screenshot observations and leaves input untouched', async t => {
  t.mock.method(Date, 'now', () => NOW);
  const fake = detachedFixture();
  let reads = 0;
  const result = await prepareBoundDirect(fake.ui, args,
    { now: () => NOW, readMessages: async () => { reads++; return local(); } });
  assert.equal(result.status, 'READY');
  assert.equal(result.evidence.proofKind, 'bound-direct-recent-context');
  assert.deepEqual(result.evidence.minuteSources, ['observed', 'observed']);
  assert.equal(result.draftEmpty, true);
  assert.equal(result.sendDispatched, false);
  assert.equal(reads, 2);
  assert.equal(fake.snapshots, 2);
  assert.deepEqual(fake.calls.filter(call => call.name === 'get_window_state')
    .map(call => call.options.include_screenshot), [true, true]);
  assert.ok(fake.calls.every(call => ['list_windows', 'get_window_state'].includes(call.name)));
});

test('synthetic detached prepare refuses changed pixels, window geometry, and a filled composer', async t => {
  t.mock.method(Date, 'now', () => NOW);
  for (const [options, code] of [
    [{ pixelsChanged: true }, 'LINE_BOUND_CONTEXT_CHANGED'],
    [{ staleWindow: true }, 'LINE_CHAT_STALE'],
    [{ draft: 'unsent synthetic draft' }, 'LINE_DRAFT_CONFLICT'],
  ]) {
    const fake = detachedFixture(options);
    await assert.rejects(prepareBoundDirect(fake.ui, args,
      { now: () => NOW, readMessages: async () => local() }), error => error.code === code);
    assert.ok(fake.calls.every(call => ['list_windows', 'get_window_state'].includes(call.name)));
  }
});
