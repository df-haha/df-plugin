import assert from 'node:assert/strict';
import fs from 'node:fs';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import { WindowsLineAutomation } from '../src/automation/windows-line-automation.js';

const WINDOWS_AUTOMATION_SOURCE = fs.readFileSync(
  fileURLToPath(new URL('../src/automation/windows-line-automation.js', import.meta.url)),
  'utf8',
);

function buildOutputDecoder(chardet, iconv) {
  const start = WINDOWS_AUTOMATION_SOURCE.indexOf('function decodeAhkOutput(');
  const end = WINDOWS_AUTOMATION_SOURCE.indexOf('\nconst HISTORY_AHK_GUARDS', start);
  assert.notEqual(start, -1, 'decodeAhkOutput must remain present');
  assert.notEqual(end, -1, 'the AHK decoder must precede the guard prelude');
  return new Function(
    'Buffer',
    'TextDecoder',
    'chardet',
    'iconv',
    `${WINDOWS_AUTOMATION_SOURCE.slice(start, end)}\nreturn decodeAhkOutput;`,
  )(Buffer, TextDecoder, chardet, iconv);
}

function sendHarness(overrides = {}) {
  const automation = new WindowsLineAutomation();
  const calls = [];
  const success = { success: true };
  Object.assign(automation, {
    _sendSingleMessageInit: async chatName => {
      calls.push(['init', chatName]);
      return success;
    },
    _sendSingleMessage: async (chatName, text) => {
      calls.push(['text', chatName, text]);
      return success;
    },
    _sendShiftEnter: async () => {
      calls.push(['newline']);
      return success;
    },
    _sendSingleMessageEnter: async () => {
      calls.push(['enter']);
      return success;
    },
    ...overrides,
  });
  return { automation, calls };
}

test('Windows sendMessage keeps @ text literal and preserves requested multiline boundaries', async () => {
  const { automation, calls } = sendHarness();

  const result = await automation.sendMessage(
    'sample group',
    'first\n@Alicia please review\n\nlast',
    true,
  );

  assert.deepEqual(result, { success: true, error: null });
  assert.deepEqual(calls, [
    ['init', 'sample group'],
    ['text', 'sample group', 'first'],
    ['newline'],
    ['text', 'sample group', '@Alicia please review'],
    ['newline'],
    ['newline'],
    ['text', 'sample group', 'last'],
    ['enter'],
  ]);
});

test('Windows sendMessage stops before newline, later text, and Enter when a helper fails', async () => {
  const failure = {
    success: false,
    error: 'LINE_PASTE_FAILED',
    code: 'LINE_PASTE_FAILED',
  };
  const { automation, calls } = sendHarness({
    _sendSingleMessage: async (chatName, text) => {
      calls.push(['text', chatName, text]);
      return failure;
    },
  });

  const result = await automation.sendMessage('sample group', 'first\nsecond', true);

  assert.deepEqual(result, failure);
  assert.deepEqual(calls, [
    ['init', 'sample group'],
    ['text', 'sample group', 'first'],
  ]);
});

test('Windows AHK output decoding preserves valid UTF-8 before consulting a legacy charset', () => {
  let detectCalls = 0;
  const strictFirst = buildOutputDecoder({
    detect: () => {
      detectCalls += 1;
      throw new Error('valid UTF-8 must not need charset detection');
    },
  }, {});

  assert.equal(strictFirst(Buffer.from('繁體中文 @ ✓', 'utf8'), 'stdout'), '繁體中文 @ ✓');
  assert.equal(detectCalls, 0);

  let fallbackBytes;
  const fallback = buildOutputDecoder({
    detect: bytes => {
      fallbackBytes = Buffer.from(bytes);
      return 'windows-1252';
    },
  }, {
    decode: (bytes, encoding) => `${encoding}:${Buffer.from(bytes).toString('hex')}`,
  });
  assert.equal(fallback(Buffer.from([0xff, 0xfe]), 'stderr'), 'windows-1252:fffe');
  assert.deepEqual([...fallbackBytes], [0xff, 0xfe]);
  assert.match(WINDOWS_AUTOMATION_SOURCE, /FileEncoding "UTF-8-RAW"/);
});

test('generated Windows send scripts bind input to an exact focused LINE HWND and guarded coordinates', async () => {
  const automation = new WindowsLineAutomation();
  const scripts = [];
  automation.runAhk = async script => {
    scripts.push(script);
    return '';
  };

  await automation._sendSingleMessageInit('sample group');
  await automation._sendSingleMessage('sample group', 'plain text');
  await automation._sendShiftEnter();
  await automation._sendSingleMessageEnter();
  const script = scripts.join('\n');

  for (const fragment of [
    'WinGetList("ahk_exe LINE.exe")',
    'RegExMatch(WinGetClass("ahk_id " hwnd), "^Qt\\d+QWindowIcon$")',
    'WinGetPID("ahk_id " hwnd)',
    'if !WinWaitActive("ahk_id " target.hwnd,, 2)',
    'active != target.hwnd',
    'WinGetPID("ahk_id " active) != target.pid',
    'ReadCurrentLineBounds(target)',
    'if !SameBounds(point.bounds, currentBounds)',
    'DllCall("WindowFromPoint"',
    'DllCall("GetAncestor", "Ptr", hit, "UInt", 2, "Ptr") != target.hwnd',
  ]) {
    assert.ok(script.includes(fragment), `missing AHK guard: ${fragment}`);
  }
  assert.match(script, /GuardedLineClick\(target, ComposerPoint\(target\)\)/);
  assert.match(script, /GuardedLineSend\(target, "\^a"\)/);
  assert.match(script, /GuardedLineSend\(target, "\{Delete\}"\)/);
  assert.match(script, /GuardedLineSend\(target, "\^v"\)/);
  assert.match(script, /GuardedLineSend\(target, "\+\{Enter\}"\)/);
  assert.match(script, /GuardedLineSend\(target, "\{Enter\}"\)/);
  assert.ok(
    script.indexOf('if (!hit || DllCall("GetAncestor"')
      < script.indexOf('Click point.x, point.y'),
    'the hit-test guard must occur before the only direct Click primitive',
  );
});

test('history copy snapshots and restores the prior ClipboardAll object before returning copied text', async () => {
  const automation = new WindowsLineAutomation();
  let script;
  automation.runAhk = async value => {
    script = value;
    return 'verified history text';
  };

  assert.equal(await automation.copyAllChatToClipboard(), 'verified history text');
  for (const fragment of [
    'savedClipboard := ClipboardAll()',
    'OnExit RestoreClipboardOnExit',
    'A_Clipboard := ""',
    'copyOwnerPid := ClipboardOwnerProcessId()',
    'chatHistory := A_Clipboard',
    'ownedClipboardSequence := copySequence',
    'A_Clipboard := savedClipboard',
    'savedClipboard := ""',
    'FileAppend chatHistory, "*"',
  ]) assert.ok(script.includes(fragment), `missing clipboard transaction fragment: ${fragment}`);

  assert.ok(script.indexOf('savedClipboard := ClipboardAll()') < script.indexOf('A_Clipboard := ""'));
  assert.ok(script.indexOf('copyOwnerPid := ClipboardOwnerProcessId()') < script.indexOf('chatHistory := A_Clipboard'));
  assert.ok(script.indexOf('chatHistory := A_Clipboard') < script.indexOf('ownedClipboardSequence := copySequence'));
  assert.ok(script.indexOf('restoreStatus := RestoreOwnedClipboard()') < script.indexOf('FileAppend chatHistory, "*"'));
  assert.doesNotMatch(script, /FileAppend A_Clipboard, "\*"/);
});

test('history copy registers non-cancelling restoration before guarded copy can exit', async () => {
  const automation = new WindowsLineAutomation();
  const guarded = Object.assign(new Error('guarded copy stopped'), { code: 'LINE_FOCUS_CHANGED' });
  let script;
  automation.runAhk = async value => {
    script = value;
    throw guarded;
  };

  await assert.rejects(automation.copyAllChatToClipboard(), error => error === guarded);
  assert.ok(script.indexOf('OnExit RestoreClipboardOnExit') < script.indexOf('A_Clipboard := ""'));
  assert.ok(script.indexOf('OnExit RestoreClipboardOnExit') < script.indexOf('GuardedLineSend(target, "^c")'));
  assert.match(script, /RestoreClipboardOnExit\(\*\)\s*\{\s*RestoreOwnedClipboard\(\)\s*\}/u);
});

test('history copy accepts only LINE-owned clipboard text and refuses any newer foreign sequence', async () => {
  const automation = new WindowsLineAutomation();
  let script;
  automation.runAhk = async value => {
    script = value;
    return 'verified history text';
  };
  await automation.copyAllChatToClipboard();

  for (const fragment of [
    'DllCall("User32.dll\\GetClipboardSequenceNumber", "UInt")',
    'DllCall("User32.dll\\GetClipboardOwner", "Ptr")',
    'DllCall("User32.dll\\GetWindowThreadProcessId", "Ptr", ownerHwnd, "UInt*", &ownerPid, "UInt")',
    'ownedClipboardSequence := ClipboardSequenceNumber()',
    'copySequence := ClipboardSequenceNumber()',
    'copyOwnerPid := ClipboardOwnerProcessId()',
    'copyOwnerPid != target.pid',
    'LINE_CLIPBOARD_SOURCE_UNVERIFIED',
    'ClipboardSequenceNumber() != copySequence',
    'ClipboardOwnerProcessId() != target.pid',
    'LINE_CLIPBOARD_SOURCE_CHANGED',
    'currentSequence != ownedClipboardSequence',
  ]) assert.ok(script.includes(fragment), `missing clipboard ownership fragment: ${fragment}`);
  assert.match(script, /if \(currentSequence != ownedClipboardSequence\) \{\s*clipboardMutated := false\s*savedClipboard := ""\s*return 0\s*\}/u);
  assert.match(script, /restoreStatus := RestoreOwnedClipboard\(\)\s*if \(restoreStatus = 0\)\s*LINE_GUARD_FAIL\("LINE_CLIPBOARD_SOURCE_CHANGED"\)/u);
  assert.ok(script.indexOf('copyOwnerPid := ClipboardOwnerProcessId()') < script.indexOf('chatHistory := A_Clipboard'));
  assert.ok(script.indexOf('chatHistory := A_Clipboard') < script.indexOf('ClipboardSequenceNumber() != copySequence'));
  assert.ok(script.indexOf('ClipboardSequenceNumber() != copySequence') < script.indexOf('ownedClipboardSequence := copySequence'));
  assert.ok(script.indexOf('currentSequence != ownedClipboardSequence') < script.indexOf('A_Clipboard := savedClipboard'));
});

test('file staging uses an exact LINE-owned native dialog, verifies the selected path, and never confirms Open', async () => {
  const automation = new WindowsLineAutomation();
  const scripts = [];
  automation.runAhk = async script => {
    scripts.push(script);
    return 'READY';
  };
  const filePath = String.raw`C:\temporary\line-test-file.txt`;

  assert.deepEqual(await automation.stageFileManual(filePath), { success: true, error: null });
  assert.equal(scripts.length, 1);
  const script = scripts[0];

  for (const fragment of [
    'target := AcquireExactLineTarget()',
    'GuardedLineSend(target, "^o")',
    'dialog := AcquireExactLineOpenDialog(target)',
    `SetExactLineFileName(target, dialog, "${filePath}")`,
    'WinGetPID("ahk_id " active) != target.pid',
    'WinGetClass("ahk_id " active) != "#32770"',
    'ControlSetText filePath, "Edit1", "ahk_id " dialog.hwnd',
    'ControlGetText("Edit1", "ahk_id " dialog.hwnd)',
    'if (actualPath != filePath)',
    'LINE_FILE_DIALOG_FILENAME_MISMATCH',
    'FileAppend "READY", "*"',
  ]) {
    assert.ok(script.includes(fragment), `missing guarded staging fragment: ${fragment}`);
  }
  assert.doesNotMatch(script, /ControlClick\b/);
  assert.doesNotMatch(script, /GuardedLineSend\(target, "\{Enter\}"\)/);
  assert.doesNotMatch(script, /A_Clipboard/);
});
