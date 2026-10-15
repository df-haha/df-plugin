import test from 'node:test';
import assert from 'node:assert/strict';
import { parseLineClientStatus } from '../src/extensions/line-client-status.mjs';
import { readLocalLineRecentChats, readLocalLineMessages } from '../src/extensions/line-local-reader.mjs';

// --- line-client-status.mjs: TRUSTED_BUILDS_INVALID must pass through ---

test('parseLineClientStatus accepts TRUSTED_BUILDS_INVALID with exit 2', () => {
  const value = { ok: false, code: 'TRUSTED_BUILDS_INVALID', client: { verified: false },
    process: { state: 'not_checked' } };
  const result = parseLineClientStatus(2, JSON.stringify(value));
  assert.deepEqual(result, value);
});

test('parseLineClientStatus rejects unknown error codes with exit 2', () => {
  const value = { ok: false, code: 'SOMETHING_UNKNOWN', client: { verified: false },
    process: { state: 'not_checked' } };
  const result = parseLineClientStatus(2, JSON.stringify(value));
  assert.equal(result.code, 'LINE_CLIENT_STATUS_UNAVAILABLE');
});

// --- line-local-reader.mjs: TRUSTED_BUILDS_INVALID in error whitelist ---

test('readLocalLineRecentChats passes through TRUSTED_BUILDS_INVALID', async () => {
  const run = async () => ({ code: 2, stdout: JSON.stringify({
    ok: false, code: 'TRUSTED_BUILDS_INVALID',
    message: 'trusted builds file invalid' }) });
  await assert.rejects(
    readLocalLineRecentChats({ days: 14 }, { runProcess: run }),
    error => error.code === 'TRUSTED_BUILDS_INVALID');
});

test('readLocalLineMessages passes through TRUSTED_BUILDS_INVALID', async () => {
  const run = async () => ({ code: 2, stdout: JSON.stringify({
    ok: false, code: 'TRUSTED_BUILDS_INVALID',
    message: 'trusted builds file invalid' }) });
  await assert.rejects(
    readLocalLineMessages({ chatName: 'test', dateFrom: '2026-10-01', dateTo: '2026-10-01' },
      { runProcess: run }),
    error => error.code === 'TRUSTED_BUILDS_INVALID');
});
