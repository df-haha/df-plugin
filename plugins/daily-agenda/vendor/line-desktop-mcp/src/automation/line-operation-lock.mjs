import { randomBytes } from 'node:crypto';
import { promises as fs } from 'node:fs';
import { dirname, isAbsolute, join } from 'node:path';
import { homedir } from 'node:os';

/**
 * One stable, machine-local lock shared by all LINE MCP Node processes.
 * Callers may override this only for isolated tests.
 */
export const DEFAULT_LINE_OPERATION_LOCK_PATH =
  join(homedir(), '.line-desktop-mcp', 'operation.lock');

export const LINE_BUSY = 'LINE_BUSY';

const OPERATION_KIND_PATTERN = /^[A-Za-z][A-Za-z0-9._:-]{0,63}$/;

export class LineOperationBusyError extends Error {
  constructor({ lockPath, operation }) {
    super(
      `LINE_BUSY: ${operation} did not start because another LINE operation owns the lock.`,
    );
    this.name = 'LineOperationBusyError';
    this.code = LINE_BUSY;
    this.lockPath = lockPath;
    this.operation = operation;
  }
}

export class LineOperationLockError extends Error {
  constructor(message, { code, lockPath, operation, cause, cleanupError } = {}) {
    super(message, cause === undefined ? undefined : { cause });
    this.name = 'LineOperationLockError';
    this.code = code;
    this.lockPath = lockPath;
    this.operation = operation;
    if (cleanupError !== undefined) {
      this.cleanupError = cleanupError;
    }
  }
}

/**
 * A cleanup error means the callback returned, so an external LINE action may
 * already have completed. Callers must inspect the chat state before retrying.
 */
export class LineOperationCleanupError extends Error {
  constructor(reason, { lockPath, operation, cause } = {}) {
    super(
      `LINE_LOCK_CLEANUP_FAILED (${reason}): the LINE operation may already have completed; inspect state before retrying.`,
      cause === undefined ? undefined : { cause },
    );
    this.name = 'LineOperationCleanupError';
    this.code = 'LINE_LOCK_CLEANUP_FAILED';
    this.reason = reason;
    this.lockPath = lockPath;
    this.operation = operation;
    this.operationMayHaveCompleted = true;
  }
}

/**
 * Used whenever both the callback and lock cleanup fail. The original callback
 * failure remains available through both `cause` and `callbackError`.
 */
export class LineOperationCallbackAndCleanupError extends Error {
  constructor(callbackError, cleanupError) {
    super(
      'LINE_OPERATION_CALLBACK_AND_CLEANUP_FAILED: the callback failed and the lock could not be cleaned up; inspect LINE state before retrying.',
      { cause: callbackError },
    );
    this.name = 'LineOperationCallbackAndCleanupError';
    this.code = 'LINE_OPERATION_CALLBACK_AND_CLEANUP_FAILED';
    this.callbackError = callbackError;
    this.cleanupError = cleanupError;
    this.operationMayHaveCompleted = true;
  }
}

/**
 * Run one complete LINE operation while holding a cross-process filesystem
 * lock. There is intentionally no retry, stale-lock takeover, or replay.
 *
 * `operation` must be a stable kind such as `history-read` or `send-text`.
 * Never pass chat names, recipients, or message content.
 *
 * @template T
 * @param {string} operation
 * @param {() => T | Promise<T>} fn
 * @param {{lockPath?: string, testHooks?: {beforeRelease?: () => unknown | Promise<unknown>}}} [options]
 * @returns {Promise<T>}
 */
export async function withLineOperation(operation, fn, options = {}) {
  const operationKind = validateOperationKind(operation);
  if (typeof fn !== 'function') {
    throw new TypeError('fn must be a function.');
  }

  const { lockPath, beforeRelease } = normalizeOptions(options);
  const lock = await acquireLock(lockPath, operationKind);

  let callbackResult;
  let callbackError;
  let callbackThrew = false;

  try {
    callbackResult = await fn();
  } catch (error) {
    callbackError = error;
    callbackThrew = true;
  }

  let cleanupError;
  try {
    await releaseOwnedLock(lock, beforeRelease);
  } catch (error) {
    cleanupError = error;
  }

  if (callbackThrew) {
    if (cleanupError !== undefined) {
      throw new LineOperationCallbackAndCleanupError(callbackError, cleanupError);
    }
    throw callbackError;
  }

  if (cleanupError !== undefined) {
    throw cleanupError;
  }

  return callbackResult;
}

function validateOperationKind(operation) {
  if (typeof operation !== 'string' || !OPERATION_KIND_PATTERN.test(operation)) {
    throw new TypeError(
      'operation must be a short stable operation kind (for example, history-read or send-text), never a chat name or message content.',
    );
  }
  return operation;
}

function normalizeOptions(options) {
  if (options === null || typeof options !== 'object' || Array.isArray(options)) {
    throw new TypeError('options must be an object when provided.');
  }

  const lockPath = options.lockPath ?? DEFAULT_LINE_OPERATION_LOCK_PATH;
  if (typeof lockPath !== 'string' || !isAbsolute(lockPath)) {
    throw new TypeError('lockPath must be an absolute path.');
  }

  const testHooks = options.testHooks;
  if (
    testHooks !== undefined &&
    (testHooks === null || typeof testHooks !== 'object' || Array.isArray(testHooks))
  ) {
    throw new TypeError('testHooks must be an object when provided.');
  }

  const beforeRelease = testHooks?.beforeRelease;
  if (beforeRelease !== undefined && typeof beforeRelease !== 'function') {
    throw new TypeError('testHooks.beforeRelease must be a function when provided.');
  }

  return { lockPath, beforeRelease };
}

async function acquireLock(lockPath, operation) {
  try {
    await fs.mkdir(dirname(lockPath), { recursive: true });
  } catch (cause) {
    throw new LineOperationLockError(
      'LINE_LOCK_ACQUIRE_FAILED: could not prepare the lock directory.',
      {
        code: 'LINE_LOCK_ACQUIRE_FAILED',
        lockPath,
        operation,
        cause,
      },
    );
  }

  let handle;
  try {
    // `wx` maps to exclusive create. An existing lock is never inspected,
    // retried, deleted, or taken over.
    handle = await fs.open(lockPath, 'wx', 0o600);
  } catch (cause) {
    if (cause?.code === 'EEXIST' || cause?.code === 'EISDIR') {
      throw new LineOperationBusyError({ lockPath, operation });
    }
    throw new LineOperationLockError(
      'LINE_LOCK_ACQUIRE_FAILED: could not create the operation lock.',
      {
        code: 'LINE_LOCK_ACQUIRE_FAILED',
        lockPath,
        operation,
        cause,
      },
    );
  }

  const metadataText = `${JSON.stringify({
    nonce: randomBytes(16).toString('hex'),
    pid: process.pid,
    createdAt: new Date().toISOString(),
    operation,
  })}\n`;

  let identity;
  try {
    await handle.writeFile(metadataText, 'utf8');
    await handle.sync();
    identity = toIdentity(await handle.stat());
  } catch (cause) {
    const cleanupError = await abandonFailedAcquire({
      handle,
      identity,
      lockPath,
      metadataText,
      operation,
    });
    throw new LineOperationLockError(
      'LINE_LOCK_ACQUIRE_FAILED: the operation lock could not be initialized.',
      {
        code: 'LINE_LOCK_ACQUIRE_FAILED',
        lockPath,
        operation,
        cause,
        cleanupError,
      },
    );
  }

  return { handle, identity, lockPath, metadataText, operation };
}

async function abandonFailedAcquire(lock) {
  try {
    await lock.handle.close();
  } catch (cause) {
    return createCleanupError('LINE_LOCK_CLOSE_FAILED', lock, cause);
  }

  // If ownership was never proven, leave the possibly invalid lock in place.
  // That is deliberate fail-closed behavior.
  if (lock.identity === undefined) {
    return undefined;
  }

  try {
    await removeOnlyExactOwnedLock(lock);
    return undefined;
  } catch (error) {
    return error;
  }
}

async function releaseOwnedLock(lock, beforeRelease) {
  try {
    await lock.handle.close();
  } catch (cause) {
    throw createCleanupError('LINE_LOCK_CLOSE_FAILED', lock, cause);
  }

  if (beforeRelease !== undefined) {
    try {
      await beforeRelease();
    } catch (cause) {
      throw createCleanupError('LINE_LOCK_TEST_HOOK_FAILED', lock, cause);
    }
  }

  await removeOnlyExactOwnedLock(lock);
}

async function removeOnlyExactOwnedLock(lock) {
  let beforeRead;
  try {
    beforeRead = await fs.lstat(lock.lockPath);
  } catch (cause) {
    throw createCleanupError('LINE_LOCK_OWNERSHIP_LOST', lock, cause);
  }

  if (!hasSameIdentity(beforeRead, lock.identity)) {
    throw createCleanupError('LINE_LOCK_OWNERSHIP_LOST', lock);
  }

  let currentMetadata;
  try {
    currentMetadata = await fs.readFile(lock.lockPath, 'utf8');
  } catch (cause) {
    throw createCleanupError('LINE_LOCK_OWNERSHIP_LOST', lock, cause);
  }

  let afterRead;
  try {
    afterRead = await fs.lstat(lock.lockPath);
  } catch (cause) {
    throw createCleanupError('LINE_LOCK_OWNERSHIP_LOST', lock, cause);
  }

  if (
    !hasSameIdentity(afterRead, lock.identity) ||
    currentMetadata !== lock.metadataText
  ) {
    throw createCleanupError('LINE_LOCK_OWNERSHIP_LOST', lock);
  }

  try {
    await fs.unlink(lock.lockPath);
  } catch (cause) {
    throw createCleanupError('LINE_LOCK_DELETE_FAILED', lock, cause);
  }
}

function toIdentity(stat) {
  return { dev: stat.dev, ino: stat.ino };
}

function hasSameIdentity(stat, expected) {
  return (
    expected !== undefined &&
    stat.isFile() &&
    stat.dev === expected.dev &&
    stat.ino === expected.ino
  );
}

function createCleanupError(reason, lock, cause) {
  return new LineOperationCleanupError(reason, {
    lockPath: lock.lockPath,
    operation: lock.operation,
    cause,
  });
}
