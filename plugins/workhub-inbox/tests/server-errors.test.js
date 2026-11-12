"use strict";

// 伺服器錯誤文字一律不輸出（預設不印）：只輸出結構化錯誤代碼、HTTP／連線代碼或固定字串。
// 一律用假金鑰、假 daily-agenda 資料與本機假伺服器；子行程同時檢查 stdout 與 stderr。

const assert = require("node:assert/strict");
const { execFile } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const CLI = path.join(__dirname, "..", "scripts", "inbox-sync.js");
const KEY = "whk_FAKE_srverr_key_0123456789abcdef";
const TITLE = "請把報價單寄給王經理並抄送財務部門的陳小姐確認";
const MIDDLE = "寄給王經理並抄送財務";
const CHAT = "某某合作群組";
const HIDDEN = "SERVER_ERROR_TEXT_HIDDEN";

function makeHome(rows, chats = []) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "da-home-"));
  fs.writeFileSync(path.join(home, "CURRENT"), "1");
  const gen = path.join(home, "gen", "1");
  fs.mkdirSync(gen, { recursive: true });
  fs.writeFileSync(path.join(gen, "state.json"), JSON.stringify({ known_chats: chats }));
  fs.writeFileSync(path.join(gen, "ledger.jsonl"), rows.map((r) => JSON.stringify(r)).join("\n"));
  return home;
}

function row(id, overrides = {}) {
  return { id, title: `待辦 ${id}`, priority: "P1", due: null, status: "open", src_ids: ["mail:x"], created_at: "2026-11-09T08:00:00+08:00", ...overrides };
}

/** 假 Work Hub：清單正常，建立依 onCreate(rpc) 回應；記錄每個工具呼叫。 */
function fakeServer(onCreate) {
  const seen = [];
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      const rpc = JSON.parse(body);
      const name = rpc.params.name;
      seen.push(name);
      let reply;
      if (name === "pm_list_personal_tasks") reply = { jsonrpc: "2.0", id: rpc.id, result: { structuredContent: { items: [] } } };
      else reply = onCreate(rpc);
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify(reply));
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, seen, url: `http://127.0.0.1:${server.address().port}/mcp` })));
}

/** Work Hub 工具錯誤的實際形狀：HTTP 200、isError、structuredContent.error（marketing-pm-system registry.js）。 */
const toolError = (rpc, text, code) => ({
  jsonrpc: "2.0",
  id: rpc.id,
  result: { isError: true, content: [{ type: "text", text }], structuredContent: { success: false, error: { code, message: text, request_id: "r-1" } } },
});

function runCli(args, url, stateBase) {
  const childEnv = { PATH: process.env.PATH, SYSTEMROOT: process.env.SYSTEMROOT, WORKHUB_INBOX_API_KEY: KEY, WORKHUB_INBOX_ENDPOINT: url, WORKHUB_INBOX_STATE_DIR: stateBase };
  return new Promise((resolve) => {
    execFile(process.execPath, [CLI, ...args], { env: childEnv, encoding: "utf8", timeout: 60_000 }, (err, stdout, stderr) => {
      resolve({ code: err ? err.code : 0, stdout, stderr });
    });
  });
}

async function runBoth(onCreate, rows) {
  const { server, seen, url } = await fakeServer(onCreate);
  const home = makeHome(rows, [{ ref: "chat:ab12", name: CHAT }]);
  const stateBase = fs.mkdtempSync(path.join(os.tmpdir(), "wi-base-"));
  try {
    const text = await runCli(["sync", "--source", "daily-agenda", "--da-home", home, "--apply"], url, stateBase);
    const json = await runCli(["sync", "--source", "daily-agenda", "--da-home", home, "--apply", "--json"], url, stateBase);
    return { text, json, seen };
  } finally {
    server.close();
  }
}

const lineRow = () => row("T-1001", { title: TITLE, src_ids: ["line:chat:ab12:m:1"] });

const LEAK_CASES = [
  ["標題中間片段", (rpc) => toolError(rpc, `VALIDATION_ERROR：欄位「${MIDDLE}」不合法`, "VALIDATION_ERROR")],
  ["JSON 跳脫形式", (rpc) => toolError(rpc, `bad input ${JSON.stringify({ title: rpc.params.arguments.title })}`.replace(/[一-鿿]/g, (ch) => `\\u${ch.charCodeAt(0).toString(16)}`), "VALIDATION_ERROR")],
  ["description 內容", (rpc) => toolError(rpc, `description 太長：${rpc.params.arguments.description.slice(8, 14)}`, "VALIDATION_ERROR")],
  ["JSON-RPC error 文字", (rpc) => ({ jsonrpc: "2.0", id: rpc.id, error: { code: -32602, message: `參數錯誤：${rpc.params.arguments.title}` } })],
];

for (const [label, onCreate] of LEAK_CASES) {
  test(`伺服器錯誤文字回帶${label}：stdout、stderr、--json 都不含不可信內容`, async () => {
    const { text, json } = await runBoth(onCreate, [lineRow()]);
    const escapedMiddle = JSON.stringify(MIDDLE).slice(1, -1).replace(/[一-鿿]/g, (ch) => `\\u${ch.charCodeAt(0).toString(16)}`);
    for (const [name, out] of [["text stdout", text.stdout], ["text stderr", text.stderr], ["json stdout", json.stdout], ["json stderr", json.stderr]]) {
      for (const marker of [MIDDLE, CHAT, escapedMiddle, "description 太長", "參數錯誤", "不合法"]) {
        assert.ok(!out.includes(marker), `${name} 含「${marker}」`);
      }
    }
    assert.equal(text.code, 3);
    assert.equal(JSON.parse(json.stdout).results.failed, 1);
  });
}

test("有結構化代碼時只輸出代碼", async () => {
  const { text, json } = await runBoth((rpc) => toolError(rpc, `欄位錯誤 ${MIDDLE}`, "VALIDATION_ERROR"), [lineRow()]);
  assert.match(text.stdout, /- 失敗 T-1001（create）：pm_create_personal_task 失敗（VALIDATION_ERROR）/);
  assert.equal(JSON.parse(json.stdout).results.failures[0].error, "pm_create_personal_task 失敗（VALIDATION_ERROR）");
});

test("代碼格式不合法（小寫、含符號、太長、不是字串）時輸出固定字串", async () => {
  for (const code of ["validation_error", "BAD<script>", `A${"B".repeat(64)}`, 42, null]) {
    const { json } = await runBoth((rpc) => toolError(rpc, `內容 ${MIDDLE}`, code), [lineRow()]);
    const error = JSON.parse(json.stdout).results.failures[0].error;
    assert.equal(error, `pm_create_personal_task 失敗（${HIDDEN}）`, String(code));
  }
});

test("JSON-RPC error 只輸出數字代碼", async () => {
  const { json } = await runBoth((rpc) => ({ jsonrpc: "2.0", id: rpc.id, error: { code: -32602, message: "bad" } }), [lineRow()]);
  assert.equal(JSON.parse(json.stdout).results.failures[0].error, "pm_create_personal_task 失敗（JSON-RPC -32602）");
});

test("工具層 MCP_FORBIDDEN（403）：第一筆之後不再送後續寫入，結束碼 1", async () => {
  const { text, seen } = await runBoth((rpc) => toolError(rpc, "MCP 權限不足", "MCP_FORBIDDEN"), [row("T-1"), row("T-2"), row("T-3")]);
  assert.equal(text.code, 1);
  // 文字與 --json 各跑一次，各只送出 1 筆寫入
  assert.equal(seen.filter((n) => n !== "pm_list_personal_tasks").length, 2);
  assert.match(text.stdout, /中止後未執行 2/);
});

test("工具層 MCP_UNAUTHENTICATED（401）同樣立即停止", async () => {
  const { json, seen } = await runBoth((rpc) => toolError(rpc, "token 無效", "MCP_UNAUTHENTICATED"), [row("T-1"), row("T-2")]);
  assert.equal(json.code, 1);
  assert.equal(JSON.parse(json.stdout).results.aborted, true);
  assert.equal(seen.filter((n) => n !== "pm_list_personal_tasks").length, 2);
});

test("讀取現況失敗也只輸出代碼", async () => {
  const { server, url } = await fakeServer(() => ({}));
  server.removeAllListeners("request");
  server.on("request", (req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      const rpc = JSON.parse(body);
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify(toolError(rpc, `清單錯誤 ${MIDDLE}`, "WORKHUB_INVALID_REQUEST")));
    });
  });
  try {
    const out = await runCli(["sync", "--source", "daily-agenda", "--da-home", makeHome([lineRow()])], url, fs.mkdtempSync(path.join(os.tmpdir(), "wi-base-")));
    assert.equal(out.code, 3);
    assert.ok(!out.stdout.includes(MIDDLE) && !out.stderr.includes(MIDDLE));
    assert.match(out.stdout, /讀取 Work Hub 現況失敗：pm_list_personal_tasks 失敗（WORKHUB_INVALID_REQUEST）/);
  } finally {
    server.close();
  }
});
