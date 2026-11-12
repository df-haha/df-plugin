"use strict";

// 金鑰外洩與不可信回應的防護：端點驗證、錯誤訊息不帶金鑰、回應格式檢查、清控制字元。
// 一律用假金鑰與本機假伺服器；每個 main() 呼叫都明送 env／daHome／stateDir，不碰真實環境。

const assert = require("node:assert/strict");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { main } = require("../scripts/inbox-sync.js");
const { createClient, validateEndpoint, WorkhubError } = require("../scripts/lib/workhub-client.js");

const KEY = "whk_FAKE_test_key_0123456789abcdef";
const NOW = new Date("2026-11-10T03:00:00Z");

function makeHome(rows) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "da-home-"));
  fs.writeFileSync(path.join(home, "CURRENT"), "1");
  const gen = path.join(home, "gen", "1");
  fs.mkdirSync(gen, { recursive: true });
  fs.writeFileSync(path.join(gen, "state.json"), JSON.stringify({ known_chats: [] }));
  fs.writeFileSync(path.join(gen, "ledger.jsonl"), rows.map((r) => JSON.stringify(r)).join("\n"));
  return home;
}

function row(id, overrides = {}) {
  return { id, title: `待辦 ${id}`, priority: "P1", due: null, status: "open", src_ids: ["mail:x"], created_at: "2026-11-09T08:00:00+08:00", ...overrides };
}

function countingFetch() {
  const calls = [];
  const fetchImpl = async (url, init) => {
    calls.push({ url, init });
    throw new Error("countingFetch 不應被呼叫");
  };
  return { calls, fetchImpl };
}

/** 跑 main()，不給 client，讓它用真的 createClient。 */
async function runReal(argv, { endpoint, apiKey = KEY, fetchImpl, rows = [row("T-1")] } = {}) {
  const out = [];
  const env = {};
  if (endpoint !== undefined) env.WORKHUB_INBOX_ENDPOINT = endpoint;
  if (apiKey !== undefined) env.WORKHUB_INBOX_API_KEY = apiKey;
  const code = await main(argv, {
    env,
    fetchImpl,
    daHome: makeHome(rows),
    stateDir: fs.mkdtempSync(path.join(os.tmpdir(), "wi-state-")),
    now: NOW,
    print: (s) => out.push(s),
  });
  return { code, out: out.join("\n") };
}

/** 本機假 Work Hub：handler(rpc) 回 JSON-RPC 物件；記錄每個請求的 Authorization。 */
function fakeServer(handler, host = "127.0.0.1") {
  const seen = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      const rpc = body ? JSON.parse(body) : {};
      seen.push({ auth: req.headers.authorization, tool: rpc.params && rpc.params.name });
      const reply = handler(rpc, req, res);
      if (reply === undefined) return;
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify(reply));
    });
  });
  return new Promise((resolve) => {
    server.listen(0, host, () => resolve({ server, seen, url: `http://${host}:${server.address().port}/mcp` }));
  });
}

const ok = (rpc, structuredContent) => ({ jsonrpc: "2.0", id: rpc.id, result: { structuredContent } });

function workhubLike(overrides = {}) {
  let nextId = 500;
  return (rpc) => {
    const name = rpc.params && rpc.params.name;
    if (overrides[name]) return overrides[name](rpc);
    if (name === "pm_list_personal_tasks") return ok(rpc, { items: [] });
    if (name === "pm_create_personal_task") return ok(rpc, { id: nextId++, deduplicated: false });
    if (name === "pm_complete_personal_task") return ok(rpc, { task: { id: rpc.params.arguments.task_id }, next: null, completed_children: [] });
    return { jsonrpc: "2.0", id: rpc.id, error: { code: -32601, message: "unknown" } };
  };
}

const CONTROL = /[\u0000-\u0009\u000b-\u001f\u007f​-‏‪-‮]/;

// ── A：端點驗證 ──────────────────────────────────────────

test("validateEndpoint：接受 https 與本機 http", () => {
  assert.equal(validateEndpoint("https://pm.example/mcp"), "https://pm.example/mcp");
  for (const url of ["http://127.0.0.1:8123/mcp", "http://localhost/mcp", "http://[::1]:9/mcp"]) {
    assert.doesNotThrow(() => validateEndpoint(url), url);
  }
});

test("validateEndpoint：拒絕非本機 http、其他協定、帳密網址、不是網址的字串，錯誤訊息不回印輸入", () => {
  const bad = [
    "http://pm.example/mcp",
    "http://localhost.evil.example/mcp",
    "http://127.0.0.1.evil.example/mcp",
    "ftp://pm.example/mcp",
    "file:///etc/passwd",
    `https://user:${KEY}@pm.example/mcp`,
    `https://${KEY}@pm.example/mcp`,
    KEY,
    "",
  ];
  for (const raw of bad) {
    assert.throws(() => validateEndpoint(raw), (err) => {
      assert.ok(err instanceof WorkhubError, raw);
      if (raw) assert.ok(!err.message.includes(raw), `訊息回印了輸入：${raw}`);
      assert.ok(!err.message.includes(KEY));
      return true;
    }, raw);
  }
});

test("createClient：http 非本機端點直接拒絕，一個請求都不送", async () => {
  const { calls, fetchImpl } = countingFetch();
  assert.throws(() => createClient({ endpoint: "http://pm.example/mcp", apiKey: KEY, fetchImpl }), WorkhubError);
  assert.equal(calls.length, 0);
});

test("CLI：端點是 http 非本機 → 結束碼 1、0 個請求、輸出不含金鑰", async () => {
  const { calls, fetchImpl } = countingFetch();
  const { code, out } = await runReal(["sync", "--source", "daily-agenda"], { endpoint: "http://pm.example/mcp", fetchImpl });
  assert.equal(code, 1);
  assert.equal(calls.length, 0);
  assert.ok(!out.includes(KEY));
});

test("CLI：不在允許清單的 http 主機，假伺服器收到 0 個請求", async () => {
  // 127.0.0.2 也通到本機，但不在允許清單（只放行 127.0.0.1、localhost、[::1]）；修正前這台假伺服器會收到金鑰。
  const { server, seen, url } = await fakeServer(workhubLike(), "127.0.0.2");
  try {
    const { code } = await runReal(["sync", "--source", "daily-agenda"], { endpoint: url });
    assert.equal(code, 1);
    assert.equal(seen.length, 0);
  } finally {
    server.close();
  }
});

// ── B：兩個變數填反、帳密網址、保底遮蔽 ─────────────────

test("CLI：端點變數被填成金鑰 → 結束碼 1，文字與 --json 都不含金鑰，0 個請求", async () => {
  for (const extra of [[], ["--json"]]) {
    const { calls, fetchImpl } = countingFetch();
    const { code, out } = await runReal(["sync", "--source", "daily-agenda", ...extra], { endpoint: KEY, apiKey: "https://pm.example/mcp", fetchImpl });
    assert.equal(code, 1, extra.join(" "));
    assert.equal(calls.length, 0);
    assert.ok(!out.includes(KEY), `輸出含金鑰（${extra.join(" ") || "文字"}）`);
    assert.match(out, /WORKHUB_INBOX_ENDPOINT/);
  }
});

test("CLI：網址含帳密 → 結束碼 1、0 個請求、輸出不含整串網址與金鑰", async () => {
  const raw = `https://user:${KEY}@pm.example/mcp`;
  for (const extra of [[], ["--json"]]) {
    const { calls, fetchImpl } = countingFetch();
    const { code, out } = await runReal(["sync", "--source", "daily-agenda", ...extra], { endpoint: raw, fetchImpl });
    assert.equal(code, 1);
    assert.equal(calls.length, 0);
    assert.ok(!out.includes(raw));
    assert.ok(!out.includes(KEY));
  }
});

test("createClient：金鑰含換行或空白直接拒絕，訊息不含金鑰", () => {
  const { calls, fetchImpl } = countingFetch();
  for (const apiKey of [`${KEY}\nX-Evil: 1`, `${KEY} `, `ab cd${KEY}`]) {
    assert.throws(() => createClient({ endpoint: "https://pm.example/mcp", apiKey, fetchImpl }), (err) => err instanceof WorkhubError && !err.message.includes(KEY));
  }
  assert.equal(calls.length, 0);
});

test("連線錯誤只帶錯誤代碼，不帶底層訊息", async () => {
  const leaky = Object.assign(new TypeError("fetch failed"), { cause: Object.assign(new Error(`connect ECONNREFUSED ${KEY}`), { code: "ECONNREFUSED" }) });
  const client = createClient({ endpoint: "https://pm.example/mcp", apiKey: KEY, fetchImpl: async () => { throw leaky; }, sleep: async () => {}, maxAttempts: 2 });
  await assert.rejects(client.callTool("x", {}), (err) => {
    assert.ok(err instanceof WorkhubError);
    assert.match(err.message, /ECONNREFUSED/);
    assert.ok(!err.message.includes(KEY));
    assert.ok(!err.message.includes("connect "));
    return true;
  });
});

test("保底遮蔽：伺服器錯誤文字回印金鑰，文字與 --json 輸出都不含金鑰，--json 仍是合法 JSON", async () => {
  const { server, url } = await fakeServer(workhubLike({
    pm_create_personal_task: (rpc) => ({ jsonrpc: "2.0", id: rpc.id, result: { isError: true, content: [{ type: "text", text: `bad token ${KEY}` }] } }),
  }));
  try {
    for (const extra of [[], ["--json"]]) {
      const { code, out } = await runReal(["sync", "--source", "daily-agenda", "--apply", ...extra], { endpoint: url });
      assert.equal(code, 3);
      assert.ok(!out.includes(KEY), `輸出含金鑰（${extra.join(" ") || "文字"}）`);
      if (extra.length) assert.equal(JSON.parse(out).results.failed, 1);
    }
  } finally {
    server.close();
  }
});

test("遮蔽在截斷之前：伺服器把金鑰放在 300 字截斷邊界上，客戶端錯誤訊息也不留下金鑰前段", async () => {
  // 目前 CLI 的截斷剛好更短而切掉這段，但不能靠這個巧合；客戶端自己要先遮蔽再截斷。
  for (const pad of [280, 290, 295]) {
    const padded = `${"x".repeat(pad)}${KEY}`;
    const replies = [
      { status: 200, ok: true, text: async () => JSON.stringify({ result: { isError: true, content: [{ type: "text", text: padded }] } }) },
      { status: 200, ok: true, text: async () => JSON.stringify({ error: { code: -32000, message: padded } }) },
    ];
    for (const reply of replies) {
      const client = createClient({ endpoint: "https://pm.example/mcp", apiKey: KEY, fetchImpl: async () => reply, sleep: async () => {} });
      await assert.rejects(client.callTool("x", {}), (err) => {
        assert.ok(!err.message.includes(KEY.slice(0, 8)), `pad ${pad}`);
        return true;
      });
    }
  }
});

test("保底遮蔽：讀取現況失敗時的錯誤也遮蔽金鑰", async () => {
  const { server, url } = await fakeServer((rpc) => ({ jsonrpc: "2.0", id: rpc.id, error: { code: -32000, message: `token ${KEY} rejected` } }));
  try {
    const { code, out } = await runReal(["sync", "--source", "daily-agenda", "--json"], { endpoint: url });
    assert.equal(code, 3);
    assert.ok(!out.includes(KEY));
  } finally {
    server.close();
  }
});

test("轉址不跟隨：端點回 307 指向別處，別處收不到請求，記為失敗", async () => {
  const other = await fakeServer(workhubLike());
  const first = await fakeServer((_rpc, _req, res) => {
    res.statusCode = 307;
    res.setHeader("location", other.url);
    res.end();
  });
  try {
    const { code, out } = await runReal(["sync", "--source", "daily-agenda"], { endpoint: first.url });
    assert.equal(code, 3);
    assert.equal(other.seen.length, 0);
    assert.match(out, /轉址/);
  } finally {
    first.server.close();
    other.server.close();
  }
});

test("本機假伺服器正常路徑：建立成功、結束碼 0（確認驗證沒有擋掉合法的本機 http）", async () => {
  const { server, seen, url } = await fakeServer(workhubLike());
  try {
    const { code, out } = await runReal(["sync", "--source", "daily-agenda", "--apply", "--json"], { endpoint: url });
    assert.equal(code, 0);
    assert.equal(JSON.parse(out).results.created, 1);
    assert.equal(seen[0].auth, `Bearer ${KEY}`);
  } finally {
    server.close();
  }
});

test("合法端點網址不算秘密：伺服器錯誤含端點網址時，失敗代碼照常顯示", async () => {
  let endpointUrl = "";
  const { server, url } = await fakeServer(workhubLike({
    pm_create_personal_task: (rpc) => ({ jsonrpc: "2.0", id: rpc.id, result: { isError: true, content: [{ type: "text", text: `請稍後再呼叫 ${endpointUrl}` }], structuredContent: { success: false, error: { code: "RATE_LIMITED", message: `請稍後再呼叫 ${endpointUrl}` } } } }),
  }));
  endpointUrl = url;
  try {
    const { code, out } = await runReal(["sync", "--source", "daily-agenda", "--apply"], { endpoint: url });
    assert.equal(code, 3);
    assert.match(out, /RATE_LIMITED/);
  } finally {
    server.close();
  }
});

// ── E：回 200 但不是 Work Hub ────────────────────────────

test("清單回應缺少 items → 讀取現況失敗、結束碼 3、不送任何寫入", async () => {
  const { server, seen, url } = await fakeServer((rpc) => ({ jsonrpc: "2.0", id: rpc.id, result: {} }));
  try {
    const { code, out } = await runReal(["sync", "--source", "daily-agenda", "--apply", "--json"], { endpoint: url });
    assert.equal(code, 3);
    assert.match(out, /格式/);
    assert.deepEqual(seen.filter((s) => s.tool !== "pm_list_personal_tasks"), []);
  } finally {
    server.close();
  }
});

test("建立回應缺少 id → 算失敗、結束碼 3，不報建立成功", async () => {
  const { server, url } = await fakeServer(workhubLike({ pm_create_personal_task: (rpc) => ok(rpc, {}) }));
  try {
    const { code, out } = await runReal(["sync", "--source", "daily-agenda", "--apply", "--json"], { endpoint: url });
    assert.equal(code, 3);
    const r = JSON.parse(out).results;
    assert.equal(r.created, 0);
    assert.equal(r.failed, 1);
  } finally {
    server.close();
  }
});

test("勾完成回應缺少 task → 算失敗、結束碼 3", async () => {
  const existing = { id: 7, source: "outlook", external_id: "agenda:T-9:2026-11-09T08:00:00+08:00", workflow_state: "active", completed_at: null };
  const { server, url } = await fakeServer(workhubLike({
    pm_list_personal_tasks: (rpc) => ok(rpc, { items: rpc.params.arguments.view === "all" ? [existing] : [] }),
    pm_complete_personal_task: (rpc) => ok(rpc, {}),
  }));
  try {
    const { code, out } = await runReal(["sync", "--source", "daily-agenda", "--apply", "--json"], { endpoint: url, rows: [row("T-9", { status: "done" })] });
    assert.equal(code, 3);
    const r = JSON.parse(out).results;
    assert.equal(r.completed, 0);
    assert.equal(r.failed, 1);
  } finally {
    server.close();
  }
});

// ── F：伺服器錯誤文字清控制字元 ─────────────────────────

test("伺服器錯誤文字（isError 與 JSON-RPC error）不輸出，失敗行沒有控制字元", async () => {
  const nasty = "壞\u001b[31m紅\u0007\r\n第二行‮反轉";
  const { server, url } = await fakeServer(workhubLike({
    pm_create_personal_task: (rpc) => (rpc.params.arguments.external_id.includes("T-1")
      ? { jsonrpc: "2.0", id: rpc.id, result: { isError: true, content: [{ type: "text", text: nasty }] } }
      : { jsonrpc: "2.0", id: rpc.id, error: { code: -32000, message: nasty } }),
  }));
  try {
    const { out } = await runReal(["sync", "--source", "daily-agenda", "--apply"], { endpoint: url, rows: [row("T-1"), row("T-2")] });
    const failLines = out.split("\n").filter((l) => l.startsWith("- 失敗"));
    assert.equal(failLines.length, 2);
    for (const line of failLines) {
      assert.ok(!CONTROL.test(line), JSON.stringify(line));
      assert.ok(!line.includes("紅") && !line.includes("第二行"), line);
      assert.match(line, /SERVER_ERROR_TEXT_HIDDEN|JSON-RPC -32000/);
    }
  } finally {
    server.close();
  }
});
