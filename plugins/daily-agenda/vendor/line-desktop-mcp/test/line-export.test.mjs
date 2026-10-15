import test from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import os from 'node:os';
import path from 'node:path';
import { createHash } from 'node:crypto';
import { exportLocalLineMessages, writeVerifiedExport } from '../src/extensions/line-export.mjs';

const scope = { chatName: 'Synthetic 測試😀', dateFrom: '2026-09-01', dateTo: '2026-09-02', messageLimit: 2 };
const fixture = () => ({
  ok: true, chatName: scope.chatName, count: 2,
  messages: [
    { sourceRef: 'message:' + 'a'.repeat(24), date: '2026-09-01', time: '09:00', sender: 'Alice', kind: 'message', text: '=1+1\n下一行😀', mentions: [] },
    { sourceRef: 'message:' + 'b'.repeat(24), date: '2026-09-02', time: '10:00', sender: 'Bob', contentType: 1, text: null, media: { available: false, kind: 'image' },
      senderRef: 'sender:fixture', sourceType: 1, sourceStatus: 0, sourceRevision: 2, relatedSourceRef: null, futureMetadata: 'synthetic' },
  ],
  scope: { kind: 'local_database', truncated: true, requested: { ...scope, mediaMode: 'metadata' } },
  freshness: { snapshotCapturedAt: '2026-09-02T02:00:00Z' },
  pagination: { hasMore: true, nextCursor: 'opaque-fixture-cursor' }, warnings: ['fixture warning'],
});

async function directory(t) {
  const root = await fs.mkdtemp(path.join(os.tmpdir(), 'line-local-export-'));
  t.after(() => fs.rm(root, { recursive: true, force: true }));
  return root;
}

test('local JSON export preserves full metadata, pagination, scope and verified bytes', async t => {
  const root = await directory(t);
  const outputPath = path.join(root, 'history.json');
  let calls = 0;
  const result = await exportLocalLineMessages({ ...scope, outputPath, format: 'json' }, { localReader: async args => {
    calls++;
    assert.deepEqual(args, { ...scope, mediaMode: 'metadata' });
    return fixture();
  } });
  const bytes = await fs.readFile(outputPath);
  assert.deepEqual(JSON.parse(bytes), { ...fixture(), exportFormat: 'line-local-history-v1' });
  assert.equal(calls, 1);
  assert.equal(result.verified, true);
  assert.equal(result.sha256, createHash('sha256').update(bytes).digest('hex'));
  assert.equal(result.bytes, bytes.length);
  assert.deepEqual(result.pagination, fixture().pagination);
  assert.deepEqual(result.freshness, fixture().freshness);
  assert.deepEqual(result.scope, fixture().scope);
});

test('bad scope, preview, format, paths and existing destinations refuse before local read', async t => {
  const root = await directory(t);
  const existing = path.join(root, 'existing.json');
  await fs.writeFile(existing, 'preserve');
  let calls = 0;
  const options = { localReader: async () => { calls++; return fixture(); } };
  for (const changes of [
    { dateTo: '2026-10-20' }, { chatName: '' }, { cursor: 'invalid!' },
    { mediaMode: 'preview' }, { compareWithUi: true }, { format: 'xml' },
    { outputPath: 'relative.json' }, { outputPath: path.join(root, 'wrong.txt') },
    { outputPath: path.join(root, 'missing', 'history.json') }, { outputPath: existing },
  ]) {
    await assert.rejects(exportLocalLineMessages({ ...scope, format: 'json', outputPath: path.join(root, 'out.json'), ...changes }, options));
  }
  assert.equal(calls, 0);
  assert.equal(await fs.readFile(existing, 'utf8'), 'preserve');
});

test('CSV and TXT disclose projection while retaining scope in their result', async t => {
  const root = await directory(t);
  for (const format of ['csv', 'txt']) {
    const outputPath = path.join(root, `history.${format}`);
    const result = await exportLocalLineMessages({ ...scope, outputPath, format }, { localReader: async () => fixture() });
    const text = await fs.readFile(outputPath, 'utf8');
    assert.deepEqual(result.projection.columns, ['date', 'time', 'sender', 'kind', 'text']);
    assert.ok(result.projection.omittedFields.includes('sourceRef'));
    assert.ok(result.projection.omittedFields.includes('media'));
    for (const field of ['senderRef', 'sourceType', 'sourceStatus', 'sourceRevision', 'relatedSourceRef', 'futureMetadata']) {
      assert.ok(result.projection.omittedFields.includes(field));
    }
    assert.match(result.projection.note, /kind.*blank/u);
    assert.equal(result.pagination.hasMore, true);
    assert.match(text, /下一行😀/u);
    assert.doesNotMatch(text, /\[object Object\]/u);
    if (format === 'csv') assert.ok(text.includes('"\'=1+1\n下一行😀"'));
    assert.ok(text.includes(format === 'csv' ? '"2026-09-02","10:00","Bob","",""' : '2026-09-02\t10:00\tBob\t\t'));
  }
});

test('post-create write failure preserves the file and reports uncertain output', async t => {
  const root = await directory(t);
  const outputPath = path.join(root, 'history.json');
  const fileSystem = { ...fs, async open(...args) {
    const handle = await fs.open(...args);
    return { writeFile: async () => { throw new Error('synthetic failure'); }, sync: () => handle.sync(), close: () => handle.close() };
  } };
  await assert.rejects(exportLocalLineMessages({ ...scope, outputPath, format: 'json' }, { fileSystem, localReader: async () => fixture() }), error => {
    assert.equal(error.code, 'LINE_EXPORT_UNVERIFIED');
    assert.equal(error.operationMayHaveCompleted, true);
    assert.equal(error.details.outputPathCreated, true);
    return true;
  });
  assert.equal((await fs.stat(outputPath)).isFile(), true);
});

test('exclusive create also refuses a destination created after path validation', async t => {
  const root = await directory(t);
  const outputPath = path.join(root, 'history.json');
  await assert.rejects(exportLocalLineMessages({ ...scope, outputPath, format: 'json' }, { localReader: async () => {
    await fs.writeFile(outputPath, 'other writer');
    return fixture();
  } }), { code: 'EEXIST' });
  assert.equal(await fs.readFile(outputPath, 'utf8'), 'other writer');
});

test('shared writer detects readback mismatch without deleting the created file', async t => {
  const root = await directory(t);
  const outputPath = path.join(root, 'out.txt');
  await assert.rejects(writeVerifiedExport(outputPath, 'expected', { fileSystem: { ...fs, readFile: async () => Buffer.from('different') } }), error => {
    assert.equal(error.code, 'LINE_EXPORT_VERIFY_FAILED');
    assert.equal(error.operationMayHaveCompleted, true);
    return true;
  });
  assert.equal(await fs.readFile(outputPath, 'utf8'), 'expected');
});
