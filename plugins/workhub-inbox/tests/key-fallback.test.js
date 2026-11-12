"use strict";

// 金鑰來源後備：沒設 WORKHUB_INBOX_API_KEY 時改讀 DF_WORKHUB_API_KEY（PM 系統「AI 串接」頁教學員設的變數）。
// 一律用假金鑰、假 daily-agenda 資料與本機假伺服器；子行程只帶明列的環境變數，不繼承這台電腦的真實環境。

const assert = require("node:assert/strict");
const { execFile } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const CLI = path.join(__dirname, "..", "scripts", "inbox-sync.js");
const INBOX_KEY = "whk_FAKE_inbox_key_0123456789abcdef";
// 全大寫英數底線：伺服器把它當錯誤代碼回傳時，客戶端會原樣放進錯誤訊息，用來驗證輸出遮蔽
const DF_KEY = "DFKEY_FAKE_FALLBACK_0123456789ABCDEF";

function makeHome() {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "da-home-"));
  fs.writeFileSync(path.join(home, "CURRENT"), "1");
  const gen = path.join(home, "gen", "1");
  fs.mkdirSync(gen, { recursive: true });
  fs.writeFileSync(path.join(gen, "state.json"), JSON.stringify({ known_chats: [] }));
  const row = { id: "T-1", title: "待辦 T-1", priority: "P1", due: null, status: "open", src_ids: ["mail:x"], created_at: new Date().toISOString() };
  fs.writeFileSync(path.join(gen, "ledger.jsonl"), JSON.stringify(row));
  return home;
}

/** 假 Work Hub：記錄每個請求的 Authorization 與工具名稱；onCreate 可改建立的回應。 */
function fakeServer(onCreate) {
  const seen = [];
  let nextId = 900;
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      const rpc = JSON.parse(body);
      const name = rpc.params.name;
      seen.push({ auth: req.headers.authorization, tool: name });
      let reply;
      if (name === "pm_list_personal_tasks") reply = { jsonrpc: "2.0", id: rpc.id, result: { structuredContent: { items: [] } } };
      else if (onCreate) reply = onCreate(rpc);
      else reply = { jsonrpc: "2.0", id: rpc.id, result: { structuredContent: { id: nextId++, deduplicated: false } } };
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify(reply));
    });
  });
  return new Promise((resolve) => server.listen(0, "127.0.0.1", () => resolve({ server, seen, url: `http://127.0.0.1:${server.address().port}/mcp` })));
}

/** 只帶明列的變數：keys 是要設的金鑰變數（值為 undefined 就不設）。 */
function runCli(args, { url, keys, stateBase }) {
  const childEnv = { PATH: process.env.PATH, SYSTEMROOT: process.env.SYSTEMROOT, WORKHUB_INBOX_ENDPOINT: url, WORKHUB_INBOX_STATE_DIR: stateBase };
  for (const [name, value] of Object.entries(keys)) if (value !== undefined) childEnv[name] = value;
  return new Promise((resolve) => {
    execFile(process.execPath, [CLI, ...args], { env: childEnv, encoding: "utf8", timeout: 60_000 }, (err, stdout, stderr) => {
      resolve({ code: err ? err.code : 0, out: `${stdout}\n${stderr}` });
    });
  });
}

async function withServer(onCreate, fn) {
  const ctx = await fakeServer(onCreate);
  try {
    return await fn(ctx);
  } finally {
    ctx.server.close();
  }
}

const SYNC = (home, ...extra) => ["sync", "--source", "daily-agenda", "--da-home", home, ...extra];
const newStateBase = () => fs.mkdtempSync(path.join(os.tmpdir(), "wi-base-"));

test("只設 DF_WORKHUB_API_KEY：用它送出請求，結束碼 0", async () => {
  await withServer(null, async ({ seen, url }) => {
    const { code } = await runCli(SYNC(makeHome(), "--apply", "--json"), { url, keys: { DF_WORKHUB_API_KEY: DF_KEY }, stateBase: newStateBase() });
    assert.equal(code, 0);
    assert.ok(seen.length > 0);
    for (const s of seen) assert.equal(s.auth, `Bearer ${DF_KEY}`);
  });
});

test("兩個金鑰變數都有值且不同：以 WORKHUB_INBOX_API_KEY 為準", async () => {
  await withServer(null, async ({ seen, url }) => {
    const { code } = await runCli(SYNC(makeHome(), "--apply", "--json"), { url, keys: { WORKHUB_INBOX_API_KEY: INBOX_KEY, DF_WORKHUB_API_KEY: DF_KEY }, stateBase: newStateBase() });
    assert.equal(code, 0);
    assert.ok(seen.length > 0);
    for (const s of seen) assert.equal(s.auth, `Bearer ${INBOX_KEY}`);
  });
});

test("WORKHUB_INBOX_API_KEY 是空字串：當成沒設，改用 DF_WORKHUB_API_KEY", async () => {
  await withServer(null, async ({ seen, url }) => {
    const { code } = await runCli(SYNC(makeHome(), "--json"), { url, keys: { WORKHUB_INBOX_API_KEY: "", DF_WORKHUB_API_KEY: DF_KEY }, stateBase: newStateBase() });
    assert.equal(code, 0);
    assert.ok(seen.length > 0);
    for (const s of seen) assert.equal(s.auth, `Bearer ${DF_KEY}`);
  });
});

test("伺服器回傳的錯誤帶出 DF_WORKHUB_API_KEY 的值：不論那把金鑰有沒有被使用，文字與 --json 輸出都看不到", async () => {
  const echoKey = (rpc) => ({ jsonrpc: "2.0", id: rpc.id, result: { isError: true, content: [{ type: "text", text: DF_KEY }], structuredContent: { success: false, error: { code: DF_KEY, message: DF_KEY } } } });
  const cases = [
    { label: "只設 DF", keys: { DF_WORKHUB_API_KEY: DF_KEY } },
    { label: "兩個都設、DF 沒被使用", keys: { WORKHUB_INBOX_API_KEY: INBOX_KEY, DF_WORKHUB_API_KEY: DF_KEY } },
  ];
  for (const { label, keys } of cases) {
    for (const extra of [[], ["--json"]]) {
      await withServer(echoKey, async ({ seen, url }) => {
        const { code, out } = await runCli(SYNC(makeHome(), "--apply", ...extra), { url, keys, stateBase: newStateBase() });
        const tag = `${label} ${extra.join(" ") || "文字"}`;
        assert.equal(code, 3, tag);
        assert.ok(seen.some((s) => s.tool === "pm_create_personal_task"), `${tag}：沒送到建立`);
        assert.ok(!out.includes(DF_KEY), `${tag}：輸出含 DF_WORKHUB_API_KEY 的值`);
        assert.ok(!out.includes(INBOX_KEY), `${tag}：輸出含 WORKHUB_INBOX_API_KEY 的值`);
      });
    }
  }
});

test("兩個金鑰變數都沒設：錯誤訊息寫出兩個變數名、結束碼非 0、一個請求都不送", async () => {
  await withServer(null, async ({ seen, url }) => {
    const { code, out } = await runCli(SYNC(makeHome(), "--apply"), { url, keys: {}, stateBase: newStateBase() });
    assert.notEqual(code, 0);
    assert.match(out, /缺少 Work Hub API 金鑰/);
    assert.match(out, /WORKHUB_INBOX_API_KEY/);
    assert.match(out, /DF_WORKHUB_API_KEY/);
    assert.equal(seen.length, 0);
  });
});

test("DF_WORKHUB_API_KEY 格式錯誤：訊息指出是 DF_WORKHUB_API_KEY、不含金鑰值、不送請求", async () => {
  const bad = `${DF_KEY} tail`;
  await withServer(null, async ({ seen, url }) => {
    const { code, out } = await runCli(SYNC(makeHome()), { url, keys: { DF_WORKHUB_API_KEY: bad }, stateBase: newStateBase() });
    assert.equal(code, 1);
    assert.match(out, /API 金鑰格式不正確：DF_WORKHUB_API_KEY/);
    assert.ok(!out.includes(DF_KEY));
    assert.equal(seen.length, 0);
  });
});

test("WORKHUB_INBOX_API_KEY 格式錯誤時不改用 DF_WORKHUB_API_KEY：訊息指出 WORKHUB_INBOX_API_KEY、不送請求", async () => {
  await withServer(null, async ({ seen, url }) => {
    const { code, out } = await runCli(SYNC(makeHome()), { url, keys: { WORKHUB_INBOX_API_KEY: `${INBOX_KEY}\nX: 1`, DF_WORKHUB_API_KEY: DF_KEY }, stateBase: newStateBase() });
    assert.equal(code, 1);
    assert.match(out, /API 金鑰格式不正確：WORKHUB_INBOX_API_KEY/);
    assert.ok(!out.includes(INBOX_KEY));
    assert.ok(!out.includes(DF_KEY));
    assert.equal(seen.length, 0);
  });
});

test("同一把金鑰不論放在哪個變數，本機對照檔的範圍資料夾相同", async () => {
  await withServer(null, async ({ url }) => {
    const dirs = [];
    for (const name of ["WORKHUB_INBOX_API_KEY", "DF_WORKHUB_API_KEY"]) {
      const stateBase = newStateBase();
      const { code } = await runCli(SYNC(makeHome(), "--apply", "--json"), { url, keys: { [name]: DF_KEY }, stateBase });
      assert.equal(code, 0, name);
      const scopes = fs.readdirSync(stateBase).filter((d) => d.startsWith("scope-"));
      assert.equal(scopes.length, 1, name);
      dirs.push(scopes[0]);
    }
    assert.equal(dirs[0], dirs[1]);
  });
});
