import assert from 'node:assert/strict';
import test from 'node:test';

import { WindowsLineAutomation } from '../src/automation/windows-line-automation.js';
import { LineAutomation } from '../src/automation/line-automation.js';

test('Windows activation binds exact HWND, PID and escaped title before focus', async () => {
  const automation = new WindowsLineAutomation();
  const scripts = [];
  automation.runAhk = async script => { scripts.push(script); return ''; };
  const target = { window_id: 8765, pid: 444, title: 'Fixture `"room' };

  assert.deepEqual(await automation.activateLine(target), { success: true });
  assert.equal(scripts.length, 1);
  assert.ok(scripts[0].includes(
    `AcquireExactLineTarget(8765, "${automation.escapeAhkString(target.title)}", 444)`,
  ));
  assert.ok(scripts[0].includes('if (expectedPid && pid != expectedPid)'));
  assert.ok(scripts[0].indexOf('if (expectedPid && pid != expectedPid)')
    < scripts[0].indexOf('WinActivate "ahk_id " target.hwnd'));
  assert.ok(scripts[0].includes('if (expectedHwnd && WinGetMinMax("ahk_id " target.hwnd) = -1)'));
  assert.ok(scripts[0].indexOf('if (expectedPid && pid != expectedPid)')
    < scripts[0].indexOf('WinRestore "ahk_id " target.hwnd'));
  assert.ok(scripts[0].indexOf('WinRestore "ahk_id " target.hwnd')
    < scripts[0].indexOf('WinActivate "ahk_id " target.hwnd'));

  assert.deepEqual(await automation.activateLine(), { success: true });
  assert.ok(scripts[1].includes('AcquireExactLineTarget()'));
});

test('malformed activation targets never reach AutoHotkey', async () => {
  const automation = new WindowsLineAutomation();
  let calls = 0;
  automation.runAhk = async () => { calls += 1; return ''; };
  for (const target of [
    null, {}, { window_id: 0, pid: 444, title: 'Room' },
    { window_id: 8765, pid: -1, title: 'Room' },
    { window_id: 8765, pid: 444, title: '' },
    { window_id: 8765, pid: 444, title: 'Room\nOther' },
  ]) {
    assert.deepEqual(await automation.activateLine(target), {
      success: false, code: 'LINE_TARGET_INVALID', error: 'A verified LINE window is required.',
    });
  }
  assert.equal(calls, 0);
});

test('facade forwards the optional verified target unchanged', async () => {
  const facade = Object.create(LineAutomation.prototype);
  const targets = [];
  facade.automation = { activateLine: async target => {
    targets.push(target);
    return { success: true };
  } };
  const target = { window_id: 8765, pid: 444, title: 'Fixture room' };
  assert.deepEqual(await facade.activateLine(target), { success: true });
  assert.deepEqual(targets, [target]);
  assert.deepEqual(await facade.activateLine(), { success: true });
  assert.equal(targets[1], undefined);
});

test('file staging binds the supplied window before activation and never sends', async () => {
  const automation = new WindowsLineAutomation();
  let script;
  automation.runAhk = async value => { script = value; return 'READY'; };
  const target = { window_id: 8765, pid: 444, title: 'Fixture `"room' };
  assert.equal((await automation.stageFileManual('C:\\fixture.txt', target)).success, true);
  assert.ok(script.includes(`AcquireExactLineTarget(8765, "${automation.escapeAhkString(target.title)}", 444)`));
  assert.ok(script.indexOf('if (expectedPid && pid != expectedPid)') < script.indexOf('WinActivate "ahk_id " target.hwnd'));
  assert.ok(script.includes('GuardedLineSend(target, "^o")'));
  assert.doesNotMatch(script, /ControlClick|GuardedLineSend\(target, "\{Enter\}"\)/u);
});

test('file staging refuses malformed explicit targets before AutoHotkey', async () => {
  const automation = new WindowsLineAutomation();
  let calls = 0;
  automation.runAhk = async () => { calls += 1; return 'READY'; };
  for (const target of [{}, { window_id: 1, pid: 0, title: 'Room' },
    { window_id: 1, pid: 2, title: 'Room\nOther' }]) {
    assert.equal((await automation.stageFileManual('C:\\fixture.txt', target)).code, 'LINE_TARGET_INVALID');
  }
  assert.equal(calls, 0);
});
