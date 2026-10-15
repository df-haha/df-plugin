import { randomBytes } from 'node:crypto';
import fs from 'node:fs/promises';
import path from 'node:path';

/**
 * Action store: pending / committing / done directories under <home>/actions/.
 *
 * ID format: 32 lowercase hex chars (128-bit random).
 * All writes use exclusive-create temp files + rename for atomicity.
 * POSIX: file mode 0o600 (owner-only). Windows: relies on user profile ACL.
 */

const ID_RE = /^[0-9a-f]{32}$/;

/** Validate an action ID against the fixed format. Prevents path traversal. */
export const isValidId = (id) => typeof id === 'string' && ID_RE.test(id);

/** Generate a new action ID. */
export const newActionId = () => randomBytes(16).toString('hex');

/** Generate an idempotency nonce. */
export const newNonce = () => randomBytes(16).toString('hex');

/**
 * @param {string} home - DAILY_AGENDA_HOME directory
 * @returns {{pending: string, committing: string, done: string}}
 */
export const actionDirs = (home) => {
  const base = path.resolve(home, 'actions');
  return {
    pending: path.resolve(base, 'pending'),
    committing: path.resolve(base, 'committing'),
    done: path.resolve(base, 'done'),
  };
};

/** Ensure action directories exist. */
export const ensureDirs = async (home) => {
  const dirs = actionDirs(home);
  await fs.mkdir(dirs.pending, { recursive: true });
  await fs.mkdir(dirs.committing, { recursive: true });
  await fs.mkdir(dirs.done, { recursive: true });
};

/**
 * Atomically write a JSON file. Uses exclusive-create temp + rename.
 * @param {string} filePath
 * @param {unknown} data
 */
export const atomicWriteJson = async (filePath, data) => {
  const dir = path.dirname(filePath);
  const tmp = path.join(dir, `.${path.basename(filePath)}.${randomBytes(8).toString('hex')}.tmp`);
  const fd = await fs.open(tmp, 'wx', 0o600);
  try {
    const content = JSON.stringify(data, null, 2);
    await fd.writeFile(content, 'utf8');
    await fd.sync();
  } finally {
    await fd.close();
  }
  await fs.rename(tmp, filePath);
};

/**
 * Read a JSON file. Returns null if not found.
 * @param {string} filePath
 * @returns {Promise<unknown | null>}
 */
export const readJson = async (filePath) => {
  try {
    const text = await fs.readFile(filePath, 'utf8');
    return JSON.parse(text);
  } catch (err) {
    if (err.code === 'ENOENT') return null;
    throw err;
  }
};

/**
 * Write a pending action record.
 * @param {string} home
 * @param {string} id
 * @param {object} record
 */
export const writePending = async (home, id, record) => {
  if (!isValidId(id)) throw new Error('Invalid action ID');
  const dirs = actionDirs(home);
  await atomicWriteJson(path.join(dirs.pending, `${id}.json`), record);
};

/**
 * Atomic rename: pending -> committing. Returns true if succeeded.
 * Returns false if source does not exist (already claimed or expired).
 * @param {string} home
 * @param {string} id
 * @returns {Promise<boolean>}
 */
export const claimOwnership = async (home, id) => {
  if (!isValidId(id)) throw new Error('Invalid action ID');
  const dirs = actionDirs(home);
  const src = path.join(dirs.pending, `${id}.json`);
  const dst = path.join(dirs.committing, `${id}.json`);
  try {
    await fs.rename(src, dst);
    return true;
  } catch (err) {
    if (err.code === 'ENOENT') return false;
    throw err;
  }
};

/**
 * Read the committing record for an action.
 * @param {string} home
 * @param {string} id
 * @returns {Promise<object | null>}
 */
export const readCommitting = async (home, id) => {
  if (!isValidId(id)) throw new Error('Invalid action ID');
  const dirs = actionDirs(home);
  return readJson(path.join(dirs.committing, `${id}.json`));
};

/**
 * Write the done record and remove the committing file.
 * @param {string} home
 * @param {string} id
 * @param {object} doneRecord
 */
export const finalize = async (home, id, doneRecord) => {
  if (!isValidId(id)) throw new Error('Invalid action ID');
  const dirs = actionDirs(home);
  await atomicWriteJson(path.join(dirs.done, `${id}.json`), doneRecord);
  try {
    await fs.unlink(path.join(dirs.committing, `${id}.json`));
  } catch (err) {
    if (err.code !== 'ENOENT') throw err;
  }
};

/**
 * Read a done record.
 * @param {string} home
 * @param {string} id
 * @returns {Promise<object | null>}
 */
export const readDone = async (home, id) => {
  if (!isValidId(id)) throw new Error('Invalid action ID');
  const dirs = actionDirs(home);
  return readJson(path.join(dirs.done, `${id}.json`));
};

/**
 * Startup recovery: move all committing/<id>.json -> done/<id>.json with status:"uncertain".
 * If done/<id> already exists (crash between done-write and committing-unlink), just unlink committing.
 * @param {string} home
 * @returns {Promise<string[]>} list of recovered IDs
 */
export const recoverCommitting = async (home) => {
  const dirs = actionDirs(home);
  let entries;
  try {
    entries = await fs.readdir(dirs.committing);
  } catch (err) {
    if (err.code === 'ENOENT') return [];
    throw err;
  }
  const recovered = [];
  for (const entry of entries) {
    if (!entry.endsWith('.json')) continue;
    const id = entry.slice(0, -5);
    if (!isValidId(id)) continue;

    const donePath = path.join(dirs.done, `${id}.json`);
    const committingPath = path.join(dirs.committing, `${id}.json`);

    // Check if done already exists (crash between done-write and committing-unlink)
    const existingDone = await readJson(donePath);
    if (existingDone !== null) {
      // Just clean up the stale committing file
      try { await fs.unlink(committingPath); } catch { /* ignore */ }
      recovered.push(id);
      continue;
    }

    // Read committing record to extract metadata for the uncertain done record
    const record = await readJson(committingPath);
    if (record === null) continue;

    // Write uncertain done record WITHOUT original content text.
    // Copy identifying fields needed for verify (target refs, timestamps).
    const doneRecord = {
      id,
      status: 'uncertain',
      kind: record.kind,
      target_id: record.target_id,
      content_sha256: record.content_sha256,
      nonce: record.nonce,
      created_at: record.created_at,
      committed_at: record.created_at, // best estimate; window end = recovered_at
      recovered_at: new Date().toISOString(),
    };
    // Copy LINE identifying fields (NOT the content/text itself)
    if (record.kind === 'line_reply' && record.target) {
      doneRecord.chat_ref = record.target.chat_ref;
      doneRecord.chat_name = record.target.chat_name;
      doneRecord.chat_type = record.target.chat_type;
    }
    // Copy calendar identifying fields
    if (record.kind === 'calendar_event' && record.target) {
      doneRecord.start_iso = record.target.start_iso;
      doneRecord.end_iso = record.target.end_iso;
      doneRecord.transaction_id = record.nonce;
      if (record.target.subject) {
        const { createHash } = await import('node:crypto');
        doneRecord.subject_sha256 = createHash('sha256').update(record.target.subject, 'utf8').digest('hex');
      }
    }
    await atomicWriteJson(donePath, doneRecord);
    try { await fs.unlink(committingPath); } catch { /* ignore */ }
    recovered.push(id);
  }
  return recovered;
};

/**
 * Read a pending action record.
 * @param {string} home
 * @param {string} id
 * @returns {Promise<object | null>}
 */
export const readPending = async (home, id) => {
  if (!isValidId(id)) throw new Error('Invalid action ID');
  const dirs = actionDirs(home);
  return readJson(path.join(dirs.pending, `${id}.json`));
};
