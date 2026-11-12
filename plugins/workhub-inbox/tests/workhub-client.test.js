"use strict";

const assert = require("node:assert/strict");
const test = require("node:test");

const { createClient, WorkhubError } = require("../scripts/lib/workhub-client.js");

function response(status, body) {
  return { status, ok: status >= 200 && status < 300, text: async () => JSON.stringify(body) };
}

function fakeFetch(queue, calls) {
  return async (url, init) => {
    calls.push({ url, init });
    const next = queue.shift();
    if (next instanceof Error) throw next;
    return next;
  };
}

const noSleep = async () => {};

test("callTool：送 JSON-RPC tools/call、帶 Bearer，回 structuredContent", async () => {
  const calls = [];
  const client = createClient({
    endpoint: "https://pm.example/mcp",
    apiKey: "secret-key",
    fetchImpl: fakeFetch([response(200, { jsonrpc: "2.0", id: 1, result: { structuredContent: { items: [1] } } })], calls),
    sleep: noSleep,
  });
  const out = await client.callTool("pm_list_personal_tasks", { view: "all" });
  assert.deepEqual(out, { items: [1] });
  const body = JSON.parse(calls[0].init.body);
  assert.equal(body.method, "tools/call");
  assert.equal(body.params.name, "pm_list_personal_tasks");
  assert.equal(calls[0].init.headers.authorization, "Bearer secret-key");
});

test("callTool：沒有 structuredContent 時解析 content 文字", async () => {
  const client = createClient({
    endpoint: "https://pm.example/mcp", apiKey: "k",
    fetchImpl: fakeFetch([response(200, { result: { content: [{ type: "text", text: "{\"id\":3}" }] } })], []),
    sleep: noSleep,
  });
  assert.deepEqual(await client.callTool("x", {}), { id: 3 });
});

test("callTool：isError 轉成 WorkhubError 且不重試", async () => {
  const calls = [];
  const client = createClient({
    endpoint: "https://pm.example/mcp", apiKey: "k",
    fetchImpl: fakeFetch([response(200, { result: { isError: true, content: [{ type: "text", text: "標題太長" }], structuredContent: { success: false, error: { code: "VALIDATION_ERROR", message: "標題太長" } } } })], calls),
    sleep: noSleep,
  });
  await assert.rejects(client.callTool("pm_create_personal_task", {}), (err) => err instanceof WorkhubError && /VALIDATION_ERROR/.test(err.message) && !err.message.includes("標題太長"));
  assert.equal(calls.length, 1);
});

test("callTool：JSON-RPC error 轉成 WorkhubError", async () => {
  const client = createClient({
    endpoint: "https://pm.example/mcp", apiKey: "k",
    fetchImpl: fakeFetch([response(200, { error: { code: -32602, message: "bad params" } })], []),
    sleep: noSleep,
  });
  await assert.rejects(client.callTool("x", {}), WorkhubError);
});

test("callTool：503 與網路錯誤會重試，最後成功", async () => {
  const calls = [];
  const client = createClient({
    endpoint: "https://pm.example/mcp", apiKey: "k",
    fetchImpl: fakeFetch([response(503, {}), new Error("fetch failed"), response(200, { result: { structuredContent: { ok: 1 } } })], calls),
    sleep: noSleep,
  });
  assert.deepEqual(await client.callTool("x", {}), { ok: 1 });
  assert.equal(calls.length, 3);
});

test("callTool：讀回應內容時斷線也會重試（有 idempotency_key，重送安全）", async () => {
  const calls = [];
  const broken = { status: 200, ok: true, text: async () => { throw new Error("socket hang up"); } };
  const client = createClient({
    endpoint: "https://pm.example/mcp", apiKey: "k",
    fetchImpl: fakeFetch([broken, response(200, { result: { structuredContent: { ok: 2 } } })], calls),
    sleep: noSleep,
  });
  assert.deepEqual(await client.callTool("x", {}), { ok: 2 });
  assert.equal(calls.length, 2);
});

test("callTool：401 不重試，錯誤訊息不含金鑰", async () => {
  const calls = [];
  const client = createClient({
    endpoint: "https://pm.example/mcp", apiKey: "super-secret",
    fetchImpl: fakeFetch([response(401, { message: "unauthorized" })], calls),
    sleep: noSleep,
  });
  await assert.rejects(client.callTool("x", {}), (err) => err instanceof WorkhubError && err.status === 401 && !err.message.includes("super-secret"));
  assert.equal(calls.length, 1);
});

test("createClient：缺端點或金鑰直接報錯", () => {
  assert.throws(() => createClient({ endpoint: "", apiKey: "k" }), WorkhubError);
  assert.throws(() => createClient({ endpoint: "https://x", apiKey: "" }), WorkhubError);
});
