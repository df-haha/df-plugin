import assert from 'node:assert/strict';
import fs from 'node:fs/promises';
import test from 'node:test';
import { Client } from '@modelcontextprotocol/sdk/client/index.js';
import { InMemoryTransport } from '@modelcontextprotocol/sdk/inMemory.js';
import { LineDesktopMCPServer } from '../src/server.js';
import { LineUi } from '../src/extensions/line-ui.mjs';
import { LineAutomation } from '../src/automation/line-automation.js';
import { LineToolError } from '../src/extensions/line-runtime.mjs';

// Captured from upstream c138ace, before adding the opt-in extension.
const legacyTools = JSON.parse(await fs.readFile(new URL('./fixtures/legacy-tools.json', import.meta.url), 'utf8'));
const body = result => JSON.parse(result.content.find(item => item.type === 'text').text);
const legacyContract = tools => tools.map(({ name, inputSchema }) => ({ name, inputSchema }));

function assertHonestDefaultDescriptions(tools, runtimePlatform) {
  assert.deepEqual(legacyContract(tools), legacyContract(legacyTools));
  for (const tool of tools) {
    if (runtimePlatform === 'darwin') {
      assert.match(tool.description, /Unavailable on macOS/u);
      assert.match(tool.description, /LINE_CHAT_VERIFICATION_UNAVAILABLE/u);
      continue;
    }
    assert.match(tool.description, /already be open/u);
    assert.match(tool.description, /LINE_MCP_CUA_DRIVER/u);
    assert.match(tool.description, /LINE_MCP_PYTHON/u);
    assert.match(tool.description, /LINE_MCP_SQLITE3MC_DLL/u);
  }
  if (runtimePlatform === 'win32') {
    const descriptions = Object.fromEntries(tools.map(tool => [tool.name, tool.description]));
    assert.match(descriptions.send_message_manual, /Stage a message draft without sending/u);
    assert.match(descriptions.send_message_auto, /Send one approved message immediately/u);
  }
}

async function connect(t, options = {}) {
  const calls = [];
  const automation = {
    async getChatHistory(...args) {
      calls.push(['history', ...args]);
      return '2026.09.10 Thursday\n10:00 *Example Sender* hello';
    },
    async sendChatMessage(...args) { calls.push(['send', ...args]); return { success: true }; },
  };
  const ui = new LineUi({ automation, runOperation: (_kind, action) => action() });
  const server = new LineDesktopMCPServer({ automation, ui, ...options });
  const client = new Client({ name: 'protocol-regression', version: '1.0.0' });
  const [clientTransport, serverTransport] = InMemoryTransport.createLinkedPair();
  t.after(async () => { await client.close(); await server.server.close(); });
  await server.server.connect(serverTransport);
  await client.connect(clientTransport);
  return { client, calls };
}

test('default Windows and macOS tools preserve legacy names, order and schemas with honest platform descriptions', async t => {
  for (const runtimePlatform of ['win32', 'darwin']) {
    const { client } = await connect(t, { extensionsEnabled: false, runtimePlatform });
    assertHonestDefaultDescriptions((await client.listTools()).tools, runtimePlatform);
  }
});

test('the Windows-only opt-in leaves macOS on five unavailable legacy contracts', async t => {
  const { client } = await connect(t, { extensionsEnabled: true, runtimePlatform: 'darwin' });
  assertHonestDefaultDescriptions((await client.listTools()).tools, 'darwin');
});

test('default history and manual send use the original handlers and response shapes', async t => {
  const { client, calls } = await connect(t, { extensionsEnabled: false, runtimePlatform: 'win32' });
  const history = body(await client.callTool({ name: 'get_line_chatroom_history_short', arguments: { chatName: 'Example Chat', date: '2026-09-10', messageLimit: 4 } }));
  assert.equal(history.chatName, 'Example Chat');
  assert.equal(history.date, '2026-09-10');
  assert.equal(history.messageLimit, 4);
  assert.match(history.history, /Example Sender/);
  assert.equal(history.messages, undefined);
  await client.callTool({ name: 'send_message_manual', arguments: { chatName: 'Example Chat', message: 'draft' } });
  assert.deepEqual(calls, [
    ['history', 'Example Chat', '2026-09-10', 4, 5],
    ['send', 'Example Chat', 'draft', false],
  ]);
});

test('opt-in exposes 33 active unique tools and validates history before any automation', async t => {
  const { client, calls } = await connect(t, { extensionsEnabled: true, runtimePlatform: 'win32' });
  const { tools } = await client.listTools();
  assert.equal(tools.length, 33);
  assert.equal(new Set(tools.map(tool => tool.name)).size, 33);
  assert.ok(tools.some(tool => tool.name === 'send_file_manual'));
  const capabilities = body(await client.callTool({ name: 'get_line_capabilities', arguments: {} }));
  assert.equal(capabilities.capabilities.length, 38);
  const chatOpen = capabilities.capabilities.find(capability => capability.id === 'chat_open');
  assert.match(chatOpen.name, /開啟或沿用/u);
  assert.match(chatOpen.limit, /checks the final exact title/u);
  assert.deepEqual(calls, []);
  const invalid = await client.callTool({ name: 'get_line_chat_messages', arguments: { chatName: 'Example Chat', date: '2026-02-30' } });
  assert.equal(invalid.isError, true);
  assert.equal(body(invalid).code, 'LINE_INVALID_ARGUMENT');
  assert.deepEqual(calls, []);
  const history = body(await client.callTool({ name: 'get_line_chat_messages', arguments: { chatName: 'Example Chat', messageLimit: 1 } }));
  assert.equal(history.count, 1);
  assert.equal(history.messages[0].text, 'hello');
  assert.equal(calls.length, 1);
});

test('missing optional CUA does not prevent metadata and never falls through to legacy sends', async t => {
  const previous = process.env.LINE_MCP_CUA_DRIVER;
  const previousPython = process.env.LINE_MCP_PYTHON;
  delete process.env.LINE_MCP_CUA_DRIVER;
  delete process.env.LINE_MCP_PYTHON;
  t.after(() => {
    if (previous === undefined) delete process.env.LINE_MCP_CUA_DRIVER; else process.env.LINE_MCP_CUA_DRIVER = previous;
    if (previousPython === undefined) delete process.env.LINE_MCP_PYTHON; else process.env.LINE_MCP_PYTHON = previousPython;
  });
  const { client, calls } = await connect(t, { extensionsEnabled: true, runtimePlatform: 'win32' });
  assert.equal(body(await client.callTool({ name: 'get_line_workflow', arguments: { workflow: 'members' } })).performedAction, false);
  const status = await client.callTool({ name: 'get_line_status', arguments: {} });
  assert.equal(status.isError, undefined);
  assert.equal(body(status).success, false);
  assert.equal(body(status).uiStatusUnavailable, true);
  assert.equal(body(status).localReader.code, 'LINE_CLIENT_STATUS_UNAVAILABLE');
  const send = await client.callTool({ name: 'send_message_auto', arguments: { chatName: 'Example Chat', message: 'not sent' } });
  assert.equal(send.isError, true);
  assert.equal(body(send).code, 'LOCAL_READER_UNAVAILABLE');
  assert.deepEqual(calls, []);
});

test('real default facade refuses unavailable chat verification over MCP before backend activity', async t => {
  const previous = process.env.LINE_MCP_CUA_DRIVER;
  const previousPython = process.env.LINE_MCP_PYTHON;
  delete process.env.LINE_MCP_CUA_DRIVER;
  delete process.env.LINE_MCP_PYTHON;
  t.after(() => {
    if (previous === undefined) delete process.env.LINE_MCP_CUA_DRIVER;
    else process.env.LINE_MCP_CUA_DRIVER = previous;
    if (previousPython === undefined) delete process.env.LINE_MCP_PYTHON;
    else process.env.LINE_MCP_PYTHON = previousPython;
  });
  for (const runtimePlatform of ['win32', 'darwin']) {
    const backendCalls = [];
    const automation = Object.create(LineAutomation.prototype);
    automation.platform = runtimePlatform;
    automation.automation = new Proxy({}, { get: (_target, name) => async () => {
      backendCalls.push(name);
      assert.fail(`Unsafe backend action: ${String(name)}`);
    } });
    // Keep the real public facade/UI routing; only replace the cross-process lock.
    automation.runOperation = (_kind, action) => action();
    const { client } = await connect(t, { automation, extensionsEnabled: false, runtimePlatform });
    assertHonestDefaultDescriptions((await client.listTools()).tools, runtimePlatform);
    for (const descriptor of legacyTools) {
      const args = descriptor.name.startsWith('send_')
        ? { chatName: 'Example Chat', message: 'must not send' }
        : { chatName: 'Example Chat' };
      const result = await client.callTool({ name: descriptor.name, arguments: args });
      assert.equal(result.isError, true, `${runtimePlatform}: ${descriptor.name}`);
      assert.equal(body(result).code, runtimePlatform === 'darwin'
        ? 'LINE_CHAT_VERIFICATION_UNAVAILABLE'
        : descriptor.name.startsWith('send_message_') ? 'LOCAL_READER_UNAVAILABLE' : 'LINE_UI_BACKEND_UNAVAILABLE',
      descriptor.name);
      assert.equal(body(result).operationMayHaveCompleted, false);
      assert.equal(body(result).history, undefined);
      assert.equal(body(result).messages, undefined);
    }
    assert.deepEqual(backendCalls, []);
  }
});

test('default MCP history and file stage refuse ambiguous local identity before UI input or legacy calls', async t => {
  for (const code of ['CHAT_AMBIGUOUS', 'GUI_IDENTITY_UNAVAILABLE']) {
    const backendCalls = [];
    const automation = Object.create(LineAutomation.prototype);
    automation.platform = 'win32';
    automation.automation = new Proxy({}, { get: (_target, name) => async () => {
      backendCalls.push(name);
      assert.fail(`Unexpected legacy action: ${String(name)}`);
    } });
    automation.runOperation = (_kind, action) => action();
    automation._verifiedUi = new LineUi({
      automation,
      runOperation: (_kind, action) => action(),
      readChatIdentity: async () => { throw new LineToolError(code, 'Synthetic identity refusal.'); },
      readNamedIdentity: async () => { throw new LineToolError(code, 'Synthetic identity refusal.'); },
      withClient: async action => action({ tools: new Set(['list_windows']), call: async name => {
        if (name === 'list_windows') return { windows: [{ app_name: 'LINE.exe', title: 'Example Chat',
          pid: 42, window_id: 99, is_on_screen: true, minimized: false }] };
        backendCalls.push(name);
        assert.fail(`Unexpected CUA input or snapshot: ${name}`);
      } }),
    });
    const { client } = await connect(t, { automation, extensionsEnabled: false, runtimePlatform: 'win32' });
    for (const descriptor of legacyTools.filter(item => !item.name.startsWith('send_message_'))) {
      const args = descriptor.name === 'send_file_manual'
        ? { chatName: 'Example Chat', filePath: 'C:\synthetic-test.txt' }
        : { chatName: 'Example Chat' };
      const result = await client.callTool({ name: descriptor.name, arguments: args });
      assert.equal(result.isError, true);
      assert.equal(body(result).code, code);
      assert.equal(body(result).operationMayHaveCompleted, false);
      assert.equal(body(result).history, undefined);
    }
    assert.deepEqual(backendCalls, []);
  }
});
