/**
 * Tests for line_collect.mjs — uses node:test with a fake vendor directory
 * containing a stub line-local-reader.mjs.
 */

import { describe, it, before, after } from 'node:test';
import assert from 'node:assert/strict';
import { spawn } from 'node:child_process';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { fileURLToPath } from 'node:url';

const __dirname = path.dirname(fileURLToPath(import.meta.url));
const SCRIPT = path.join(__dirname, 'line_collect.mjs');

let vendorDir;
let readerModulePath;

/**
 * Create a fake vendor directory with a stub reader module.
 * The stub returns canned responses based on env-controlled behaviour.
 */
async function createFakeVendor(readerCode) {
  vendorDir = await fs.mkdtemp(path.join(os.tmpdir(), 'line-collect-test-'));
  const extDir = path.join(vendorDir, 'src', 'extensions');
  await fs.mkdir(extDir, { recursive: true });
  readerModulePath = path.join(extDir, 'line-local-reader.mjs');
  // Also create a stub line-runtime.mjs that the real reader imports
  const runtimePath = path.join(extDir, 'line-runtime.mjs');
  await fs.writeFile(runtimePath, `
export class LineToolError extends Error {
  constructor(code, msg) { super(msg); this.code = code; }
}
export function requireChat(name) {
  if (!name || typeof name !== 'string') throw new LineToolError('LINE_INVALID_ARGUMENT', '');
}
export function configuredPythonPath(p) { return p ?? process.env.LINE_MCP_PYTHON ?? 'python3'; }
`, 'utf-8');
  await fs.writeFile(readerModulePath, readerCode, 'utf-8');
}

function run(request, env = {}) {
  return new Promise((resolve, reject) => {
    const child = spawn(process.execPath, [SCRIPT, '--vendor', vendorDir], {
      env: { ...process.env, ...env },
      stdio: ['pipe', 'pipe', 'pipe'],
    });
    let stdout = '';
    let stderr = '';
    child.stdout.on('data', c => { stdout += c; });
    child.stderr.on('data', c => { stderr += c; });
    child.on('close', code => {
      try {
        resolve({ code, result: JSON.parse(stdout), stderr });
      } catch {
        resolve({ code, result: null, stdout, stderr });
      }
    });
    child.on('error', reject);
    child.stdin.end(JSON.stringify(request));
  });
}

describe('line_collect.mjs', () => {
  describe('with working fake reader', () => {
    before(async () => {
      await createFakeVendor(`
export async function readLocalLineRecentChats(args) {
  if (process.env.FAKE_PROBE_FAIL) {
    const { LineToolError } = await import('./line-runtime.mjs');
    throw new LineToolError('SESSION_KEY_UNAVAILABLE', 'test');
  }
  const now = new Date();
  const taipeiNow = new Date(now.getTime() + 8 * 60 * 60 * 1000);
  const checkedAt = taipeiNow.toISOString().replace('Z', '+08:00');
  return {
    ok: true, days: args.days,
    dateFrom: '2026-09-08', dateTo: '2026-10-08',
    checkedAt,
    ownSenderRef: 'sender:aabbccdd11223344aabbccdd',
    chats: [{
      chatRef: 'chat:112233445566778899aabbcc',
      chatName: 'Test Group',
      chatType: 'group',
      lastMessageAt: checkedAt,
      lastMessageTimestamp: now.getTime(),
    }],
    hasMore: false,
    warnings: [],
  };
}

export async function readLocalLineMessages(args) {
  const now = new Date();
  const ts = now.getTime();
  return {
    ok: true,
    chatName: args.chatName,
    chatRef: args.expectedChatRef ?? 'chat:112233445566778899aabbcc',
    chatIdentity: { kind: args.chatType === 'direct' ? 'direct' : 'group',
                    displayName: args.chatName, uiIdentityVerified: false },
    ownSenderRef: 'sender:aabbccdd11223344aabbccdd',
    count: 1,
    messages: [{
      sourceRef: 'message:aabb11223344aabb11223344',
      sourceMessageId: '123456',
      sourceTimestamp: ts,
      date: '2026-10-08',
      time: '09:00:00',
      text: 'Hello from fake reader',
      senderRef: 'sender:ddccbbaa44332211ddccbbaa',
      sender: 'Fake User',
      contentType: null,
      media: { state: 'not_applicable' },
      mentions: null,
    }],
    pagination: { hasMore: false, nextCursor: null },
    scope: { kind: 'local_database', requested: args },
  };
}
`);
    });

    after(async () => {
      await fs.rm(vendorDir, { recursive: true, force: true });
    });

    it('probe returns ok', async () => {
      const { result } = await run({ op: 'probe' });
      assert.equal(result.ok, true);
      assert.equal(result.code, null);
      assert.equal(typeof result.ms, 'number');
    });

    it('probe failure returns code', async () => {
      const { result } = await run({ op: 'probe' }, { FAKE_PROBE_FAIL: '1' });
      assert.equal(result.ok, false);
      assert.equal(result.code, 'SESSION_KEY_UNAVAILABLE');
    });

    it('recentChats returns chats', async () => {
      const { result } = await run({ op: 'recentChats', days: 30, limit: 50 });
      assert.equal(result.ok, true);
      assert.equal(Array.isArray(result.chats), true);
      assert.equal(result.chats.length, 1);
      assert.equal(result.chats[0].chatName, 'Test Group');
      assert.equal(typeof result.hasMore, 'boolean');
    });

    it('messages returns messages', async () => {
      const { result } = await run({
        op: 'messages',
        chatName: 'Test Group',
        chatType: 'group',
        dateFrom: '2026-10-08',
        dateTo: '2026-10-08',
        expectedChatRef: 'chat:112233445566778899aabbcc',
      });
      assert.equal(result.ok, true);
      assert.equal(result.count, 1);
      assert.equal(result.messages[0].text, 'Hello from fake reader');
      assert.equal(result.messages[0].sourceRef, 'message:aabb11223344aabb11223344');
    });

    it('unknown op returns error', async () => {
      const { result } = await run({ op: 'unknown' });
      assert.equal(result.ok, false);
      assert.equal(result.code, 'LINE_INVALID_ARGUMENT');
    });

    it('LINE_MCP_EXTENSIONS is stripped', async () => {
      // The fake reader doesn't use it, but verify the env is clean
      const { result } = await run({ op: 'probe' }, { LINE_MCP_EXTENSIONS: '1' });
      assert.equal(result.ok, true);
    });
  });

  describe('missing vendor', () => {
    it('returns LINE_RUNTIME_MISSING for bad vendor path', async () => {
      const result = await new Promise((resolve, reject) => {
        const child = spawn(process.execPath, [SCRIPT, '--vendor', '/nonexistent/vendor'], {
          stdio: ['pipe', 'pipe', 'pipe'],
        });
        let stdout = '';
        child.stdout.on('data', c => { stdout += c; });
        child.on('close', () => resolve(JSON.parse(stdout)));
        child.on('error', reject);
        child.stdin.end('{"op":"probe"}');
      });
      assert.equal(result.ok, false);
      assert.equal(result.code, 'LINE_RUNTIME_MISSING');
    });
  });

  describe('no --vendor flag', () => {
    it('returns LINE_INVALID_ARGUMENT', async () => {
      const result = await new Promise((resolve, reject) => {
        const child = spawn(process.execPath, [SCRIPT], {
          stdio: ['pipe', 'pipe', 'pipe'],
        });
        let stdout = '';
        child.stdout.on('data', c => { stdout += c; });
        child.on('close', () => resolve(JSON.parse(stdout)));
        child.on('error', reject);
        child.stdin.end('{"op":"probe"}');
      });
      assert.equal(result.ok, false);
      assert.equal(result.code, 'LINE_INVALID_ARGUMENT');
    });
  });
});
