import assert from 'node:assert/strict';
import { mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import test from 'node:test';

import { LineAutomation } from '../src/automation/line-automation.js';
import { withLineOperation } from '../src/automation/line-operation-lock.mjs';
import { createLineExtensions } from '../src/extensions/line-extensions.mjs';

function fakeWindowsFacade() {
  const instance = Object.create(LineAutomation.prototype);
  instance.platform = 'win32';
  instance.runOperation = async (_kind, action) => action();
  const calls = [];
  instance.automation = {
    switchToEnglish: async () => {
      calls.push('switch');
    },
    activateLine: async () => {
      calls.push('activate');
      return { success: true };
    },
    selectChat: async chatName => {
      calls.push(['select', chatName]);
      return true;
    },
    pageUp: async times => {
      calls.push(['page-up', times]);
    },
    copyAllChatToClipboard: async () => {
      calls.push('copy');
      return 'fixture chat history';
    },
    sendMessage: async (chatName, message, autoSend) => {
      calls.push(['send', chatName, message, autoSend]);
      return { success: true, error: null };
    },
    stageFileManual: async filePath => {
      calls.push(['stage-file', filePath]);
      return { success: true, error: null };
    },
  };
  return { instance, calls };
}

test('Windows facade delegates chat-scoped work to one verified UI without altering literal text', async () => {
  const { instance, calls } = fakeWindowsFacade();
  const message = '@All stays literal\nsecond line';
  const verifiedUi = {
    async readLegacyHistory(args) {
      calls.push(['ui-history', args]);
      return 'fixture chat history';
    },
    async sendText(args) {
      calls.push(['ui-send', args]);
      return { success: true, verification: { chat: 'exact-header' } };
    },
    async stageFile(args) {
      calls.push(['ui-file', args]);
      return { success: true, staged: true };
    },
  };
  instance._verifiedUi = verifiedUi;

  assert.equal(instance.getVerifiedUi(), verifiedUi);
  assert.equal(instance.getVerifiedUi(), verifiedUi);
  assert.equal(await instance.getChatHistory('sample group', '2026-09-12', 25, 5), 'fixture chat history');
  assert.deepEqual(await instance.sendChatMessage('sample group', message, true), { success: true, error: null });
  assert.deepEqual(await instance.stageFileManual(
    'sample group',
    'C:\\temporary\\file.txt',
    'attachment note',
  ), { success: true, error: null });

  assert.deepEqual(calls, [
    ['ui-history', { chatName: 'sample group', pageUpTimes: 5 }],
    ['ui-send', { chatName: 'sample group', message, autoSend: true }],
    ['ui-file', { chatName: 'sample group', filePath: 'C:\\temporary\\file.txt', optionalMessage: 'attachment note' }],
  ]);
});

test('Windows facade never falls through to legacy chat helpers when verified UI refuses', async () => {
  const { instance, calls } = fakeWindowsFacade();
  const refusal = Object.assign(new Error('CUA or exact header unavailable'), {
    code: 'LINE_UI_BACKEND_UNAVAILABLE',
    operationMayHaveCompleted: false,
  });
  instance._verifiedUi = {
    readLegacyHistory: async () => { throw refusal; },
    sendText: async () => { throw refusal; },
    stageFile: async () => { throw refusal; },
  };

  for (const action of [
    () => instance.getChatHistory('sample group'),
    () => instance.sendChatMessage('sample group', 'not sent', true),
    () => instance.stageFileManual('sample group', 'C:\\temporary\\file.txt'),
  ]) await assert.rejects(action(), error => error === refusal);

  assert.deepEqual(calls, []);
});

test('Windows facade treats empty or error verified history output as a failed history read', async () => {
  for (const value of [null, '', ' ', 'ERROR: Clipboard is empty']) {
    const { instance } = fakeWindowsFacade();
    instance._verifiedUi = { readLegacyHistory: async () => value };
    await assert.rejects(
      instance.getChatHistory('sample group'),
      error => error?.code === 'HISTORY_READ_FAILED' && /Do not treat this as empty history/i.test(error.message),
    );
  }
});

test('Windows facade shares one operation lock across send, history, and file flows without replaying work', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'line-stage-workflow-'));
  const lockPath = path.join(directory, 'operation.lock');
  t.after(() => rm(directory, { recursive: true, force: true }));

  const { instance } = fakeWindowsFacade();
  instance.runOperation = (kind, action) => withLineOperation(kind, action, { lockPath });

  let release;
  let starts = 0;
  let startedResolve;
  const started = new Promise(resolve => {
    startedResolve = resolve;
  });
  instance._verifiedUi = {
    sendText: () => instance.runOperation('ui-send-text', async () => {
      starts += 1;
      startedResolve();
      await new Promise(resolve => {
        release = resolve;
      });
      return { success: true };
    }),
    readLegacyHistory: () => instance.runOperation('ui-read-legacy-history', async () => 'history'),
    stageFile: () => instance.runOperation('ui-stage-file', async () => ({ success: true })),
  };

  const running = instance.sendChatMessage('sample group', 'draft only');
  await started;
  await assert.rejects(instance.getChatHistory('sample group'), { code: 'LINE_BUSY' });
  await assert.rejects(instance.stageFileManual('sample group', 'C:\\temporary\\file.txt'), { code: 'LINE_BUSY' });
  release();

  assert.deepEqual(await running, { success: true, error: null });
  assert.equal(starts, 1);
});

test('macOS chat-scoped facade methods fail closed before any backend method', async () => {
  const { instance, calls } = fakeWindowsFacade();
  instance.platform = 'darwin';
  instance._verifiedUi = {
    readLegacyHistory: async () => calls.push('ui-history'),
    sendText: async () => calls.push('ui-send'),
    stageFile: async () => calls.push('ui-file'),
  };

  for (const action of [
    () => instance.getChatHistory('sample group'),
    () => instance.sendChatMessage('sample group', 'not sent', true),
    () => instance.stageFileManual('sample group', '/tmp/file.txt'),
  ]) {
    await assert.rejects(action(), error => error?.code === 'LINE_CHAT_VERIFICATION_UNAVAILABLE'
      && error?.operationMayHaveCompleted === false);
  }
  assert.deepEqual(calls, []);
});

test('extension construction reuses the facade verified UI without starting it', async () => {
  const { instance } = fakeWindowsFacade();
  const calls = [];
  const verifiedUi = {
    sendText: async args => {
      calls.push(args);
      return { success: true };
    },
  };
  instance.getVerifiedUi = () => {
    calls.push('get-ui');
    return verifiedUi;
  };

  const extension = createLineExtensions(instance, { now: () => new Date('2026-09-12T00:00:00Z') });
  assert.deepEqual(calls, ['get-ui']);
  const result = await extension.call('send_message_manual', {
    chatName: 'sample group',
    message: 'literal draft',
  });
  assert.equal(result.isError, undefined);
  assert.deepEqual(calls, [
    'get-ui',
    { chatName: 'sample group', message: 'literal draft', autoSend: false },
  ]);
});

test('lock ownership loss after a completed workflow reports uncertainty and preserves the foreign lock', async t => {
  const directory = await mkdtemp(path.join(os.tmpdir(), 'line-stage-lock-'));
  const lockPath = path.join(directory, 'operation.lock');
  t.after(() => rm(directory, { recursive: true, force: true }));

  await assert.rejects(
    withLineOperation('stage-file', async () => ({ staged: true }), {
      lockPath,
      testHooks: {
        beforeRelease: async () => {
          await writeFile(lockPath, 'foreign replacement', 'utf8');
        },
      },
    }),
    error => error?.code === 'LINE_LOCK_CLEANUP_FAILED'
      && error?.operationMayHaveCompleted === true
      && error?.reason === 'LINE_LOCK_OWNERSHIP_LOST',
  );
  assert.equal(await readFile(lockPath, 'utf8'), 'foreign replacement');
});
