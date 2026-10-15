import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import test from 'node:test';
import { WindowsLineAutomation, configuredAutoHotkeyPath } from '../src/automation/windows-line-automation.js';

test('AutoHotkey resolution never searches cwd or PATH and rejects invalid explicit overrides', () => {
  assert.equal(configuredAutoHotkeyPath('autohotkey', 'C:/Program Files'), null);
  assert.equal(configuredAutoHotkeyPath('./AutoHotkey.exe', 'C:/Program Files'), null);
  assert.equal(configuredAutoHotkeyPath(''), null);
  assert.equal(configuredAutoHotkeyPath('C:/Tools/ahk.cmd'), null);
  assert.equal(configuredAutoHotkeyPath(' C:/Tools/ahk.exe'), null);
  if (process.platform === 'win32') {
    assert.equal(configuredAutoHotkeyPath(undefined, 'C:/Program Files'), path.join('C:/Program Files', 'AutoHotkey', 'v2', 'AutoHotkey64.exe'));
    assert.equal(configuredAutoHotkeyPath('C:/Chosen/ahk.exe', 'C:/Program Files'), 'C:/Chosen/ahk.exe');
  }
});

test('AHK execution uses an absolute executable, literal argv and distinct owned temporary scripts', {
  skip: process.platform !== 'win32',
}, async () => {
  const automation = new WindowsLineAutomation();
  automation.ahkPath = process.execPath; // A real file, never launched by this harness.
  const observed = [];
  automation.executeFile = async (executable, args, options) => {
    assert.equal(executable, process.execPath);
    assert.equal(args.length, 1);
    assert.equal(options.shell, false);
    assert.equal(options.windowsHide, true);
    const directory = path.dirname(args[0]);
    assert.equal(path.dirname(directory), os.tmpdir());
    assert.match(path.basename(directory), /^line-mcp-ahk-/);
    assert.match(await fs.readFile(args[0], 'utf8'), /FileEncoding "UTF-8-RAW"/);
    observed.push(args[0]);
    return { stdout: Buffer.from('synthetic result'), stderr: Buffer.alloc(0) };
  };
  assert.deepEqual(await Promise.all([automation.runAhk('; first'), automation.runAhk('; second')]), ['synthetic result', 'synthetic result']);
  assert.equal(new Set(observed).size, 2);
  for (const file of observed) await assert.rejects(fs.stat(path.dirname(file)), { code: 'ENOENT' });
});

test('AHK failure removes only its owned script and does not expose exception details', {
  skip: process.platform !== 'win32',
}, async () => {
  const automation = new WindowsLineAutomation();
  automation.ahkPath = process.execPath;
  let ownedPath;
  automation.executeFile = async (_, args) => {
    ownedPath = args[0];
    throw new Error('synthetic-private-detail');
  };
  await assert.rejects(automation.runAhk('; failure'), error => !error.message.includes('synthetic-private-detail'));
  await assert.rejects(fs.stat(path.dirname(ownedPath)), { code: 'ENOENT' });
});
