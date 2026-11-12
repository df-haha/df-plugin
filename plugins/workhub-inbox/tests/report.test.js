"use strict";

// --report：只同步某一份 daily-agenda 彙整頁（報表）列出的待辦。
// 報表對照來自 daily-agenda CURRENT generation 的 state.json：report_code（R-加 3 碼）、report_index（{"項次": "T-xxxx"}）。
// 一律用假金鑰、假 daily-agenda 資料夾與 127.0.0.1 假伺服器；子行程不繼承本機環境。

const assert = require("node:assert/strict");
const { execFile } = require("node:child_process");
const fs = require("node:fs");
const http = require("node:http");
const os = require("node:os");
const path = require("node:path");
const test = require("node:test");

const { main, parseArgs } = require("../scripts/inbox-sync.js");

const CLI = path.join(__dirname, "..", "scripts", "inbox-sync.js");
const KEY = "whk_FAKE_report_key_0123456789abcdef";
const CREATED = "2026-11-09T08:00:00+08:00";

const extId = (id, createdAt = CREATED) => `agenda:${id}:${createdAt}`;

function row(id, overrides = {}) {
  return { id, title: `待辦 ${id}`, priority: "P1", due: null, status: "open", src_ids: ["mail:x"], created_at: new Date().toISOString(), ...overrides };
}

/** 假 daily-agenda 資料夾，長得像 confirm 之後：report_index 仍列著已標完成／刪除的代碼。 */
function makeHome(rows, { reportCode = "R-7KQ", reportIndex } = {}) {
  const home = fs.mkdtempSync(path.join(os.tmpdir(), "da-home-"));
  fs.writeFileSync(path.join(home, "CURRENT"), "3\n");
  const gen = path.join(home, "gen", "3");
  fs.mkdirSync(gen, { recursive: true });
  const index = reportIndex || Object.fromEntries(rows.map((r, i) => [String(i + 1), r.id]));
  fs.writeFileSync(path.join(gen, "state.json"), JSON.stringify({ known_chats: [], report_code: reportCode, report_index: index }));
  fs.writeFileSync(path.join(gen, "ledger.jsonl"), rows.map((r) => JSON.stringify(r)).join("\n"));
  return home;
}

/** 有狀態的假 Work Hub：記得建過的待辦，重跑時清單看得到、同鍵建立回 deduplicated。 */
async function fakeServer(initial = []) {
  const tasks = initial.map((t) => ({ ...t }));
  const calls = [];
  let nextId = 500;
  const server = http.createServer((req, res) => {
    let body = "";
    req.on("data", (c) => { body += c; });
    req.on("end", () => {
      const rpc = JSON.parse(body);
      const { name, arguments: args } = rpc.params;
      calls.push({ name, args });
      const handlers = {
        pm_list_personal_tasks: () => ({ items: tasks.filter((t) => (args.view === "completed" ? t.workflow_state === "completed" : true)) }),
        pm_create_personal_task: () => {
          const found = tasks.find((t) => t.external_id === args.external_id);
          if (found) return { id: found.id, deduplicated: true };
          const task = { id: nextId++, external_id: args.external_id, source: args.source, workflow_state: "active", completed_at: null };
          tasks.push(task);
          return { id: task.id, deduplicated: false };
        },
        pm_complete_personal_task: () => {
          const task = tasks.find((t) => t.id === args.task_id);
          if (task) Object.assign(task, { workflow_state: "completed", completed_at: "2026-11-13T00:00:00Z" });
          return { task: { id: args.task_id }, next: null, completed_children: [] };
        },
      };
      res.setHeader("content-type", "application/json");
      res.end(JSON.stringify({ jsonrpc: "2.0", id: rpc.id, result: { structuredContent: handlers[name]() } }));
    });
  });
  await new Promise((resolve) => server.listen(0, "127.0.0.1", resolve));
  const writes = () => calls.filter((c) => c.name !== "pm_list_personal_tasks");
  return { server, calls, writes, url: `http://127.0.0.1:${server.address().port}/mcp` };
}

function runCli(args, url, stateBase) {
  const childEnv = { PATH: process.env.PATH, SYSTEMROOT: process.env.SYSTEMROOT, WORKHUB_INBOX_API_KEY: KEY, WORKHUB_INBOX_ENDPOINT: url, WORKHUB_INBOX_STATE_DIR: stateBase };
  return new Promise((resolve) => {
    execFile(process.execPath, [CLI, ...args], { env: childEnv, encoding: "utf8", timeout: 60_000 }, (err, stdout, stderr) => {
      resolve({ code: err ? err.code : 0, stdout, stderr });
    });
  });
}

async function withServer(initial, fn) {
  const srv = await fakeServer(initial);
  const stateBase = fs.mkdtempSync(path.join(os.tmpdir(), "wi-base-"));
  try {
    return await fn({ ...srv, cli: (args) => runCli(args, srv.url, stateBase) });
  } finally {
    srv.server.close();
  }
}

const sync = (home, ...extra) => ["sync", "--source", "daily-agenda", "--da-home", home, "--apply", "--json", ...extra];
const createdIds = (writes) => writes.filter((w) => w.name === "pm_create_personal_task").map((w) => w.args.external_id.split(":")[1]).sort();

test("parseArgs：--report 只收 R-加 3 碼大寫英數或 latest", () => {
  assert.equal(parseArgs(["sync", "--report", "R-7KQ"]).report, "R-7KQ");
  assert.equal(parseArgs(["sync", "--report", "latest"]).report, "latest");
  assert.equal(parseArgs(["sync"]).report, null);
  for (const bad of ["r-7kq", "R-7K", "R-7KQQ", "R-7K;", "latest ", "LATEST", "R-ＡＢＣ", "../R-7KQ"]) {
    assert.throws(() => parseArgs(["sync", "--report", bad]), /--report/, bad);
  }
  assert.throws(() => parseArgs(["sync", "--report"]), /--report/);
});

test("parseArgs：--report 不能和 --no-create 一起用", () => {
  assert.throws(() => parseArgs(["sync", "--report", "R-7KQ", "--no-create"]), /--no-create/);
});

test("--report：只建立報表內仍未完成的項目，報表外的未完成項目不建立", async () => {
  const rows = [row("T-1001"), row("T-1002"), row("T-2001")];
  const home = makeHome(rows, { reportIndex: { 1: "T-1001", 2: "T-1002" } });
  await withServer([], async ({ cli, writes }) => {
    const { code, stdout, stderr } = await cli(sync(home, "--report", "R-7KQ"));
    assert.equal(code, 0, stderr);
    assert.deepEqual(createdIds(writes()), ["T-1001", "T-1002"]);
    const report = JSON.parse(stdout);
    assert.equal(report.report, "R-7KQ");
    assert.equal(report.counts.notInReport, 1);
    assert.equal(report.results.created, 2);
  });
});

test("--report：報表內已標完成或刪除的，PM 對應待辦勾完成；可能已完成的不寫入", async () => {
  const doneAt = "2026-11-01T08:00:00+08:00";
  const rows = [row("T-1001"), row("T-1002", { status: "done", created_at: doneAt }), row("T-1003", { status: "dismissed", created_at: doneAt }), row("T-1004", { status: "maybe_done" })];
  const initial = ["T-1002", "T-1003"].map((id, i) => ({ id: 70 + i, external_id: extId(id, doneAt), source: "outlook", workflow_state: "active", completed_at: null }));
  await withServer(initial, async ({ cli, writes }) => {
    const { code, stdout, stderr } = await cli(sync(makeHome(rows), "--report", "R-7KQ"));
    assert.equal(code, 0, stderr);
    const w = writes();
    assert.deepEqual(w.filter((c) => c.name === "pm_complete_personal_task").map((c) => c.args.task_id).sort(), [70, 71]);
    assert.deepEqual(createdIds(w), ["T-1001"]);
    const report = JSON.parse(stdout);
    assert.equal(report.results.completed, 2);
    assert.equal(report.counts.maybeDone, 1);
  });
});

test("--report：勾完成照舊不限報表（報表外標完成的也勾）", async () => {
  const rows = [row("T-1001"), row("T-3001", { status: "done", created_at: CREATED })];
  const initial = [{ id: 90, external_id: extId("T-3001"), source: "outlook", workflow_state: "active", completed_at: null }];
  await withServer(initial, async ({ cli, writes }) => {
    const { code, stderr } = await cli(sync(makeHome(rows, { reportIndex: { 1: "T-1001" } }), "--report", "R-7KQ"));
    assert.equal(code, 0, stderr);
    assert.deepEqual(writes().filter((c) => c.name === "pm_complete_personal_task").map((c) => c.args.task_id), [90]);
  });
});

test("--report latest：用 daily-agenda 目前最新的報表", async () => {
  const rows = [row("T-1001"), row("T-2001")];
  await withServer([], async ({ cli, writes }) => {
    const { code, stdout, stderr } = await cli(sync(makeHome(rows, { reportCode: "R-ABC", reportIndex: { 1: "T-2001" } }), "--report", "latest"));
    assert.equal(code, 0, stderr);
    assert.deepEqual(createdIds(writes()), ["T-2001"]);
    assert.equal(JSON.parse(stdout).report, "R-ABC");
  });
});

test("--report：重跑不重複建立", async () => {
  const home = makeHome([row("T-1001"), row("T-1002")]);
  await withServer([], async ({ cli, writes }) => {
    const first = await cli(sync(home, "--report", "R-7KQ"));
    assert.equal(first.code, 0, first.stderr);
    const second = await cli(sync(home, "--report", "R-7KQ"));
    assert.equal(second.code, 0, second.stderr);
    assert.equal(JSON.parse(second.stdout).results.created, 0);
    assert.equal(writes().filter((c) => c.name === "pm_create_personal_task").length, 2);
  });
});

test("--report 格式錯誤：結束碼 1、不送任何請求", async () => {
  await withServer([], async ({ cli, calls }) => {
    const { code, stdout } = await cli(sync(makeHome([row("T-1001")]), "--report", "R-7kq"));
    assert.equal(code, 1);
    assert.match(stdout, /--report/);
    assert.equal(calls.length, 0);
  });
});

for (const [label, opts, requested] of [
  ["代碼不是最新一份", { reportCode: "R-7KQ" }, "R-XYZ"],
  ["daily-agenda 還沒有報表", { reportCode: null }, "R-7KQ"],
  ["latest 但還沒有報表", { reportCode: null }, "latest"],
]) {
  test(`--report 找不到報表（${label}）：結束碼 2、白話訊息、不送任何請求`, async () => {
    await withServer([], async ({ cli, calls }) => {
      const { code, stdout } = await cli(sync(makeHome([row("T-1001")], opts), "--report", requested));
      assert.equal(code, 2);
      assert.match(stdout, /報表/);
      assert.ok(!stdout.includes("待辦 T-1001"));
      assert.equal(calls.length, 0);
    });
  });
}

test("--report：report_index 裡格式不符的值不當成代碼", async () => {
  const rows = [row("T-1001"), row("T-1002")];
  const home = makeHome(rows, { reportIndex: { 1: "T-1001", 2: "T-1002; rm -rf /", 3: 42 } });
  await withServer([], async ({ cli, writes }) => {
    const { code, stderr } = await cli(sync(home, "--report", "R-7KQ"));
    assert.equal(code, 0, stderr);
    assert.deepEqual(createdIds(writes()), ["T-1001"]);
  });
});

test("--report 與 --only 取交集、--exclude 再扣掉、--priorities 照樣收窄", async () => {
  const rows = [row("T-1001"), row("T-1002"), row("T-1003", { priority: "P2" }), row("T-2001")];
  const home = makeHome(rows, { reportIndex: { 1: "T-1001", 2: "T-1002", 3: "T-1003" } });
  await withServer([], async ({ cli, writes }) => {
    const { code, stderr } = await cli(sync(home, "--report", "R-7KQ", "--only", "T-1001,T-1002,T-2001", "--exclude", "T-1002"));
    assert.equal(code, 0, stderr);
    assert.deepEqual(createdIds(writes()), ["T-1001"]);
  });
  await withServer([], async ({ cli, writes }) => {
    const { code, stderr } = await cli(sync(home, "--report", "R-7KQ", "--priorities", "P0,P1"));
    assert.equal(code, 0, stderr);
    assert.deepEqual(createdIds(writes()), ["T-1001", "T-1002"]);
  });
});

test("--report：報表內的舊項目不套 7 天新鮮度（使用者在彙整頁看過、保留了）；不帶 --report 照舊略過", async () => {
  const old = "2026-10-01T08:00:00+08:00";
  const rows = [row("T-1001", { created_at: old })];
  const fake = () => {
    const calls = [];
    return { calls, async callTool(name, args) {
      calls.push(name);
      if (name === "pm_list_personal_tasks") return { items: [] };
      return { id: 1, deduplicated: false };
    } };
  };
  const runIn = async (extra) => {
    const out = [];
    const client = fake();
    const code = await main(["sync", "--source", "daily-agenda", "--apply", "--json", ...extra], {
      client, env: {}, daHome: makeHome(rows), stateDir: fs.mkdtempSync(path.join(os.tmpdir(), "wi-state-")), now: new Date("2026-11-13T03:00:00Z"), print: (s) => out.push(s),
    });
    return { code, report: JSON.parse(out.join("\n")), client };
  };
  const withReport = await runIn(["--report", "R-7KQ"]);
  assert.equal(withReport.code, 0);
  assert.equal(withReport.report.results.created, 1);
  const without = await runIn([]);
  assert.equal(without.report.counts.stale, 1);
  assert.equal(without.client.calls.filter((n) => n === "pm_create_personal_task").length, 0);
});

test("--report：文字輸出列出報表代碼，不含待辦標題", async () => {
  const home = makeHome([row("T-1001", { title: "機密標題字串" })]);
  await withServer([], async ({ cli }) => {
    const { code, stdout, stderr } = await cli(["sync", "--source", "daily-agenda", "--da-home", home, "--apply", "--report", "R-7KQ"]);
    assert.equal(code, 0, stderr);
    assert.match(stdout, /報表 R-7KQ/);
    assert.ok(!stdout.includes("機密標題字串"));
    assert.ok(!stderr.includes("機密標題字串"));
  });
});
