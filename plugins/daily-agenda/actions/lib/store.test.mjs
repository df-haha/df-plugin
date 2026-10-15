import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import {
  isValidId, newActionId, newNonce, ensureDirs, actionDirs,
  writePending, claimOwnership, readCommitting, finalize, readDone,
  recoverCommitting, readPending, readJson, atomicWriteJson,
} from './store.mjs';

const tmpHome = async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'da-store-test-'));
  return dir;
};

describe('isValidId', () => {
  it('accepts 32 lowercase hex', () => {
    assert.ok(isValidId('aabbccddee112233aabbccddee112233'));
  });
  it('rejects uppercase', () => {
    assert.ok(!isValidId('AABBCCDDEE112233aabbccddee112233'));
  });
  it('rejects wrong length', () => {
    assert.ok(!isValidId('aabbccdd'));
  });
  it('rejects path traversal', () => {
    assert.ok(!isValidId('../../../etc/passwd'));
  });
  it('rejects null', () => {
    assert.ok(!isValidId(null));
  });
});

describe('newActionId', () => {
  it('generates 32 hex chars', () => {
    const id = newActionId();
    assert.match(id, /^[0-9a-f]{32}$/);
  });
  it('generates unique values', () => {
    assert.notEqual(newActionId(), newActionId());
  });
});

describe('store lifecycle', () => {
  it('write pending -> claim -> read committing -> finalize -> read done', async () => {
    const home = await tmpHome();
    await ensureDirs(home);
    const id = newActionId();
    const record = { id, kind: 'test', data: 'hello' };

    await writePending(home, id, record);
    const pending = await readPending(home, id);
    assert.deepEqual(pending, record);

    const claimed = await claimOwnership(home, id);
    assert.ok(claimed);

    const committing = await readCommitting(home, id);
    assert.deepEqual(committing, record);

    // Claiming again fails (already moved)
    const claimedAgain = await claimOwnership(home, id);
    assert.ok(!claimedAgain);

    await finalize(home, id, { id, status: 'sent' });
    const done = await readDone(home, id);
    assert.equal(done.status, 'sent');

    // Committing file should be removed
    const committingAfter = await readCommitting(home, id);
    assert.equal(committingAfter, null);
  });

  it('throws on invalid id', async () => {
    const home = await tmpHome();
    await assert.rejects(() => writePending(home, 'bad-id', {}));
    await assert.rejects(() => claimOwnership(home, '../x'));
    await assert.rejects(() => readDone(home, ''));
  });
});

describe('recoverCommitting', () => {
  it('moves committing to done/uncertain', async () => {
    const home = await tmpHome();
    await ensureDirs(home);
    const id = 'aabbccddee112233aabbccddee112233';
    const dirs = actionDirs(home);
    await atomicWriteJson(path.join(dirs.committing, `${id}.json`), {
      id, kind: 'line_reply', target_id: 'chat:abc', content_sha256: 'xxx',
      nonce: 'n1', created_at: '2026-01-01T00:00:00Z',
    });

    const recovered = await recoverCommitting(home);
    assert.deepEqual(recovered, [id]);

    const done = await readDone(home, id);
    assert.equal(done.status, 'uncertain');
    assert.equal(done.kind, 'line_reply');
    // Verify no original content in uncertain record
    assert.ok(!done.content);
  });

  it('cleans up when done already exists (crash between done-write and committing-unlink)', async () => {
    const home = await tmpHome();
    await ensureDirs(home);
    const id = 'bbccddee112233aabbccddee11223344';
    const dirs = actionDirs(home);
    // Both exist
    await atomicWriteJson(path.join(dirs.committing, `${id}.json`), { id, kind: 'test' });
    await atomicWriteJson(path.join(dirs.done, `${id}.json`), { id, status: 'sent' });

    const recovered = await recoverCommitting(home);
    assert.deepEqual(recovered, [id]);

    // Committing cleaned up, done preserved
    const files = await fs.readdir(dirs.committing);
    assert.equal(files.length, 0);
    const done = await readDone(home, id);
    assert.equal(done.status, 'sent');
  });

  it('returns empty when committing dir is empty', async () => {
    const home = await tmpHome();
    await ensureDirs(home);
    const recovered = await recoverCommitting(home);
    assert.deepEqual(recovered, []);
  });
});

describe('atomicWriteJson', () => {
  it('writes valid JSON atomically', async () => {
    const home = await tmpHome();
    const filePath = path.join(home, 'test.json');
    await atomicWriteJson(filePath, { hello: 'world' });
    const data = await readJson(filePath);
    assert.deepEqual(data, { hello: 'world' });
  });
});
