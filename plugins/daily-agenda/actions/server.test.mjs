import { describe, it, beforeEach } from 'node:test';
import assert from 'node:assert/strict';
import { createServer } from './server.mjs';
import { createFakeLineAdapter, createFakeCalendarAdapter } from './lib/adapters-fake.mjs';
import fs from 'node:fs/promises';
import path from 'node:path';
import os from 'node:os';
import { spawn } from 'node:child_process';
import { sha256Raw } from './lib/canonical.mjs';
import { recoverCommitting, ensureDirs as ensureActionDirs } from './lib/store.mjs';

const tmpHome = async () => {
  const dir = await fs.mkdtemp(path.join(os.tmpdir(), 'da-test-'));
  return dir;
};

const fixedNow = () => new Date('2026-10-08T10:00:00+08:00');

const makeServer = async (opts = {}) => {
  const home = opts.home || await tmpHome();
  const lineAdapter = Object.hasOwn(opts, 'lineAdapter') ? opts.lineAdapter : createFakeLineAdapter();
  const calendarAdapter = Object.hasOwn(opts, 'calendarAdapter') ? opts.calendarAdapter : createFakeCalendarAdapter();
  const server = createServer({
    home,
    lineAdapter,
    calendarAdapter,
    now: opts.now || fixedNow,
    stderr: opts.stderr || (() => {}),
  });
  return { server, home, lineAdapter, calendarAdapter };
};

// ── Protocol tests ──

describe('MCP protocol', () => {
  it('handles initialize', async () => {
    const { server } = await makeServer();
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 1, method: 'initialize',
      params: { protocolVersion: '2024-11-05', capabilities: {} },
    }));
    assert.equal(resp.result.protocolVersion, '2024-11-05');
    assert.equal(resp.result.serverInfo.name, 'daily-agenda-actions');
  });

  it('handles ping', async () => {
    const { server } = await makeServer();
    const resp = JSON.parse(await server.handleMessage({ jsonrpc: '2.0', id: 2, method: 'ping' }));
    assert.deepEqual(resp.result, {});
  });

  it('handles tools/list with commit_action _meta', async () => {
    const { server } = await makeServer();
    const resp = JSON.parse(await server.handleMessage({ jsonrpc: '2.0', id: 3, method: 'tools/list' }));
    const tools = resp.result.tools;
    assert.equal(tools.length, 3);
    const commit = tools.find((t) => t.name === 'commit_action');
    assert.ok(commit._meta);
    assert.equal(commit._meta['anthropic/requiresUserInteraction'], true);
  });

  it('returns error for unknown method', async () => {
    const { server } = await makeServer();
    const resp = JSON.parse(await server.handleMessage({ jsonrpc: '2.0', id: 4, method: 'unknown/method' }));
    assert.ok(resp.error);
    assert.equal(resp.error.code, -32601);
  });

  it('ignores notifications (no id)', async () => {
    const { server } = await makeServer();
    const resp = await server.handleMessage({ jsonrpc: '2.0', method: 'notifications/initialized' });
    assert.equal(resp, null);
  });
});

// ── prepare_action tests ──

describe('prepare_action (line_reply)', () => {
  it('creates a pending action and returns preview', async () => {
    const { server, home } = await makeServer();
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 10, method: 'tools/call',
      params: {
        name: 'prepare_action',
        arguments: {
          kind: 'line_reply',
          target: { chat_ref: 'chat:aabbccddee112233aabbccdd', chat_name: '測試群組', chat_type: 'group' },
          content: '收到，明天下午兩點開會',
        },
      },
    }));
    const result = JSON.parse(resp.result.content[0].text);
    assert.ok(result.id);
    assert.match(result.id, /^[0-9a-f]{32}$/);
    assert.equal(result.target_display, 'LINE 群組「測試群組」 (chat:aabbccddee112233aabbccdd)');
    assert.equal(result.content_display, '收到，明天下午兩點開會');
    assert.ok(result.expires_at);

    // Verify pending file exists
    const pending = JSON.parse(await fs.readFile(
      path.join(home, 'actions', 'pending', `${result.id}.json`), 'utf8'));
    assert.equal(pending.kind, 'line_reply');
    assert.equal(pending.content, '收到，明天下午兩點開會');
    assert.ok(pending.payload_sha256);
    assert.ok(pending.nonce);
  });

  it('rejects invalid chat_ref', async () => {
    const { server } = await makeServer();
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 11, method: 'tools/call',
      params: {
        name: 'prepare_action',
        arguments: {
          kind: 'line_reply',
          target: { chat_ref: 'bad', chat_name: 'test', chat_type: 'direct' },
          content: 'hello',
        },
      },
    }));
    assert.equal(resp.result.isError, true);
    assert.ok(JSON.parse(resp.result.content[0].text).error === 'INVALID_CHAT_REF');
  });

  it('sanitizes control characters in content', async () => {
    const { server, home } = await makeServer();
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 12, method: 'tools/call',
      params: {
        name: 'prepare_action',
        arguments: {
          kind: 'line_reply',
          target: { chat_ref: 'chat:aabbccddee112233aabbccdd', chat_name: 'test', chat_type: 'direct' },
          content: 'hello\x00world\x01foo\nbar\ttab',
        },
      },
    }));
    const result = JSON.parse(resp.result.content[0].text);
    const pending = JSON.parse(await fs.readFile(
      path.join(home, 'actions', 'pending', `${result.id}.json`), 'utf8'));
    assert.equal(pending.content, 'helloworldfoo\nbar\ttab');
  });

  it('rejects content exceeding max length', async () => {
    const { server } = await makeServer();
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 13, method: 'tools/call',
      params: {
        name: 'prepare_action',
        arguments: {
          kind: 'line_reply',
          target: { chat_ref: 'chat:aabbccddee112233aabbccdd', chat_name: 'test', chat_type: 'direct' },
          content: 'x'.repeat(10001),
        },
      },
    }));
    assert.equal(resp.result.isError, true);
  });
});

// ── F7/Item6: name resolution from CURRENT + state.json ──

describe('prepare_action name resolution (F7)', () => {
  it('resolves chat_name from known_chats via CURRENT file', async () => {
    const home = await tmpHome();
    // Plant CURRENT=1 and gen/1/state.json
    await fs.writeFile(path.join(home, 'CURRENT'), '1\n');
    await fs.mkdir(path.join(home, 'gen', '1'), { recursive: true });
    await fs.writeFile(path.join(home, 'gen', '1', 'state.json'), JSON.stringify({
      known_chats: [
        { ref: 'chat:aabbccddee112233aabbccdd', name: '真正的群組名稱' },
      ],
    }));

    const { server } = await makeServer({ home });
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 50, method: 'tools/call',
      params: {
        name: 'prepare_action',
        arguments: {
          kind: 'line_reply',
          target: {
            chat_ref: 'chat:aabbccddee112233aabbccdd',
            chat_name: '攻擊者假名', // model-supplied wrong name
            chat_type: 'group',
          },
          content: '測試訊息',
        },
      },
    }));
    const result = JSON.parse(resp.result.content[0].text);
    // Name should be resolved from state, not model-supplied
    assert.ok(result.target_display.includes('真正的群組名稱'), `got: ${result.target_display}`);
    assert.ok(!result.target_display.includes('攻擊者假名'), `should not contain attacker name`);
    assert.ok(result.target_display.includes('chat:aabbccddee112233aabbccdd'), `should show ref`);
  });

  it('uses fallback name for valid unknown ref', async () => {
    const home = await tmpHome();
    await fs.writeFile(path.join(home, 'CURRENT'), '1\n');
    await fs.mkdir(path.join(home, 'gen', '1'), { recursive: true });
    await fs.writeFile(path.join(home, 'gen', '1', 'state.json'), JSON.stringify({
      known_chats: [
        { ref: 'chat:000000000000000000000000', name: 'Only Known Chat' },
      ],
    }));

    const { server } = await makeServer({ home });
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 51, method: 'tools/call',
      params: {
        name: 'prepare_action',
        arguments: {
          kind: 'line_reply',
          target: {
            chat_ref: 'chat:ffffffffffffffffffffffff', // valid 24 hex, not in known_chats
            chat_name: 'Fallback Name',
            chat_type: 'direct',
          },
          content: '測試',
        },
      },
    }));
    const result = JSON.parse(resp.result.content[0].text);
    // resolveKnownChatName returns null for unknown ref → fallback to model name
    assert.ok(result.target_display.includes('Fallback Name'), `got: ${result.target_display}`);
  });
});

describe('prepare_action (calendar_event)', () => {
  it('creates a pending calendar event', async () => {
    const { server, home } = await makeServer();
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 20, method: 'tools/call',
      params: {
        name: 'prepare_action',
        arguments: {
          kind: 'calendar_event',
          target: {
            start_iso: '2026-10-09T14:00:00+08:00',
            end_iso: '2026-10-09T15:00:00+08:00',
            subject: '週會',
            location: '會議室A',
          },
        },
      },
    }));
    const result = JSON.parse(resp.result.content[0].text);
    assert.ok(result.id);
    assert.equal(result.target_display, '行事曆事件');
    assert.ok(result.content_display.includes('週會'));
    assert.ok(result.content_display.includes('會議室A'));
  });

  it('returns duplicate_candidates when similar event exists (Q-1)', async () => {
    const calendarAdapter = createFakeCalendarAdapter({
      findSimilarResult: [
        { id: 'ev-1', start: '2026-10-09T14:00:00+08:00', end: '2026-10-09T15:00:00+08:00', subject: '週會' },
      ],
    });
    const { server } = await makeServer({ calendarAdapter });
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 21, method: 'tools/call',
      params: {
        name: 'prepare_action',
        arguments: {
          kind: 'calendar_event',
          target: {
            start_iso: '2026-10-09T14:00:00+08:00',
            end_iso: '2026-10-09T15:00:00+08:00',
            subject: '週會',
          },
        },
      },
    }));
    const result = JSON.parse(resp.result.content[0].text);
    assert.ok(result.duplicate_candidates);
    assert.equal(result.duplicate_candidates.length, 1);
  });

  it('skips duplicate check with allow_duplicate (SC-5)', async () => {
    const calendarAdapter = createFakeCalendarAdapter({
      findSimilarResult: [
        { id: 'ev-1', start: '2026-10-09T14:00:00+08:00', end: '2026-10-09T15:00:00+08:00', subject: '週會' },
      ],
    });
    const { server } = await makeServer({ calendarAdapter });
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 22, method: 'tools/call',
      params: {
        name: 'prepare_action',
        arguments: {
          kind: 'calendar_event',
          target: {
            start_iso: '2026-10-09T14:00:00+08:00',
            end_iso: '2026-10-09T15:00:00+08:00',
            subject: '週會',
          },
          allow_duplicate: true,
        },
      },
    }));
    const result = JSON.parse(resp.result.content[0].text);
    assert.ok(result.id); // created, not blocked
    assert.ok(!result.duplicate_candidates);
  });
});

// ── commit_action tests (SC-4) ──

describe('commit_action', () => {
  const prepareAndGetId = async (server) => {
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 100, method: 'tools/call',
      params: {
        name: 'prepare_action',
        arguments: {
          kind: 'line_reply',
          target: { chat_ref: 'chat:aabbccddee112233aabbccdd', chat_name: '測試', chat_type: 'direct' },
          content: '好的',
        },
      },
    }));
    return JSON.parse(resp.result.content[0].text);
  };

  it('sends successfully with matching display fields', async () => {
    const { server, lineAdapter } = await makeServer();
    const prepared = await prepareAndGetId(server);

    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 101, method: 'tools/call',
      params: {
        name: 'commit_action',
        arguments: {
          id: prepared.id,
          target_display: prepared.target_display,
          content_display: prepared.content_display,
        },
      },
    }));
    const result = JSON.parse(resp.result.content[0].text);
    assert.equal(result.status, 'sent');
    assert.equal(lineAdapter.calls.length, 1);
    assert.equal(lineAdapter.calls[0].method, 'send');
  });

  it('rejects expired action (SC-4)', async () => {
    let time = new Date('2026-10-08T10:00:00+08:00');
    const { server } = await makeServer({ now: () => time });
    const prepared = await prepareAndGetId(server);

    // Advance time past expiry
    time = new Date('2026-10-08T10:11:00+08:00');
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 102, method: 'tools/call',
      params: {
        name: 'commit_action',
        arguments: {
          id: prepared.id,
          target_display: prepared.target_display,
          content_display: prepared.content_display,
        },
      },
    }));
    assert.equal(resp.result.isError, true);
    assert.ok(JSON.parse(resp.result.content[0].text).error === 'EXPIRED');
  });

  it('rejects replay (SC-4)', async () => {
    const { server } = await makeServer();
    const prepared = await prepareAndGetId(server);

    // First commit succeeds
    await server.handleMessage({
      jsonrpc: '2.0', id: 103, method: 'tools/call',
      params: {
        name: 'commit_action',
        arguments: {
          id: prepared.id,
          target_display: prepared.target_display,
          content_display: prepared.content_display,
        },
      },
    });

    // Second commit returns already_processed
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 104, method: 'tools/call',
      params: {
        name: 'commit_action',
        arguments: {
          id: prepared.id,
          target_display: prepared.target_display,
          content_display: prepared.content_display,
        },
      },
    }));
    const result = JSON.parse(resp.result.content[0].text);
    assert.equal(result.status, 'already_processed');
  });

  it('rejects content_display mismatch (SC-4)', async () => {
    const { server } = await makeServer();
    const prepared = await prepareAndGetId(server);

    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 105, method: 'tools/call',
      params: {
        name: 'commit_action',
        arguments: {
          id: prepared.id,
          target_display: prepared.target_display,
          content_display: 'MODIFIED CONTENT',
        },
      },
    }));
    assert.equal(resp.result.isError, true);
    assert.ok(JSON.parse(resp.result.content[0].text).error === 'CONTENT_MISMATCH');
  });

  it('rejects target_display mismatch (SC-4)', async () => {
    const { server } = await makeServer();
    const prepared = await prepareAndGetId(server);

    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 106, method: 'tools/call',
      params: {
        name: 'commit_action',
        arguments: {
          id: prepared.id,
          target_display: 'WRONG TARGET',
          content_display: prepared.content_display,
        },
      },
    }));
    assert.equal(resp.result.isError, true);
    assert.ok(JSON.parse(resp.result.content[0].text).error === 'TARGET_MISMATCH');
  });

  it('concurrent commits: only one succeeds (SC-4)', async () => {
    const home = await tmpHome();
    const lineAdapter = createFakeLineAdapter();
    // Add delay to simulate real work
    const slowAdapter = {
      ...lineAdapter,
      send: async (args) => {
        await new Promise((r) => setTimeout(r, 50));
        return lineAdapter.send(args);
      },
    };
    const server1 = createServer({ home, lineAdapter: slowAdapter, calendarAdapter: createFakeCalendarAdapter(), now: fixedNow, stderr: () => {} });
    const server2 = createServer({ home, lineAdapter: slowAdapter, calendarAdapter: createFakeCalendarAdapter(), now: fixedNow, stderr: () => {} });

    // Prepare with server1
    const resp = JSON.parse(await server1.handleMessage({
      jsonrpc: '2.0', id: 200, method: 'tools/call',
      params: {
        name: 'prepare_action',
        arguments: {
          kind: 'line_reply',
          target: { chat_ref: 'chat:aabbccddee112233aabbccdd', chat_name: '測試', chat_type: 'direct' },
          content: 'concurrent test',
        },
      },
    }));
    const prepared = JSON.parse(resp.result.content[0].text);

    // Both try to commit simultaneously
    const [r1, r2] = await Promise.all([
      server1.handleMessage({
        jsonrpc: '2.0', id: 201, method: 'tools/call',
        params: { name: 'commit_action', arguments: { id: prepared.id, target_display: prepared.target_display, content_display: prepared.content_display } },
      }),
      server2.handleMessage({
        jsonrpc: '2.0', id: 202, method: 'tools/call',
        params: { name: 'commit_action', arguments: { id: prepared.id, target_display: prepared.target_display, content_display: prepared.content_display } },
      }),
    ]);

    const res1 = JSON.parse(JSON.parse(r1).result.content[0].text);
    const res2 = JSON.parse(JSON.parse(r2).result.content[0].text);

    // Exactly one sent, one NOT_FOUND or already_processed
    const statuses = [res1.status || res1.error, res2.status || res2.error].sort();
    assert.ok(
      statuses.includes('sent') || statuses.includes('already_processed'),
      `Expected one success, got: ${JSON.stringify(statuses)}`,
    );
    // The adapter should have been called at most once
    assert.ok(lineAdapter.calls.filter((c) => c.method === 'send').length <= 1);
  });

  it('rejects invalid id format (path traversal prevention)', async () => {
    const { server } = await makeServer();
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 110, method: 'tools/call',
      params: {
        name: 'commit_action',
        arguments: { id: '../../../etc/passwd', target_display: 'x', content_display: 'y' },
      },
    }));
    assert.equal(resp.result.isError, true);
  });
});

// ── Crash recovery tests (SC-4) ──

describe('crash recovery', () => {
  it('recovers committing actions as uncertain on startup', async () => {
    const home = await tmpHome();
    // Manually create a committing record
    const committingDir = path.join(home, 'actions', 'committing');
    const doneDir = path.join(home, 'actions', 'done');
    const pendingDir = path.join(home, 'actions', 'pending');
    await fs.mkdir(committingDir, { recursive: true });
    await fs.mkdir(doneDir, { recursive: true });
    await fs.mkdir(pendingDir, { recursive: true });
    const id = 'aabbccddee112233aabbccddee112233';
    await fs.writeFile(path.join(committingDir, `${id}.json`), JSON.stringify({
      id, kind: 'line_reply', target_id: 'chat:abc', content_sha256: 'xxx',
      nonce: 'nnn', created_at: '2026-10-08T10:00:00+08:00',
      content: 'should not appear in done record',
    }));

    // Run recovery (in real use, main() does this before creating server)
    await recoverCommitting(home);

    // Create server with no LINE adapter so verify cannot resolve the status
    const { server } = await makeServer({ home, lineAdapter: null });
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 300, method: 'tools/call',
      params: { name: 'action_status', arguments: { id } },
    }));
    const result = JSON.parse(resp.result.content[0].text);
    assert.equal(result.status, 'uncertain');

    // Verify done record does NOT contain original content
    const done = JSON.parse(await fs.readFile(path.join(doneDir, `${id}.json`), 'utf8'));
    assert.ok(!JSON.stringify(done).includes('should not appear in done record'));
  });

  it('action_status for uncertain calls verify but never resends', async () => {
    const home = await tmpHome();
    const doneDir = path.join(home, 'actions', 'done');
    await fs.mkdir(doneDir, { recursive: true });
    const id = 'aabbccddee112233aabbccddee112234';
    await fs.writeFile(path.join(doneDir, `${id}.json`), JSON.stringify({
      id, status: 'uncertain', kind: 'line_reply',
      chat_ref: 'chat:aabbccddee112233aabbccdd', chat_name: '測試', chat_type: 'direct',
      content_sha256: 'xxx', nonce: 'nnn', created_at: '2026-10-08T10:00:00+08:00',
      committed_at: '2026-10-08T10:01:00+08:00',
    }));

    const lineAdapter = createFakeLineAdapter({ verifyResult: { found: true } });
    const { server } = await makeServer({ home, lineAdapter });

    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 301, method: 'tools/call',
      params: { name: 'action_status', arguments: { id } },
    }));
    const result = JSON.parse(resp.result.content[0].text);
    assert.equal(result.status, 'sent');
    // Verify was called but send was NOT called
    assert.equal(lineAdapter.calls.filter((c) => c.method === 'verify').length, 1);
    assert.equal(lineAdapter.calls.filter((c) => c.method === 'send').length, 0);
  });

  it('cleanup: crash between done-write and committing-unlink', async () => {
    const home = await tmpHome();
    const committingDir = path.join(home, 'actions', 'committing');
    const doneDir = path.join(home, 'actions', 'done');
    const pendingDir = path.join(home, 'actions', 'pending');
    await fs.mkdir(committingDir, { recursive: true });
    await fs.mkdir(doneDir, { recursive: true });
    await fs.mkdir(pendingDir, { recursive: true });
    const id = 'aabbccddee112233aabbccddee112235';
    // Both committing and done exist (crash between write-done and unlink-committing)
    await fs.writeFile(path.join(committingDir, `${id}.json`), JSON.stringify({ id, kind: 'line_reply' }));
    await fs.writeFile(path.join(doneDir, `${id}.json`), JSON.stringify({ id, status: 'sent', kind: 'line_reply' }));

    // Run recovery (in real use, main() does this)
    await recoverCommitting(home);

    // committing should be cleaned up
    const files = await fs.readdir(committingDir);
    assert.equal(files.length, 0);
    // done should still exist with original status preserved
    const done = JSON.parse(await fs.readFile(path.join(doneDir, `${id}.json`), 'utf8'));
    assert.equal(done.status, 'sent');
  });
});

// ── Done record must not contain original text ──

describe('done record privacy', () => {
  it('done record does not contain original message text', async () => {
    const { server, home } = await makeServer();
    const secretMessage = 'this_is_secret_client_content_42';
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 400, method: 'tools/call',
      params: {
        name: 'prepare_action',
        arguments: {
          kind: 'line_reply',
          target: { chat_ref: 'chat:aabbccddee112233aabbccdd', chat_name: 'test', chat_type: 'direct' },
          content: secretMessage,
        },
      },
    }));
    const prepared = JSON.parse(resp.result.content[0].text);

    await server.handleMessage({
      jsonrpc: '2.0', id: 401, method: 'tools/call',
      params: {
        name: 'commit_action',
        arguments: { id: prepared.id, target_display: prepared.target_display, content_display: prepared.content_display },
      },
    });

    const doneContent = await fs.readFile(
      path.join(home, 'actions', 'done', `${prepared.id}.json`), 'utf8');
    assert.ok(!doneContent.includes(secretMessage), 'Done record must not contain the original message text');
    // But content hash should be present
    const done = JSON.parse(doneContent);
    assert.ok(done.content_sha256);
    assert.equal(done.content_sha256, sha256Raw(secretMessage));
  });
});

// ── Receipt whitelist (no original content leaks) ──

describe('receipt whitelist', () => {
  it('done record does not contain adapter-returned message text', async () => {
    const secretContent = 'super_secret_message_text_12345';
    const lineAdapter = createFakeLineAdapter({
      sendResult: {
        status: 'RECORDED_LOCAL',
        sourceRef: 'message:aabbccddee112233aabbccdd',
        operationId: 'op-1',
        // Upstream can include message in return value
        message: secretContent,
        chatName: 'leaked-name',
      },
    });
    const { server, home } = await makeServer({ lineAdapter });

    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 550, method: 'tools/call',
      params: {
        name: 'prepare_action',
        arguments: {
          kind: 'line_reply',
          target: { chat_ref: 'chat:aabbccddee112233aabbccdd', chat_name: 'test', chat_type: 'direct' },
          content: secretContent,
        },
      },
    }));
    const prepared = JSON.parse(resp.result.content[0].text);

    await server.handleMessage({
      jsonrpc: '2.0', id: 551, method: 'tools/call',
      params: {
        name: 'commit_action',
        arguments: { id: prepared.id, target_display: prepared.target_display, content_display: prepared.content_display },
      },
    });

    const doneContent = await fs.readFile(
      path.join(home, 'actions', 'done', `${prepared.id}.json`), 'utf8');
    assert.ok(!doneContent.includes(secretContent), 'Done record must not contain the original message text from adapter');
    assert.ok(!doneContent.includes('leaked-name'), 'Done record must not contain leaked chatName from adapter');
    const done = JSON.parse(doneContent);
    assert.ok(done.receipt);
    assert.equal(done.receipt.status, 'RECORDED_LOCAL');
    assert.ok(!done.receipt.message); // whitelisted out
    assert.ok(!done.receipt.chatName); // whitelisted out
  });
});

// ── SC-5: transaction_id reuse on retry ──

describe('SC-5: calendar transaction_id', () => {
  it('calendar create uses the nonce as transactionId', async () => {
    const calendarAdapter = createFakeCalendarAdapter();
    const { server, home } = await makeServer({ calendarAdapter });

    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 500, method: 'tools/call',
      params: {
        name: 'prepare_action',
        arguments: {
          kind: 'calendar_event',
          target: { start_iso: '2026-10-09T14:00:00+08:00', end_iso: '2026-10-09T15:00:00+08:00', subject: '會議' },
        },
      },
    }));
    const prepared = JSON.parse(resp.result.content[0].text);

    await server.handleMessage({
      jsonrpc: '2.0', id: 501, method: 'tools/call',
      params: {
        name: 'commit_action',
        arguments: { id: prepared.id, target_display: prepared.target_display, content_display: prepared.content_display },
      },
    });

    // Read the pending to get the nonce
    const pendingFiles = await fs.readdir(path.join(home, 'actions', 'pending')).catch(() => []);
    // Pending was moved, check the adapter call
    assert.equal(calendarAdapter.calls.filter((c) => c.method === 'create').length, 1);
    const createCall = calendarAdapter.calls.find((c) => c.method === 'create');
    assert.ok(createCall.transactionId, 'transactionId must be passed to adapter');
    assert.match(createCall.transactionId, /^[0-9a-f]{32}$/);
  });
});

// ── ID validation (path traversal prevention) ──

describe('ID validation', () => {
  it('rejects path traversal in prepare response id', async () => {
    const { server } = await makeServer();
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 600, method: 'tools/call',
      params: {
        name: 'action_status',
        arguments: { id: '../../etc/passwd' },
      },
    }));
    assert.equal(resp.result.isError, true);
  });

  it('rejects non-hex characters', async () => {
    const { server } = await makeServer();
    const resp = JSON.parse(await server.handleMessage({
      jsonrpc: '2.0', id: 601, method: 'tools/call',
      params: {
        name: 'commit_action',
        arguments: { id: 'gggggggggggggggggggggggggggggggg', target_display: 'x', content_display: 'y' },
      },
    }));
    assert.equal(resp.result.isError, true);
  });
});

// ── stdio end-to-end test ──

describe('stdio end-to-end', () => {
  it('runs a full prepare -> commit cycle via stdio', async () => {
    const home = await tmpHome();
    const serverPath = path.resolve(
      path.dirname(new URL(import.meta.url).pathname), 'server.mjs',
    );
    const child = spawn(process.execPath, [serverPath], {
      env: { ...process.env, DAILY_AGENDA_HOME: home, DAILY_AGENDA_ACTIONS_TEST_ADAPTER: 'fake' },
      stdio: ['pipe', 'pipe', 'pipe'],
    });

    const sendMsg = (msg) => child.stdin.write(JSON.stringify(msg) + '\n');

    // Collect responses via promise
    const responses = [];
    let commitSent = false;

    await new Promise((resolve, reject) => {
      let buffer = '';
      const timeout = setTimeout(() => {
        child.kill('SIGKILL');
        reject(new Error(`stdio test timeout after ${responses.length} responses`));
      }, 8000);

      child.stdout.on('data', (chunk) => {
        buffer += chunk.toString();
        const lines = buffer.split('\n');
        buffer = lines.pop();
        for (const line of lines) {
          if (!line.trim()) continue;
          try { responses.push(JSON.parse(line)); } catch { continue; }

          // After we get 3 responses (init, tools/list, prepare), send commit
          if (responses.length === 3 && !commitSent) {
            commitSent = true;
            const prepareResp = responses[2];
            const prepared = JSON.parse(prepareResp.result.content[0].text);
            sendMsg({ jsonrpc: '2.0', id: 4, method: 'tools/call', params: {
              name: 'commit_action',
              arguments: { id: prepared.id, target_display: prepared.target_display, content_display: prepared.content_display },
            }});
          }

          // After we get 4 responses, we're done
          if (responses.length >= 4) {
            clearTimeout(timeout);
            child.stdin.end();
            child.kill('SIGTERM');
            resolve();
          }
        }
      });

      child.stderr.on('data', () => {}); // consume stderr
      child.on('error', (err) => { clearTimeout(timeout); reject(err); });

      // Send protocol messages
      sendMsg({ jsonrpc: '2.0', id: 1, method: 'initialize', params: { protocolVersion: '2024-11-05', capabilities: {} } });
      sendMsg({ jsonrpc: '2.0', method: 'notifications/initialized' });
      sendMsg({ jsonrpc: '2.0', id: 2, method: 'tools/list' });
      sendMsg({ jsonrpc: '2.0', id: 3, method: 'tools/call', params: {
        name: 'prepare_action',
        arguments: {
          kind: 'line_reply',
          target: { chat_ref: 'chat:aabbccddee112233aabbccdd', chat_name: 'e2e', chat_type: 'direct' },
          content: 'end to end test',
        },
      }});
    });

    assert.ok(responses.length >= 4, `Expected >= 4 responses, got ${responses.length}`);
    assert.equal(responses[0].result.serverInfo.name, 'daily-agenda-actions');
    assert.equal(responses[1].result.tools.length, 3);
    const prepResult = JSON.parse(responses[2].result.content[0].text);
    assert.ok(prepResult.id);
    const commitResult = JSON.parse(responses[3].result.content[0].text);
    assert.equal(commitResult.status, 'sent');
  });
});
