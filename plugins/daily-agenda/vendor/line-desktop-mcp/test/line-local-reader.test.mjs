import test from 'node:test';
import assert from 'node:assert/strict';
import { createHash } from 'node:crypto';
import { spawn as spawnChild } from 'node:child_process';
import { EventEmitter } from 'node:events';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { PassThrough } from 'node:stream';
import { fileURLToPath } from 'node:url';
import { readLocalLineMessages, readLocalLineChatIdentity, readLocalLineGuiChatIdentity,
  runReaderProcess, validateLocalScope } from '../src/extensions/line-local-reader.mjs';
const args = { chatName: '測試群組', dateFrom: '2026-09-05', dateTo: '2026-09-11' };
const response = () => ({ ok: true, chatName: args.chatName, ownSenderRef: null, chatIdentity: { kind: 'group', displayName: args.chatName, uiIdentityVerified: false }, count: 1, messages: [{ sourceRef: 'message:test', date: '2026-09-05', sourceTimestamp: Date.parse('2026-09-05T00:00:00+08:00'), text: '多行\n😀' }], scope: { kind: 'local_database', truncated: false, requested: { ...args, messageLimit: 200, mediaMode: 'metadata' } }, pagination: { hasMore: false, nextCursor: null } });
const run = result => async () => ({ code: 0, stdout: JSON.stringify(result) });
const readerFixture = fileURLToPath(new URL('./test-fixtures/reader-lifecycle-child.mjs', import.meta.url));

async function withSyntheticLocalAppData(t, action) {
  const previous = process.env.LOCALAPPDATA;
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'line-reader-lifecycle-'));
  process.env.LOCALAPPDATA = root;
  t.after(async () => {
    if (previous === undefined) delete process.env.LOCALAPPDATA;
    else process.env.LOCALAPPDATA = previous;
    await removeSyntheticLocalAppData(root);
  });
  return action(root);
}

async function removeSyntheticLocalAppData(root) {
  const parent = path.join(root, 'line-desktop-mcp');
  const reader = path.join(parent, 'line-reader');
  try { await fs.rmdir(reader); } catch (error) { if (error?.code !== 'ENOENT') throw error; }
  try { await fs.rmdir(parent); } catch (error) { if (error?.code !== 'ENOENT') throw error; }
  try { await fs.rmdir(root); } catch (error) { if (error?.code !== 'ENOENT') throw error; }
}

function fixtureSpawn(mode, onSpawn = () => {}) {
  return (_pythonPath, _pythonArgs, options) => {
    const child = spawnChild(process.execPath, [readerFixture, mode], {
      env: options.env,
      shell: false,
      stdio: options.stdio,
      windowsHide: true,
    });
    onSpawn(child, options);
    return child;
  };
}

function requestDirectory(spawnOptions) {
  return path.join(spawnOptions.env.LOCALAPPDATA, 'line-desktop-mcp', 'line-reader',
    `line-reader-${spawnOptions.env.LINE_MCP_READER_REQUEST_ID}`);
}

async function removeKnownReaderFiles(directory) {
  for (const name of ['snapshot.edb-wal', 'snapshot.edb-shm', 'snapshot.edb-journal', 'snapshot.edb']) {
    try { await fs.unlink(path.join(directory, name)); } catch (error) { if (error?.code !== 'ENOENT') throw error; }
  }
}

function liveMockReaderChild({ streams = true, killResult = false } = {}) {
  const child = new EventEmitter();
  child.pid = 12345;
  child.exitCode = null;
  child.signalCode = null;
  child.killCalls = 0;
  child.kill = () => { child.killCalls++; return killResult; };
  if (streams) {
    child.stdin = new PassThrough();
    child.stdout = new PassThrough();
    child.stderr = new PassThrough();
  }
  return child;
}

async function waitForRemoval(target) {
  for (let attempt = 0; attempt < 25; attempt++) {
    try { await fs.access(target); }
    catch (error) {
      if (error?.code === 'ENOENT') return;
      throw error;
    }
    await new Promise(resolve => setTimeout(resolve, 10));
  }
  assert.fail(`owned directory was not removed after child close: ${target}`);
}

test('portable reader refuses implicit Python lookup and preserves fixed setup diagnostics', async () => {
  for (const pythonPath of [null, '', 'python', 'python.exe', './python.exe']) {
    await assert.rejects(runReaderProcess(args, { pythonPath }), { code: 'LOCAL_READER_UNAVAILABLE' });
  }
  for (const code of ['ENGINE_DLL_UNCONFIGURED', 'ENGINE_DLL_INVALID_PATH', 'ENGINE_DLL_UNAVAILABLE', 'RUNTIME_DIRECTORY_UNAVAILABLE']) {
    await assert.rejects(readLocalLineMessages(args, { runProcess: async () => ({ code: 2,
      stdout: JSON.stringify({ ok: false, code, message: 'private diagnostic must not escape' }) }) }),
    error => error.code === code && !error.message.includes('private diagnostic'));
  }
});

test('private chat identity lookup excludes history and is not a public message option', async () => {
  let calls = 0;
  await assert.rejects(readLocalLineMessages({ ...args, identityOnly: true }, { runProcess: async () => { calls++; } }));
  assert.equal(calls, 0);
  const runProcess = async scope => ({ code: 0, stdout: JSON.stringify({ ...response(),
    chatRef: 'chat:' + 'a'.repeat(24), count: 0, messages: [],
    scope: { kind: 'local_chat_identity', truncated: false, requested: scope } }) });
  const result = await readLocalLineChatIdentity(args, { runProcess });
  assert.equal(result.scope.requested.identityOnly, true);
  assert.deepEqual(result.messages, []);
  await assert.rejects(readLocalLineChatIdentity(args, { runProcess: run(response()) }), { code: 'LOCAL_READER_SCOPE_MISMATCH' });
});

test('GUI chat identity wrapper creates a private one-day Taipei scope and requires uniqueness proof', async () => {
  const now = () => new Date('2026-09-11T16:30:00.000Z');
  const chatRef = 'chat:' + 'b'.repeat(24);
  const runProcess = async scope => {
    assert.deepEqual(scope, {
      chatName: args.chatName,
      dateFrom: '2026-09-12',
      dateTo: '2026-09-12',
      identityOnly: true,
      guiIdentityOnly: true,
      messageLimit: 200,
      mediaMode: 'metadata',
    });
    return { code: 0, stdout: JSON.stringify({
      ok: true, chatName: scope.chatName, chatRef,
      chatIdentity: { kind: 'group', displayName: scope.chatName,
        uiIdentityVerified: false, guiDisplayNameUnique: true },
      count: 0, messages: [], pagination: { hasMore: false, nextCursor: null },
      scope: { kind: 'local_gui_chat_identity', truncated: false, requested: scope },
    }) };
  };
  const result = await readLocalLineGuiChatIdentity({ chatName: args.chatName }, { now, runProcess });
  assert.equal(result.chatRef, chatRef);
  assert.equal(result.chatIdentity.guiDisplayNameUnique, true);
  assert.equal(result.scope.kind, 'local_gui_chat_identity');

  for (const mutate of [
    result => { delete result.chatIdentity.guiDisplayNameUnique; },
    result => { result.chatIdentity.guiDisplayNameUnique = false; },
    result => { result.scope.kind = 'local_chat_identity'; },
    result => { result.scope.requested.guiIdentityOnly = undefined; },
    result => { result.scope.requested.unrelatedName = 'must not escape'; },
    result => { result.chatIdentity.unrelatedId = 'must not escape'; },
    result => { result.count = 1; },
    result => { result.messages = [{ sourceRef: 'message:private' }]; },
  ]) {
    await assert.rejects(readLocalLineGuiChatIdentity({ chatName: args.chatName }, { now,
      runProcess: async requested => {
        const output = await runProcess(requested);
        const value = JSON.parse(output.stdout);
        mutate(value);
        return { code: 0, stdout: JSON.stringify(value) };
      } }), { code: 'LOCAL_READER_SCOPE_MISMATCH' });
  }
});

test('GUI-only flag is rejected by public readers and malformed wrapper input never launches', async () => {
  const internal = { ...args, identityOnly: true, guiIdentityOnly: true };
  assert.throws(() => validateLocalScope(internal), { code: 'LINE_INVALID_ARGUMENT' });
  let calls = 0;
  await assert.rejects(readLocalLineMessages(internal, { runProcess: async () => { calls++; } }),
    { code: 'LINE_INVALID_ARGUMENT' });
  for (const input of [args, { chatName: args.chatName, chatType: 'group' }, { chatName: ' x' }, null]) {
    await assert.rejects(readLocalLineGuiChatIdentity(input, { runProcess: async () => { calls++; } }),
      { code: 'LINE_INVALID_ARGUMENT' });
  }
  assert.equal(calls, 0);
});

test('GUI identity unavailable is a safe propagated child code', async () => {
  await assert.rejects(readLocalLineGuiChatIdentity({ chatName: args.chatName }, {
    now: () => new Date('2026-09-12T00:00:00Z'),
    runProcess: async () => ({ code: 2, stdout: JSON.stringify({
      ok: false, code: 'GUI_IDENTITY_UNAVAILABLE', message: 'private inventory detail',
    }) }),
  }), error => error.code === 'GUI_IDENTITY_UNAVAILABLE'
    && !error.message.includes('inventory detail'));
});

test('bad scope is rejected before starting a subprocess', async () => {
  let calls = 0;
  for (const changed of [{ dateFrom: '2026-02-30' }, { dateTo: '2026-10-06' }, { chatName: ' x' }, { messageLimit: 0 }, { allChats: true }, { dateFrom: undefined }, { query: '\0' }]) {
    await assert.rejects(readLocalLineMessages({ ...args, ...changed }, { runProcess: async () => { calls++; } }));
  }
  assert.equal(calls, 0);
});

test('scoped Unicode results pass intact; identity/date/count mismatch refuses', async () => {
  const result = await readLocalLineMessages(args, { runProcess: run(response()) });
  assert.equal(result.messages[0].text, '多行\n😀');
  for (const result of [{ ...response(), chatName: '其他群組' }, { ...response(), count: 0 }, { ...response(), messages: [{ sourceRef: 'id', date: '2026-09-12' }] }]) {
    await assert.rejects(readLocalLineMessages(args, { runProcess: run(result) }), { code: 'LOCAL_READER_SCOPE_MISMATCH' });
  }
});

test('child exceptions and malicious error strings never leak', async () => {
  const secret = 'do-not-print-key';
  for (const runProcess of [async () => { throw new Error(secret); }, async () => ({ code: 2, stdout: JSON.stringify({ ok: false, code: secret, message: secret }) }), async () => ({ code: 0, stdout: secret })]) {
    try { await readLocalLineMessages(args, { runProcess }); assert.fail('should refuse'); }
    catch (error) { assert.ok(!error.message.includes(secret)); assert.ok(!error.code.includes(secret)); }
  }
});

test('null/numeric dates, timestamp mismatch and query-echo mismatch are refused', async () => {
  for (const date of [null, 20260905, '2026-02-30']) {
    const result = response(); result.messages[0].date = date;
    await assert.rejects(readLocalLineMessages(args, { runProcess: run(result) }), { code: 'LOCAL_READER_SCOPE_MISMATCH' });
  }
  const shifted = response(); shifted.messages[0].sourceTimestamp += 86400000;
  await assert.rejects(readLocalLineMessages(args, { runProcess: run(shifted) }), { code: 'LOCAL_READER_SCOPE_MISMATCH' });
  const wrong = response(); wrong.scope.requested.query = 'needle';
  await assert.rejects(readLocalLineMessages({ ...args, query: 'needle' }, { runProcess: run(wrong) }), { code: 'LOCAL_READER_SCOPE_MISMATCH' });
  const echo = response(); echo.scope.requested.messageLimit = 100;
  await assert.rejects(readLocalLineMessages(args, { runProcess: run(echo) }), { code: 'LOCAL_READER_SCOPE_MISMATCH' });
});

test('new media options default to metadata and invalid selectors fail before process launch', async () => {
  let calls = 0;
  for (const changed of [{ mediaMode: null }, { mediaMode: 'all' }, { mediaSourceRefs: [] },
    { mediaMode: 'preview', mediaSourceRefs: ['message:bad'] }, { cursor: 'bad!' }]) {
    await assert.rejects(readLocalLineMessages({ ...args, ...changed }, { runProcess: async () => { calls++; } }));
  }
  assert.equal(calls, 0);
  const refs = ['message:' + 'a'.repeat(24)];
  await readLocalLineMessages({ ...args, mediaMode: 'preview', mediaSourceRefs: refs }, { runProcess: async scope => {
    assert.equal(scope.mediaMode, 'preview');
    assert.deepEqual(scope.mediaSourceRefs, refs);
    return { code: 0, stdout: JSON.stringify({ ...response(), scope: { kind: 'local_database', truncated: false, requested: scope } }) };
  } });
});

test('cursor scope is checked before launch and response cursor must identify oldest returned row', async () => {
  const chat = 'chat:' + 'a'.repeat(24);
  const token = { v: 1, scope: createHash('sha256').update(JSON.stringify([args.chatName, args.dateFrom, args.dateTo, null])).digest('hex'),
    chat, time: response().messages[0].sourceTimestamp, id: 'fixture-id' };
  const cursor = Buffer.from(JSON.stringify(token)).toString('base64url');
  let calls = 0;
  await assert.rejects(readLocalLineMessages({ ...args, query: 'other', cursor }, { runProcess: async () => { calls++; } }), { code: 'CURSOR_SCOPE_MISMATCH' });
  assert.equal(calls, 0);
  const result = response();
  result.chatRef = chat;
  result.messages[0].sourceMessageId = token.id;
  result.scope.truncated = true;
  result.pagination = { hasMore: true, nextCursor: cursor };
  await readLocalLineMessages(args, { runProcess: run(result) });
  result.messages[0].sourceMessageId = 'different';
  await assert.rejects(readLocalLineMessages(args, { runProcess: run(result) }), { code: 'LOCAL_READER_INVALID_RESULT' });
});

test('safe diagnostic codes survive child failures without exception text or keys', async () => {
  for (const code of ['RESULT_TOO_LARGE', 'MAIN_DATABASE_AMBIGUOUS', 'LINE_PROCESS_AMBIGUOUS', 'ENGINE_INTEGRITY_FAILED', 'SESSION_KEY_CHANGED', 'INVALID_CURSOR',
    'INVALID_PATH', 'SOURCE_LIMIT_INVALID', 'SNAPSHOT_DESTINATION_EXISTS', 'SNAPSHOT_DISK_FULL', 'SNAPSHOT_IO_ERROR', 'SNAPSHOT_CLEANUP_FAILED']) {
    await assert.rejects(readLocalLineMessages(args, { runProcess: async () => ({ code: 2, stdout: JSON.stringify({ ok: false, code, message: 'secret path/key' }) }) }), error => {
      assert.equal(error.code, code);
      assert.equal(error.message.includes('secret'), false);
      return true;
    });
  }
});

test('successful reader results must include complete pagination even on the final page', async () => {
  for (const pagination of [undefined, null, false, [], {}, { hasMore: false }, { hasMore: true, nextCursor: null }]) {
    await assert.rejects(readLocalLineMessages(args, { runProcess: run({ ...response(), pagination }) }), { code: 'LOCAL_READER_INVALID_RESULT' });
  }
});

test('direct kind is scope-bound and reader identity must agree before accepting rows', async () => {
  const requested = { ...args, chatType: 'direct' };
  let calls = 0;
  await assert.rejects(readLocalLineMessages({ ...args, chatType: 'all' }, { runProcess: async () => { calls++; } }));
  assert.equal(calls, 0);
  const result = response();
  result.scope.requested.chatType = 'direct';
  await assert.rejects(readLocalLineMessages(requested, { runProcess: run(result) }), { code: 'LOCAL_READER_SCOPE_MISMATCH' });
  result.chatIdentity.kind = 'direct';
  await readLocalLineMessages(requested, { runProcess: run(result) });
  result.chatIdentity.displayName = 'other';
  await assert.rejects(readLocalLineMessages(requested, { runProcess: run(result) }), { code: 'LOCAL_READER_SCOPE_MISMATCH' });
  const token = { v: 1, scope: createHash('sha256').update(JSON.stringify([args.chatName, args.dateFrom, args.dateTo, null, 'direct'])).digest('hex'),
    chat: 'chat:' + 'a'.repeat(24), time: result.messages[0].sourceTimestamp, id: 'fixture' };
  const cursor = Buffer.from(JSON.stringify(token)).toString('base64url');
  for (const chatType of ['group', 'auto', undefined]) {
    await assert.rejects(readLocalLineMessages({ ...args, chatType, cursor }, { runProcess: async () => { calls++; } }), { code: 'CURSOR_SCOPE_MISMATCH' });
  }
  assert.equal(calls, 0);
});

test('reader parent owns a real hanging child until it exits, then removes its exact scratch directory', async t => {
  await withSyntheticLocalAppData(t, async () => {
    let spawned;
    let scratchDirectory;
    let ready;
    const readyPromise = new Promise(resolve => { ready = resolve; });
    const running = runReaderProcess(args, {
      pythonPath: process.execPath,
      timeoutMs: 500,
      spawnProcess: fixtureSpawn('hang', (child, options) => {
        spawned = child;
        scratchDirectory = requestDirectory(options);
        child.stdout.on('data', chunk => {
          if (chunk.toString('utf8').includes('READY')) ready();
        });
      }),
    });
    await readyPromise;
    await fs.access(path.join(scratchDirectory, 'snapshot.edb'));
    assert.equal(spawned.exitCode, null);
    await assert.rejects(running, { code: 'LOCAL_READER_TIMEOUT' });
    assert.ok(spawned.exitCode !== null || spawned.signalCode !== null);
    await assert.rejects(fs.access(scratchDirectory), { code: 'ENOENT' });
  });
});

test('reader parent cleans synthetic normal and nonzero child exits without reading LINE data', async t => {
  await withSyntheticLocalAppData(t, async () => {
    for (const [mode, expectedCode] of [['success', 0], ['nonzero', 2]]) {
      let scratchDirectory;
      const output = await runReaderProcess(args, {
        pythonPath: process.execPath,
        timeoutMs: 1_000,
        spawnProcess: fixtureSpawn(mode, (_child, options) => { scratchDirectory = requestDirectory(options); }),
      });
      assert.equal(output.code, expectedCode);
      assert.equal(output.stdout.includes('private synthetic stderr'), false);
      await assert.rejects(fs.access(scratchDirectory), { code: 'ENOENT' });
    }
  });
});

test('reader parent kills an overflowing synthetic child and cleans only after close', async t => {
  await withSyntheticLocalAppData(t, async () => {
    let scratchDirectory;
    await assert.rejects(runReaderProcess(args, {
      pythonPath: process.execPath,
      timeoutMs: 2_000,
      spawnProcess: fixtureSpawn('overflow', (_child, options) => { scratchDirectory = requestDirectory(options); }),
    }), { code: 'LOCAL_READER_RESULT_TOO_LARGE' });
    await assert.rejects(fs.access(scratchDirectory), { code: 'ENOENT' });
  });
});

test('reader spawn failures clean the freshly created owned directory and never expose private errors', async t => {
  await withSyntheticLocalAppData(t, async () => {
    const secret = 'private-spawn-failure';
    let scratchDirectory;
    await assert.rejects(runReaderProcess(args, {
      pythonPath: process.execPath,
      spawnProcess: (_pythonPath, _pythonArgs, options) => {
        scratchDirectory = requestDirectory(options);
        throw new Error(secret);
      },
    }), error => error.code === 'LOCAL_READER_UNAVAILABLE' && !error.message.includes(secret));
    await assert.rejects(fs.access(scratchDirectory), { code: 'ENOENT' });
  });
});

test('reader cleans its owned directory after an asynchronous executable spawn failure', async t => {
  await withSyntheticLocalAppData(t, async root => {
    let scratchDirectory;
    const missingExecutable = path.join(root, 'missing-reader.exe');
    await assert.rejects(runReaderProcess(args, {
      pythonPath: missingExecutable,
      spawnProcess: (executable, commandArgs, options) => {
        scratchDirectory = requestDirectory(options);
        return spawnChild(executable, commandArgs, options);
      },
    }), { code: 'LOCAL_READER_UNAVAILABLE' });
    await assert.rejects(fs.access(scratchDirectory), { code: 'ENOENT' });
  });
});

test('a live child that refuses termination returns a bounded failure, retains scratch, then cleans only after close', async t => {
  await withSyntheticLocalAppData(t, async () => {
    const child = liveMockReaderChild();
    let scratchDirectory;
    await assert.rejects(runReaderProcess(args, {
      pythonPath: process.execPath,
      timeoutMs: 1,
      spawnProcess: (_executable, _commandArgs, options) => {
        scratchDirectory = requestDirectory(options);
        return child;
      },
    }), error => error.code === 'LOCAL_READER_TERMINATION_FAILED'
      && error.details.mayStillBeRunning === true);
    assert.equal(child.killCalls, 1);
    await fs.access(scratchDirectory);
    child.emit('close', null);
    await waitForRemoval(scratchDirectory);
  });
});

test('a signalled child that never closes also reaches the bounded retained-scratch failure', async t => {
  await withSyntheticLocalAppData(t, async () => {
    const child = liveMockReaderChild({ killResult: true });
    let scratchDirectory;
    await assert.rejects(runReaderProcess(args, {
      pythonPath: process.execPath,
      timeoutMs: 1,
      spawnProcess: (_executable, _commandArgs, options) => {
        scratchDirectory = requestDirectory(options);
        return child;
      },
    }), error => error.code === 'LOCAL_READER_TERMINATION_FAILED'
      && error.details.mayStillBeRunning === true);
    assert.equal(child.killCalls, 1);
    await fs.access(scratchDirectory);
    child.emit('close', null);
    await waitForRemoval(scratchDirectory);
  });
});

test('a malformed injected child with a possible live PID retains scratch until its later close', async t => {
  await withSyntheticLocalAppData(t, async () => {
    const child = liveMockReaderChild({ streams: false });
    let scratchDirectory;
    await assert.rejects(runReaderProcess(args, {
      pythonPath: process.execPath,
      timeoutMs: 1,
      spawnProcess: (_executable, _commandArgs, options) => {
        scratchDirectory = requestDirectory(options);
        return child;
      },
    }), error => error.code === 'LOCAL_READER_TERMINATION_FAILED'
      && error.details.mayStillBeRunning === true);
    assert.equal(child.killCalls, 1);
    await fs.access(scratchDirectory);
    child.emit('close', null);
    await waitForRemoval(scratchDirectory);
  });
});

test('reader rejects malformed scope before configuration, directory work, or spawn', async () => {
  let spawned = 0;
  await assert.rejects(runReaderProcess({ ...args, unknownOption: true }, {
    pythonPath: null,
    spawnProcess: () => { spawned++; },
  }), { code: 'LINE_INVALID_ARGUMENT' });
  assert.equal(spawned, 0);
});

test('reader rejects invalid environment timeout values before filesystem or spawn work, but allows a small programmatic override', async t => {
  await withSyntheticLocalAppData(t, async () => {
    const previous = process.env.LINE_MCP_READER_TIMEOUT_MS;
    t.after(() => {
      if (previous === undefined) delete process.env.LINE_MCP_READER_TIMEOUT_MS;
      else process.env.LINE_MCP_READER_TIMEOUT_MS = previous;
    });
    for (const value of ['999', '1800001', '01000', '1000.0', ' 1000', 'not-a-number']) {
      process.env.LINE_MCP_READER_TIMEOUT_MS = value;
      let spawned = 0;
      await assert.rejects(runReaderProcess(args, {
        pythonPath: process.execPath,
        spawnProcess: () => { spawned++; },
      }), { code: 'LOCAL_READER_TIMEOUT_INVALID' });
      assert.equal(spawned, 0);
    }
    process.env.LINE_MCP_READER_TIMEOUT_MS = 'not-a-number';
    let spawned = 0;
    await assert.rejects(runReaderProcess(args, {
      pythonPath: process.execPath,
      timeoutMs: 5,
      spawnProcess: () => { spawned++; throw new Error('synthetic only'); },
    }), { code: 'LOCAL_READER_UNAVAILABLE' });
    assert.equal(spawned, 1);
  });
});

test('reader rejects a junction runtime destination before creating a request directory or spawning', async t => {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'line-reader-reparse-root-'));
  const target = path.join(root, 'target');
  const junction = path.join(root, 'junction');
  const previous = process.env.LOCALAPPDATA;
  await fs.mkdir(target);
  try {
    await fs.symlink(target, junction, 'junction');
  } catch (error) {
    await fs.rmdir(target);
    await fs.rmdir(root);
    if (error?.code === 'EPERM' || error?.code === 'EACCES') {
      t.skip('Windows did not permit a synthetic junction.');
      return;
    }
    throw error;
  }
  t.after(async () => {
    if (previous === undefined) delete process.env.LOCALAPPDATA;
    else process.env.LOCALAPPDATA = previous;
    await fs.unlink(junction);
    await fs.rmdir(target);
    await fs.rmdir(root);
  });
  process.env.LOCALAPPDATA = junction;
  let spawned = 0;
  await assert.rejects(runReaderProcess(args, {
    pythonPath: process.execPath,
    spawnProcess: () => { spawned++; },
  }), { code: 'LOCAL_READER_UNAVAILABLE' });
  assert.equal(spawned, 0);
});

test('reader cleanup refuses unexpected files and reparse snapshot destinations without recursive deletion', async t => {
  await withSyntheticLocalAppData(t, async root => {
    let unexpectedDirectory;
    await assert.rejects(runReaderProcess(args, {
      pythonPath: process.execPath,
      spawnProcess: fixtureSpawn('unexpected', (_child, options) => { unexpectedDirectory = requestDirectory(options); }),
    }), { code: 'LOCAL_READER_CLEANUP_FAILED' });
    await fs.access(path.join(unexpectedDirectory, 'unexpected-private-file'));
    await removeKnownReaderFiles(unexpectedDirectory);
    await fs.unlink(path.join(unexpectedDirectory, 'unexpected-private-file'));
    await fs.rmdir(unexpectedDirectory);

    let reparseDirectory;
    try {
      await assert.rejects(runReaderProcess(args, {
        pythonPath: process.execPath,
        spawnProcess: fixtureSpawn('reparse', (_child, options) => { reparseDirectory = requestDirectory(options); }),
      }), { code: 'LOCAL_READER_CLEANUP_FAILED' });
      const info = await fs.lstat(path.join(reparseDirectory, 'snapshot.edb'));
      assert.equal(info.isSymbolicLink(), true);
      await fs.unlink(path.join(reparseDirectory, 'snapshot.edb'));
      await fs.rmdir(reparseDirectory);
    } catch (error) {
      if (error?.code === 'EPERM' || error?.code === 'EACCES') t.skip('Windows did not permit a synthetic file symlink.');
      else throw error;
    }
    await fs.access(root);
  });
});

test('SOURCE_TOO_LARGE exposes only valid configured limit details and filters malformed private child data', async t => {
  const previous = process.env.LINE_MCP_MAX_WAL_BYTES;
  t.after(() => {
    if (previous === undefined) delete process.env.LINE_MCP_MAX_WAL_BYTES;
    else process.env.LINE_MCP_MAX_WAL_BYTES = previous;
  });
  delete process.env.LINE_MCP_MAX_WAL_BYTES;
  const valid = {
    ok: false,
    code: 'SOURCE_TOO_LARGE',
    details: {
      sourceKind: 'wal',
      sourceBytes: 268_435_457,
      maxBytes: 268_435_456,
      setting: 'LINE_MCP_MAX_WAL_BYTES',
    },
  };
  await assert.rejects(readLocalLineMessages(args, { runProcess: async () => ({ code: 2, stdout: JSON.stringify(valid) }) }), error => {
    assert.equal(error.code, 'SOURCE_TOO_LARGE');
    assert.equal(error.message.includes('private'), false);
    assert.deepEqual(error.details, valid.details);
    return true;
  });

  process.env.LINE_MCP_MAX_WAL_BYTES = '00042';
  const configured = { ...valid, details: { ...valid.details, sourceBytes: 43, maxBytes: 42 } };
  await assert.rejects(readLocalLineMessages(args, { runProcess: async () => ({ code: 2, stdout: JSON.stringify(configured) }) }), error => {
    assert.equal(error.code, 'SOURCE_TOO_LARGE');
    assert.deepEqual(error.details, configured.details);
    return true;
  });

  const secret = 'C:\\private\\database.edb';
  for (const details of [
    { ...valid.details, sourceBytes: valid.details.maxBytes },
    { ...valid.details, sourceKind: 'not-a-source' },
    { ...valid.details, setting: 'LINE_MCP_MAX_SOURCE_BYTES' },
    { ...valid.details, privatePath: secret },
    { sourceKind: 'wal', sourceBytes: '268435457', maxBytes: 268_435_456, setting: 'LINE_MCP_MAX_WAL_BYTES' },
  ]) {
    await assert.rejects(readLocalLineMessages(args, { runProcess: async () => ({ code: 2, stdout: JSON.stringify({
      ok: false, code: 'SOURCE_TOO_LARGE', details, message: secret,
    }) }) }), error => error.code === 'LOCAL_READER_FAILED'
      && !error.message.includes(secret) && !JSON.stringify(error.details).includes(secret));
  }
});
