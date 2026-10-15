import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';

import { openExactChat } from '../src/extensions/line-plain-send.mjs';
import { searchDirectCandidate as searchDirect, selectDirectCandidate as selectDirect } from '../src/extensions/line-direct-navigation.mjs';
import { searchDirectCandidate as searchGroup, selectDirectCandidate as selectGroup } from '../src/extensions/line-group-navigation.mjs';

const [main, aux] = JSON.parse(fs.readFileSync(
  new URL('./fixtures/live-window-structure-20260928.json', import.meta.url), 'utf8'));
const minimizedMain = JSON.parse(fs.readFileSync(
  new URL('./fixtures/minimized-window-structure-20260928.json', import.meta.url), 'utf8'))[0];
const target = { pid:main.window.pid, window_id:main.window.window_id, title:main.window.title };
const switched = { window:{ ...main.window, window_id:main.window.window_id + 10 },
  elements:main.elements };

function fixture({ activationSucceeds = false, switchWindow = false,
  minimized = false, restoreVisible = true } = {}) {
  let activated = false;
  const calls = [], activations = [];
  const initial = minimized ? minimizedMain.window : main.window;
  const api = { async call(name, args) {
    calls.push({ name, args });
    if (name === 'list_windows') return { windows:[aux.window,
      activated && switchWindow ? switched.window
        : activated && restoreVisible ? main.window : initial] };
    if (name === 'get_window_state') return args.window_id === aux.window.window_id
      ? { elements:aux.elements }
      : minimized && (!activated || !restoreVisible)
        ? { elements:minimizedMain.elements, ...minimizedMain.stateMeta }
        : { elements:main.elements };
    assert.fail(`input before target verification: ${name}`);
  } };
  const automation = { async activateLine(value) {
    activations.push(value);
    activated = true;
    return { success:activationSucceeds };
  } };
  return { api, automation, ui:{ automation }, calls, activations };
}

test('plain, direct and group navigation activate the exact proven HWND/PID/title', async () => {
  for (const run of [
    value => openExactChat(value.ui, value.api, 'Synthetic chat', 'direct', () => {}),
    value => searchDirect(value.api, value.automation, 'Synthetic chat'),
    value => searchGroup(value.api, value.automation, 'Synthetic group'),
    value => selectDirect(value.api, value.automation, { target:{ pid:target.pid,
      window_id:target.window_id }, window:main.window }),
    value => selectGroup(value.api, value.automation, { target:{ pid:target.pid,
      window_id:target.window_id }, window:main.window }),
  ]) {
    const value = fixture();
    await assert.rejects(run(value), error => error?.code === 'LINE_FOCUS_UNAVAILABLE');
    assert.deepEqual(value.activations, [target]);
    assert.ok(value.calls.every(call => ['list_windows', 'get_window_state'].includes(call.name)));
  }
});

test('a changed main HWND after activation stops before any search or composer input', async () => {
  for (const run of [
    value => openExactChat(value.ui, value.api, 'Synthetic chat', 'direct', () => {}),
    value => searchDirect(value.api, value.automation, 'Synthetic chat'),
    value => searchGroup(value.api, value.automation, 'Synthetic group'),
  ]) {
    const value = fixture({ activationSucceeds:true, switchWindow:true });
    await assert.rejects(run(value), error =>
      ['LINE_TARGET_CHANGED', 'LINE_DIRECT_WINDOW_CHANGED', 'LINE_GROUP_WINDOW_CHANGED'].includes(error?.code));
    assert.deepEqual(value.activations, [target]);
    assert.ok(value.calls.every(call => ['list_windows', 'get_window_state'].includes(call.name)));
  }
});

test('a structurally proven minimized main is activated by exact target and reobserved', async () => {
  for (const run of [
    value => openExactChat(value.ui, value.api, 'Synthetic chat', 'direct', () => {}),
    value => searchDirect(value.api, value.automation, 'Synthetic chat'),
    value => searchGroup(value.api, value.automation, 'Synthetic group'),
  ]) {
    const value = fixture({ activationSucceeds:true, minimized:true });
    await assert.rejects(run(value)); // No synthetic screenshot/OCR is supplied.
    assert.deepEqual(value.activations, [target]);
    assert.ok(value.calls.some(call => call.name === 'get_window_state'
      && call.args.include_screenshot === true), 'navigation reached a fresh visible snapshot');
    assert.ok(value.calls.every(call => ['list_windows', 'get_window_state'].includes(call.name)));
  }
});

test('a minimized target still minimized after activation refuses before input', async () => {
  for (const run of [
    value => openExactChat(value.ui, value.api, 'Synthetic chat', 'direct', () => {}),
    value => searchDirect(value.api, value.automation, 'Synthetic chat'),
    value => searchGroup(value.api, value.automation, 'Synthetic group'),
  ]) {
    const value = fixture({ activationSucceeds:true, minimized:true, restoreVisible:false });
    await assert.rejects(run(value), error =>
      ['LINE_TARGET_CHANGED', 'LINE_DIRECT_WINDOW_CHANGED', 'LINE_GROUP_WINDOW_CHANGED'].includes(error?.code));
    assert.deepEqual(value.activations, [target]);
    assert.ok(value.calls.every(call => ['list_windows', 'get_window_state'].includes(call.name)));
    assert.ok(value.calls.every(call => call.args?.include_screenshot !== true));
  }
});
