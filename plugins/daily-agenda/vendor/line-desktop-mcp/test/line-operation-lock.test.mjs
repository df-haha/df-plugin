import assert from 'node:assert/strict';
import { once } from 'node:events';
import { mkdir, mkdtemp, readFile, rm, writeFile } from 'node:fs/promises';
import { homedir, tmpdir } from 'node:os';
import { dirname, join } from 'node:path';
import { spawn } from 'node:child_process';
import test from 'node:test';
import { fileURLToPath } from 'node:url';

import {
  DEFAULT_LINE_OPERATION_LOCK_PATH,
  LINE_BUSY,
  LineOperationCallbackAndCleanupError,
  LineOperationCleanupError,
  withLineOperation,
} from '../src/automation/line-operation-lock.mjs';

const fixturePath = fileURLToPath(new URL('./test-fixtures/hold-lock.mjs', import.meta.url));

async function makeLockPath(t) {
  const directory = await mkdtemp(join(tmpdir(), 'line-operation-lock-'));
  t.after(async () => {
    await rm(directory, { recursive: true, force: true });
  });
  return join(directory, 'run', 'operation.lock');
}

function waitForOutput(stream, expected, timeoutMs = 5_000) {
  return new Promise((resolve, reject) => {
    let output = '';
    const timeout = setTimeout(() => {
      finish(new Error(`Timed out waiting for ${expected}; received: ${output}`));
    }, timeoutMs);

    function onData(chunk) {
      output += chunk.toString();
      if (output.includes(expected)) {
        finish();
      }
    }

    function onError(error) {
      finish(error);
    }

    function finish(error) {
      clearTimeout(timeout);
      stream.off('data', onData);
      stream.off('error', onError);
      if (error) {
        reject(error);
      } else {
        resolve(output);
      }
    }

    stream.on('data', onData);
    stream.once('error', onError);
  });
}

test('exports the stable machine-local default lock path', () => {
  assert.equal(
    DEFAULT_LINE_OPERATION_LOCK_PATH,
    join(homedir(), '.line-desktop-mcp', 'operation.lock'),
  );
});

test('records only the minimal ownership metadata', async (t) => {
  const lockPath = await makeLockPath(t);
  let metadata;

  const result = await withLineOperation('history-read', async () => 'done', {
    lockPath,
    testHooks: {
      beforeRelease: async () => {
        metadata = JSON.parse(await readFile(lockPath, 'utf8'));
      },
    },
  });

  assert.equal(result, 'done');
  assert.deepEqual(Object.keys(metadata).sort(), [
    'createdAt',
    'nonce',
    'operation',
    'pid',
  ]);
  assert.match(metadata.nonce, /^[a-f0-9]{32}$/);
  assert.equal(metadata.pid, process.pid);
  assert.equal(metadata.operation, 'history-read');
  assert.ok(Number.isFinite(Date.parse(metadata.createdAt)));
});

test('fails fast across processes and never runs the contending callback', async (t) => {
  const lockPath = await makeLockPath(t);
  const child = spawn(process.execPath, [fixturePath, lockPath], {
    stdio: ['pipe', 'pipe', 'pipe'],
  });
  const exitPromise = once(child, 'exit');
  let childExited = false;
  void exitPromise.then(
    () => {
      childExited = true;
    },
    () => {},
  );
  t.after(() => {
    if (!childExited) {
      child.kill();
    }
  });

  await waitForOutput(child.stdout, 'LOCKED');

  let callbackRan = false;
  await assert.rejects(
    withLineOperation(
      'send-text',
      async () => {
        callbackRan = true;
      },
      { lockPath },
    ),
    (error) => error?.code === LINE_BUSY,
  );
  assert.equal(callbackRan, false);

  child.stdin.end('release\n');
  const [exitCode] = await exitPromise;
  assert.equal(exitCode, 0);
});

test('releases its own lock after a callback exception and preserves the callback error', async (t) => {
  const lockPath = await makeLockPath(t);
  const expected = new Error('callback failed');

  await assert.rejects(
    withLineOperation(
      'history-read',
      async () => {
        throw expected;
      },
      { lockPath },
    ),
    (error) => error === expected,
  );

  const result = await withLineOperation('history-read', async () => 'released', {
    lockPath,
  });
  assert.equal(result, 'released');
});

test('retains a foreign lock and reports that the completed operation must be inspected', async (t) => {
  const lockPath = await makeLockPath(t);
  const foreignMetadata = `${JSON.stringify({
    nonce: 'f'.repeat(32),
    pid: 12345,
    createdAt: '2000-01-01T00:00:00.000Z',
    operation: 'send-text',
  })}\n`;

  await assert.rejects(
    withLineOperation(
      'send-text',
      async () => 'sent',
      {
        lockPath,
        testHooks: {
          beforeRelease: async () => {
            await writeFile(lockPath, foreignMetadata, 'utf8');
          },
        },
      },
    ),
    (error) =>
      error instanceof LineOperationCleanupError &&
      error.reason === 'LINE_LOCK_OWNERSHIP_LOST' &&
      error.operationMayHaveCompleted === true,
  );

  assert.equal(await readFile(lockPath, 'utf8'), foreignMetadata);
});

test('wraps callback and cleanup failure without mutating the callback error', async (t) => {
  const lockPath = await makeLockPath(t);
  const callbackError = new Error('transport status was uncertain');
  const foreignMetadata = `${JSON.stringify({
    nonce: 'e'.repeat(32),
    pid: 54321,
    createdAt: '2000-01-01T00:00:00.000Z',
    operation: 'send-text',
  })}\n`;

  await assert.rejects(
    withLineOperation(
      'send-text',
      async () => {
        throw callbackError;
      },
      {
        lockPath,
        testHooks: {
          beforeRelease: async () => {
            await writeFile(lockPath, foreignMetadata, 'utf8');
          },
        },
      },
    ),
    (error) => {
      assert.ok(error instanceof LineOperationCallbackAndCleanupError);
      assert.equal(error.code, 'LINE_OPERATION_CALLBACK_AND_CLEANUP_FAILED');
      assert.equal(error.cause, callbackError);
      assert.equal(error.callbackError, callbackError);
      assert.equal(error.operationMayHaveCompleted, true);
      assert.ok(error.cleanupError instanceof LineOperationCleanupError);
      assert.match(error.message, /inspect/i);
      assert.equal(callbackError.operationMayHaveCompleted, undefined);
      assert.equal(callbackError.lineOperationCleanupError, undefined);
      return true;
    },
  );

  assert.equal(await readFile(lockPath, 'utf8'), foreignMetadata);
});

test('does not take over a stale lock or run the callback', async (t) => {
  const lockPath = await makeLockPath(t);
  await mkdir(dirname(lockPath), { recursive: true });
  const staleMetadata = `${JSON.stringify({
    nonce: 'd'.repeat(32),
    pid: 99999,
    createdAt: '2000-01-01T00:00:00.000Z',
    operation: 'history-read',
  })}\n`;
  await writeFile(lockPath, staleMetadata, 'utf8');

  let callbackRan = false;
  await assert.rejects(
    withLineOperation(
      'history-read',
      async () => {
        callbackRan = true;
      },
      { lockPath },
    ),
    (error) => error?.code === LINE_BUSY,
  );
  assert.equal(callbackRan, false);
  assert.equal(await readFile(lockPath, 'utf8'), staleMetadata);
});

test('treats an invalid existing lock as busy without running the callback', async (t) => {
  const lockPath = await makeLockPath(t);
  await mkdir(dirname(lockPath), { recursive: true });
  await writeFile(lockPath, 'not valid lock metadata\n', 'utf8');

  let callbackRan = false;
  await assert.rejects(
    withLineOperation(
      'history-read',
      async () => {
        callbackRan = true;
      },
      { lockPath },
    ),
    (error) => error?.code === LINE_BUSY,
  );
  assert.equal(callbackRan, false);
  assert.equal(await readFile(lockPath, 'utf8'), 'not valid lock metadata\n');
});
