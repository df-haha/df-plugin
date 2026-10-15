import test from 'node:test';
import assert from 'node:assert/strict';
import { readLocalLineRecentChats, readLocalLineMessages } from '../src/extensions/line-local-reader.mjs';

// --- line-local-reader.mjs: MAIN_DATABASE_INVALID and MAIN_DATABASE_NOT_FOUND in error whitelist ---

test('readLocalLineRecentChats passes through MAIN_DATABASE_INVALID', async () => {
  const run = async () => ({ code: 2, stdout: JSON.stringify({
    ok: false, code: 'MAIN_DATABASE_INVALID',
    message: 'configured database name is invalid' }) });
  await assert.rejects(
    readLocalLineRecentChats({ days: 14 }, { runProcess: run }),
    error => error.code === 'MAIN_DATABASE_INVALID');
});

test('readLocalLineRecentChats passes through MAIN_DATABASE_NOT_FOUND', async () => {
  const run = async () => ({ code: 2, stdout: JSON.stringify({
    ok: false, code: 'MAIN_DATABASE_NOT_FOUND',
    message: 'configured database not in list' }) });
  await assert.rejects(
    readLocalLineRecentChats({ days: 14 }, { runProcess: run }),
    error => error.code === 'MAIN_DATABASE_NOT_FOUND');
});

test('readLocalLineMessages passes through MAIN_DATABASE_INVALID', async () => {
  const run = async () => ({ code: 2, stdout: JSON.stringify({
    ok: false, code: 'MAIN_DATABASE_INVALID',
    message: 'configured database name is invalid' }) });
  await assert.rejects(
    readLocalLineMessages({ chatName: 'test', dateFrom: '2026-10-01', dateTo: '2026-10-01' },
      { runProcess: run }),
    error => error.code === 'MAIN_DATABASE_INVALID');
});

test('readLocalLineMessages passes through MAIN_DATABASE_NOT_FOUND', async () => {
  const run = async () => ({ code: 2, stdout: JSON.stringify({
    ok: false, code: 'MAIN_DATABASE_NOT_FOUND',
    message: 'configured database not in list' }) });
  await assert.rejects(
    readLocalLineMessages({ chatName: 'test', dateFrom: '2026-10-01', dateTo: '2026-10-01' },
      { runProcess: run }),
    error => error.code === 'MAIN_DATABASE_NOT_FOUND');
});
